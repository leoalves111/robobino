"""
Persistência centralizada — Supabase (PostgreSQL) com fallback local.

Todas as operações expostas como async; chamadas sync do supabase-py rodam
em asyncio.to_thread para não bloquear o loop de mercado.

Fallback: fila append-only em cache/db_pending.jsonl — sincronizada quando
a nuvem voltar.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

PENDING_FILE = Path("cache") / "db_pending.jsonl"
LOCAL_SNAPSHOT_DIR = Path("cache") / "cloud_snapshots"

TABLE_LOGS = "trading_logs"
TABLE_PERF = "strategy_performance"
TABLE_CACHE = "market_data_cache"
TABLE_STATUS = "bot_status"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    key: str
    enabled: bool
    instance_id: str
    instance_label: str
    role: str  # primary | secondary
    asset_ric: str
    timeframe_seconds: int
    peer_ttl_seconds: int
    sync_interval_seconds: float
    signal_lock_seconds: int

    @classmethod
    def from_env(cls) -> SupabaseConfig:
        instance_id = os.getenv("BOT_INSTANCE_ID", "").strip() or socket.gethostname()
        return cls(
            url=os.getenv("SUPABASE_URL", "").strip(),
            key=os.getenv("SUPABASE_KEY", "").strip(),
            enabled=os.getenv("SUPABASE_ENABLED", "false").lower() in ("1", "true", "yes"),
            instance_id=instance_id,
            instance_label=os.getenv("BOT_INSTANCE_LABEL", instance_id).strip(),
            role=os.getenv("BOT_ROLE", "primary").strip().lower(),
            asset_ric=os.getenv("ASSET_RIC", "Z-CRY/IDX").strip(),
            timeframe_seconds=int(os.getenv("TIMEFRAME_SECONDS", "300")),
            peer_ttl_seconds=int(os.getenv("BOT_PEER_TTL_SEC", "120")),
            sync_interval_seconds=float(os.getenv("SUPABASE_SYNC_INTERVAL_SEC", "30")),
            signal_lock_seconds=int(os.getenv("SIGNAL_LOCK_SECONDS", "300")),
        )

    def is_configured(self) -> bool:
        return bool(self.enabled and self.url and self.key)


@dataclass
class BotStatusRow:
    asset_ric: str
    active_instance_id: str
    active_instance_label: str | None
    instance_role: str
    order_status: str
    active_strategy_file: str | None
    market_type: str | None
    signal_lock_until: datetime | None
    last_heartbeat: datetime | None
    version: int = 1

    def is_peer_fresh(self, ttl_seconds: int) -> bool:
        if self.last_heartbeat is None:
            return False
        return (_utcnow() - self.last_heartbeat).total_seconds() < ttl_seconds

    def is_foreign_lock(self, my_instance_id: str) -> bool:
        if self.active_instance_id == my_instance_id:
            return False
        now = _utcnow()
        if self.order_status in ("OPEN", "SIGNAL_LOCK"):
            return True
        if self.signal_lock_until and self.signal_lock_until > now:
            return True
        return False


class PendingQueue:
    """Fila local JSONL — sobrevive a quedas de rede."""

    def __init__(self, path: Path = PENDING_FILE) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def enqueue(self, op: str, payload: dict[str, Any]) -> None:
        row = {
            "id": str(uuid.uuid4()),
            "op": op,
            "payload": payload,
            "created_at": _iso(_utcnow()),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def rewrite(self, remaining: list[dict[str, Any]]) -> None:
        if not remaining:
            if self.path.is_file():
                self.path.unlink()
            return
        text = "\n".join(json.dumps(r, ensure_ascii=False) for r in remaining) + "\n"
        self.path.write_text(text, encoding="utf-8")


class DatabaseManager:
    """
    Facade async para Supabase + fallback local.

    Uso típico:
        db = await DatabaseManager.create_from_env()
        await db.start()
        await db.save_trade_log(...)
        await db.close()
    """

    def __init__(self, config: SupabaseConfig) -> None:
        self.config = config
        self._client: Any = None
        self._online = False
        self._pending = PendingQueue()
        self._sync_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._local_snapshots = LOCAL_SNAPSHOT_DIR
        self._local_snapshots.mkdir(parents=True, exist_ok=True)

    @classmethod
    async def create_from_env(cls) -> DatabaseManager:
        cfg = SupabaseConfig.from_env()
        mgr = cls(cfg)
        await mgr.initialize()
        return mgr

    async def initialize(self) -> None:
        if not self.config.is_configured():
            logger.info(
                "Supabase desativado ou credenciais ausentes — modo local-only (fallback JSONL)"
            )
            return
        try:
            await self._connect()
            await self.sync_pending()
            logger.info(
                "Supabase conectado | instancia=%s (%s) | role=%s",
                self.config.instance_id,
                self.config.instance_label,
                self.config.role,
            )
        except Exception as exc:
            logger.warning("Supabase indisponível no arranque — fallback local: %s", exc)
            self._online = False

    async def _connect(self) -> None:
        def _mk() -> Any:
            from supabase import create_client

            return create_client(self.config.url, self.config.key)

        self._client = await asyncio.to_thread(_mk)
        # ping leve
        await self._run(lambda: self._client.table(TABLE_STATUS).select("asset_ric").limit(1).execute())
        self._online = True

    async def _run(self, fn: Callable[[], Any]) -> Any:
        return await asyncio.to_thread(fn)

    @property
    def is_online(self) -> bool:
        return self._online and self._client is not None

    async def start(self) -> None:
        if self._sync_task is None:
            self._sync_task = asyncio.create_task(self._sync_loop(), name="supabase_sync")

    async def close(self) -> None:
        self._stop.set()
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        await self.sync_pending()

    async def _sync_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(self.config.sync_interval_seconds)
                if not self._online:
                    await self._connect()
                await self.sync_pending()
                await self.register_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Sync loop: %s", exc)
                self._online = False

    # ------------------------------------------------------------------ #
    # Fallback + replay
    # ------------------------------------------------------------------ #

    def _save_local_snapshot(self, name: str, data: dict[str, Any]) -> None:
        path = self._local_snapshots / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _execute_or_queue(self, op: str, payload: dict[str, Any], executor: Callable[[], Any]) -> bool:
        if not self.config.is_configured():
            self._pending.enqueue(op, payload)
            return False
        try:
            if not self._online:
                await self._connect()
            await self._run(executor)
            return True
        except Exception as exc:
            logger.warning("Supabase %s falhou — enfileirando local: %s", op, exc)
            self._online = False
            self._pending.enqueue(op, payload)
            self._save_local_snapshot(op, payload)
            return False

    async def sync_pending(self) -> int:
        if not self.config.is_configured():
            return 0
        rows = self._pending.read_all()
        if not rows:
            return 0
        if not self._online:
            try:
                await self._connect()
            except Exception:
                return 0

        remaining: list[dict[str, Any]] = []
        synced = 0
        dispatch = {
            "save_trade_log": self._sync_save_trade_log,
            "update_strategy_state": self._sync_update_strategy_state,
            "upsert_market_cache": self._sync_upsert_market_cache,
            "upsert_bot_status": self._sync_upsert_bot_status,
        }
        for row in rows:
            op = row.get("op", "")
            payload = row.get("payload") or {}
            handler = dispatch.get(op)
            if handler is None:
                remaining.append(row)
                continue
            try:
                await self._run(lambda h=handler, p=payload: h(p))
                synced += 1
            except Exception as exc:
                logger.debug("Replay pendente falhou (%s): %s", op, exc)
                remaining.append(row)
                self._online = False
                break

        self._pending.rewrite(remaining)
        if synced:
            logger.info("Supabase: %d operação(ões) pendente(s) sincronizada(s)", synced)
        return synced

    # ------------------------------------------------------------------ #
    # trading_logs
    # ------------------------------------------------------------------ #

    async def save_trade_log(
        self,
        *,
        signal: str | None = None,
        confidence: int | None = None,
        price: float | None = None,
        reason: str = "",
        strategy_file: str = "",
        market_type: str = "",
        event_type: str = "SIGNAL",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        payload = {
            "instance_id": self.config.instance_id,
            "instance_label": self.config.instance_label,
            "asset_ric": self.config.asset_ric,
            "event_type": event_type,
            "signal": signal,
            "confidence": confidence,
            "price": price,
            "reason": reason[:2000] if reason else None,
            "strategy_file": strategy_file or None,
            "market_type": market_type or None,
            "metadata": metadata or {},
        }
        ok = await self._execute_or_queue(
            "save_trade_log",
            payload,
            lambda: self._sync_save_trade_log(payload),
        )
        if signal and strategy_file:
            await self.update_strategy_state(strategy_file, market_type=market_type)
        return ok

    def _sync_save_trade_log(self, payload: dict[str, Any]) -> None:
        self._client.table(TABLE_LOGS).insert(payload).execute()

    async def get_last_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.is_online:
            return self._read_local_logs(limit)
        try:
            resp = await self._run(
                lambda: self._client.table(TABLE_LOGS)
                .select("*")
                .eq("asset_ric", self.config.asset_ric)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return list(resp.data or [])
        except Exception as exc:
            logger.warning("get_last_logs falhou: %s", exc)
            return self._read_local_logs(limit)

    def _read_local_logs(self, limit: int) -> list[dict[str, Any]]:
        rows = self._pending.read_all()
        logs = [r["payload"] for r in rows if r.get("op") == "save_trade_log"]
        return logs[-limit:]

    # ------------------------------------------------------------------ #
    # strategy_performance
    # ------------------------------------------------------------------ #

    async def update_strategy_state(
        self,
        strategy_file: str,
        *,
        market_type: str = "",
        increment_signal: bool = True,
        win: bool | None = None,
    ) -> bool:
        payload = {
            "strategy_file": strategy_file,
            "asset_ric": self.config.asset_ric,
            "market_type": market_type or None,
            "increment_signal": increment_signal,
            "win": win,
        }
        return await self._execute_or_queue(
            "update_strategy_state",
            payload,
            lambda: self._sync_update_strategy_state(payload),
        )

    def _sync_update_strategy_state(self, payload: dict[str, Any]) -> None:
        sf = payload["strategy_file"]
        asset = payload["asset_ric"]
        existing = (
            self._client.table(TABLE_PERF)
            .select("*")
            .eq("strategy_file", sf)
            .eq("asset_ric", asset)
            .limit(1)
            .execute()
        )
        now = _iso(_utcnow())
        row = (existing.data or [{}])[0]
        total = int(row.get("total_signals") or 0)
        wins = int(row.get("wins") or 0)
        losses = int(row.get("losses") or 0)
        if payload.get("increment_signal"):
            total += 1
        if payload.get("win") is True:
            wins += 1
        elif payload.get("win") is False:
            losses += 1
        upsert = {
            "strategy_file": sf,
            "asset_ric": asset,
            "total_signals": total,
            "wins": wins,
            "losses": losses,
            "last_signal_at": now if payload.get("increment_signal") else row.get("last_signal_at"),
            "last_market_type": payload.get("market_type") or row.get("last_market_type"),
            "updated_at": now,
        }
        self._client.table(TABLE_PERF).upsert(upsert, on_conflict="strategy_file,asset_ric").execute()

    # ------------------------------------------------------------------ #
    # market_data_cache
    # ------------------------------------------------------------------ #

    async def upsert_market_cache(self, candles: list[dict[str, Any]]) -> bool:
        if not candles:
            return False
        last_ts = candles[-1].get("timestamp")
        payload = {
            "asset_ric": self.config.asset_ric,
            "timeframe_seconds": self.config.timeframe_seconds,
            "candles": candles,
            "candle_count": len(candles),
            "last_candle_at": last_ts,
            "written_by": self.config.instance_id,
        }
        return await self._execute_or_queue(
            "upsert_market_cache",
            payload,
            lambda: self._sync_upsert_market_cache(payload),
        )

    def _sync_upsert_market_cache(self, payload: dict[str, Any]) -> None:
        existing = (
            self._client.table(TABLE_CACHE)
            .select("version")
            .eq("asset_ric", payload["asset_ric"])
            .eq("timeframe_seconds", payload["timeframe_seconds"])
            .limit(1)
            .execute()
        )
        version = int((existing.data or [{}])[0].get("version") or 0) + 1
        payload["version"] = version
        payload["updated_at"] = _iso(_utcnow())
        self._client.table(TABLE_CACHE).upsert(
            payload, on_conflict="asset_ric,timeframe_seconds"
        ).execute()

    async def load_market_cache(self) -> list[dict[str, Any]]:
        if not self.is_online:
            return self._load_local_market_cache()
        try:
            resp = await self._run(
                lambda: self._client.table(TABLE_CACHE)
                .select("candles, candle_count, updated_at, written_by")
                .eq("asset_ric", self.config.asset_ric)
                .eq("timeframe_seconds", self.config.timeframe_seconds)
                .limit(1)
                .execute()
            )
            data = (resp.data or [{}])[0]
            candles = data.get("candles") or []
            logger.info(
                "Cache nuvem: %d velas (escrito por %s)",
                len(candles),
                data.get("written_by", "?"),
            )
            return list(candles)
        except Exception as exc:
            logger.warning("load_market_cache falhou: %s", exc)
            return self._load_local_market_cache()

    def _load_local_market_cache(self) -> list[dict[str, Any]]:
        path = self._local_snapshots / "upsert_market_cache.json"
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return list(data.get("candles") or [])
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # bot_status — coordenação multi-instância
    # ------------------------------------------------------------------ #

    async def register_heartbeat(
        self,
        *,
        strategy_file: str = "",
        market_type: str = "",
        order_status: str = "NONE",
    ) -> bool:
        payload = {
            "asset_ric": self.config.asset_ric,
            "active_instance_id": self.config.instance_id,
            "active_instance_label": self.config.instance_label,
            "instance_role": self.config.role,
            "order_status": order_status,
            "active_strategy_file": strategy_file or None,
            "market_type": market_type or None,
            "last_heartbeat": _iso(_utcnow()),
        }
        return await self._execute_or_queue(
            "upsert_bot_status",
            payload,
            lambda: self._sync_upsert_bot_status(payload, touch_version=False),
        )

    def _sync_upsert_bot_status(self, payload: dict[str, Any], *, touch_version: bool = True) -> None:
        asset = payload["asset_ric"]
        existing = (
            self._client.table(TABLE_STATUS)
            .select("*")
            .eq("asset_ric", asset)
            .limit(1)
            .execute()
        )
        row = (existing.data or [{}])[0]
        version = int(row.get("version") or 0)
        if touch_version:
            version += 1
        merge = {
            **row,
            **payload,
            "version": version,
            "updated_at": _iso(_utcnow()),
        }
        merge.pop("id", None)
        self._client.table(TABLE_STATUS).upsert(merge, on_conflict="asset_ric").execute()

    async def get_bot_status(self) -> BotStatusRow | None:
        if not self.is_online:
            return None
        try:
            resp = await self._run(
                lambda: self._client.table(TABLE_STATUS)
                .select("*")
                .eq("asset_ric", self.config.asset_ric)
                .limit(1)
                .execute()
            )
            data = (resp.data or [None])[0]
            if not data:
                return None
            lock_until = data.get("signal_lock_until")
            return BotStatusRow(
                asset_ric=data["asset_ric"],
                active_instance_id=data.get("active_instance_id", ""),
                active_instance_label=data.get("active_instance_label"),
                instance_role=data.get("instance_role", "primary"),
                order_status=data.get("order_status", "NONE"),
                active_strategy_file=data.get("active_strategy_file"),
                market_type=data.get("market_type"),
                signal_lock_until=datetime.fromisoformat(str(lock_until).replace("Z", "+00:00"))
                if lock_until
                else None,
                last_heartbeat=datetime.fromisoformat(
                    str(data.get("last_heartbeat", "")).replace("Z", "+00:00")
                )
                if data.get("last_heartbeat")
                else None,
                version=int(data.get("version") or 1),
            )
        except Exception as exc:
            logger.debug("get_bot_status: %s", exc)
            return None

    async def can_emit_signal(self, local_order_status: str = "NONE") -> tuple[bool, str]:
        """
        Verifica se esta instância pode emitir sinal sem conflitar com AWS/PC.
        """
        if not self.config.is_configured():
            return True, ""

        if self.config.role == "secondary":
            peer = await self.get_bot_status()
            if peer and peer.active_instance_id != self.config.instance_id:
                if peer.is_peer_fresh(self.config.peer_ttl_seconds):
                    if peer.instance_role == "primary" or peer.is_foreign_lock(
                        self.config.instance_id
                    ):
                        label = peer.active_instance_label or peer.active_instance_id
                        return False, f"Instancia primaria ativa ({label}) — sinal bloqueado"

        if local_order_status == "OPEN":
            return False, "Ordem local aberta"

        peer = await self.get_bot_status()
        if peer and peer.is_peer_fresh(self.config.peer_ttl_seconds):
            if peer.is_foreign_lock(self.config.instance_id):
                label = peer.active_instance_label or peer.active_instance_id
                return False, f"Outra instancia com exposicao ({label})"

        return True, ""

    async def try_acquire_signal_lock(self) -> bool:
        """Lock atômico via RPC (requer schema.sql aplicado no Supabase)."""
        if not self.is_online:
            return True
        try:
            resp = await self._run(
                lambda: self._client.rpc(
                    "try_acquire_signal_lock",
                    {
                        "p_asset_ric": self.config.asset_ric,
                        "p_instance_id": self.config.instance_id,
                        "p_instance_label": self.config.instance_label,
                        "p_lock_seconds": self.config.signal_lock_seconds,
                        "p_peer_ttl_seconds": self.config.peer_ttl_seconds,
                    },
                ).execute()
            )
            granted = bool(resp.data)
            if not granted:
                logger.warning(
                    "Lock de sinal negado — outra instancia (AWS/PC) operando"
                )
            return granted
        except Exception as exc:
            logger.warning("RPC try_acquire_signal_lock indisponivel — fallback otimista: %s", exc)
            await self.register_heartbeat(order_status="SIGNAL_LOCK")
            return True

    async def publish_analysis_state(
        self,
        *,
        strategy_file: str,
        market_type: str,
        order_status: str = "NONE",
    ) -> None:
        await self.register_heartbeat(
            strategy_file=strategy_file,
            market_type=market_type,
            order_status=order_status,
        )
