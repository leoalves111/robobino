"""
Persistência de velas M5 — janela FIFO, frescor temporal e gravação atômica.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

MAX_CACHE_CANDLES = 500
DEFAULT_CACHE_PATH = Path("cache") / "candles_m5.json"
REQUIRED_FIELDS = ("timestamp", "open", "high", "low", "close")


@dataclass(frozen=True)
class CacheFreshnessPolicy:
    """Regras para usar apenas velas do período operacional atual."""

    interval_seconds: int = 300
    max_gap_multiplier: float = 2.0
    max_last_candle_age_multiplier: float = 3.0
    max_age_hours: float = 24.0
    same_trading_day: bool = False
    tz_offset_hours: int = -3

    @property
    def max_intrabar_gap_seconds(self) -> float:
        return self.interval_seconds * self.max_gap_multiplier

    @property
    def max_last_candle_age_seconds(self) -> float:
        return self.interval_seconds * self.max_last_candle_age_multiplier


@dataclass
class CacheLoadResult:
    candles: list[Any]
    valid: bool
    message: str
    count: int = 0
    archived_stale: bool = False


def parse_row_timestamp(row: dict[str, Any]) -> Optional[datetime]:
    try:
        raw = row.get("timestamp")
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def trading_day(dt: datetime, tz_offset_hours: int) -> datetime.date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt + timedelta(hours=tz_offset_hours)
    return local.date()


class CandleCache:
    """
    Mantém até max_candles velas em memória (FIFO).
    Só reutiliza velas contíguas, recentes e do período operacional válido.
    """

    def __init__(
        self,
        path: Path = DEFAULT_CACHE_PATH,
        max_candles: int = MAX_CACHE_CANDLES,
        parse_row: Callable[[dict[str, Any]], Any] | None = None,
        policy: CacheFreshnessPolicy | None = None,
    ) -> None:
        self.path = path
        self.max_candles = max_candles
        self._parse_row = parse_row
        self.policy = policy or CacheFreshnessPolicy()
        self._rows: list[dict[str, Any]] = []
        self._dirty = False
        self.last_status = "nao_carregado"

    @property
    def count(self) -> int:
        return len(self._rows)

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def export_rows(self) -> list[dict[str, Any]]:
        """Cópia das velas serializáveis (para Supabase / backup)."""
        return list(self._rows)

    @classmethod
    def policy_from_env(cls, interval_seconds: int) -> CacheFreshnessPolicy:
        import os

        return CacheFreshnessPolicy(
            interval_seconds=interval_seconds,
            max_gap_multiplier=float(os.getenv("CACHE_MAX_GAP_MULTIPLIER", "2")),
            max_last_candle_age_multiplier=float(
                os.getenv("CACHE_MAX_LAST_CANDLE_AGE_MULTIPLIER", "3")
            ),
            max_age_hours=float(os.getenv("CACHE_MAX_AGE_HOURS", "24")),
            same_trading_day=os.getenv("CACHE_SAME_TRADING_DAY", "false").lower()
            in ("1", "true", "yes"),
            tz_offset_hours=int(os.getenv("CACHE_TZ_OFFSET", "-3")),
        )

    def candle_to_row(self, candle: Any) -> dict[str, Any]:
        ts = candle.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return {
            "timestamp": ts.isoformat(),
            "open": float(candle.open),
            "high": float(candle.high),
            "low": float(candle.low),
            "close": float(candle.close),
            "volume": float(getattr(candle, "volume", 0) or 0),
        }

    @staticmethod
    def _validate_row(row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        for field in REQUIRED_FIELDS:
            if field not in row:
                return False
        try:
            close = float(row["close"])
            if close <= 0:
                return False
        except (TypeError, ValueError):
            return False
        return parse_row_timestamp(row) is not None

    def _split_contiguous_segments(
        self, rows: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        if not rows:
            return []
        segments: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = [rows[0]]
        max_gap = self.policy.max_intrabar_gap_seconds

        for row in rows[1:]:
            prev_ts = parse_row_timestamp(current[-1])
            curr_ts = parse_row_timestamp(row)
            if prev_ts is None or curr_ts is None:
                continue
            gap = (curr_ts - prev_ts).total_seconds()
            if gap > max_gap:
                segments.append(current)
                current = [row]
            else:
                current.append(row)
        segments.append(current)
        return segments

    def _filter_by_max_age(
        self, rows: list[dict[str, Any]], now: datetime
    ) -> list[dict[str, Any]]:
        cutoff = now - timedelta(hours=self.policy.max_age_hours)
        kept: list[dict[str, Any]] = []
        for row in rows:
            ts = parse_row_timestamp(row)
            if ts and ts >= cutoff:
                kept.append(row)
        return kept

    def _filter_same_trading_day(
        self, rows: list[dict[str, Any]], now: datetime
    ) -> list[dict[str, Any]]:
        if not self.policy.same_trading_day:
            return rows
        today = trading_day(now, self.policy.tz_offset_hours)
        return [
            row
            for row in rows
            if parse_row_timestamp(row)
            and trading_day(parse_row_timestamp(row), self.policy.tz_offset_hours) == today
        ]

    def apply_freshness_policy(
        self, rows: list[dict[str, Any]], now: Optional[datetime] = None
    ) -> tuple[list[dict[str, Any]], str]:
        """
        Retorna apenas velas utilizáveis para a sessão atual.
        Descarta: velas muito antigas, lacunas longas, última vela desatualizada, outro dia.
        """
        now = now or datetime.now(timezone.utc)
        if not rows:
            return [], "vazio"

        rows = sorted(rows, key=lambda r: r["timestamp"])
        rows = self._filter_by_max_age(rows, now)
        if not rows:
            return [], "expirado_idade"

        rows = self._filter_same_trading_day(rows, now)
        if not rows:
            return [], "expirado_dia"

        segments = self._split_contiguous_segments(rows)
        segment = segments[-1]

        last_ts = parse_row_timestamp(segment[-1])
        if last_ts is None:
            return [], "expirado_invalido"

        last_close = last_ts + timedelta(seconds=self.policy.interval_seconds)
        age_since_close = (now - last_close).total_seconds()
        if age_since_close > self.policy.max_last_candle_age_seconds:
            hours = age_since_close / 3600
            logger.warning(
                "Última vela do cache encerrou há %.1f h — limite %.0f min para reinício.",
                hours,
                self.policy.max_last_candle_age_seconds / 60,
            )
            return [], "expirado_lacuna"

        if len(segments) > 1:
            dropped = sum(len(s) for s in segments[:-1])
            logger.info(
                "Cache: descartadas %d velas antes de lacuna de mercado/parada.",
                dropped,
            )

        return segment[-self.max_candles :], "ok"

    def load(self) -> CacheLoadResult:
        """Carrega cache com validação de integridade e frescor temporal."""
        self._rows = []
        self._dirty = False

        if not self.path.is_file():
            self.last_status = "ausente"
            logger.info("Cache %s ausente — nova coleta M5.", self.path)
            return CacheLoadResult([], False, "cache_ausente", 0)

        try:
            raw_text = self.path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            self._handle_corrupt(f"erro de leitura: {exc}")
            return CacheLoadResult([], False, "corrompido", 0)

        if not raw_text:
            self._handle_empty()
            return CacheLoadResult([], False, "vazio", 0)

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            self._handle_corrupt(f"JSON inválido: {exc}")
            return CacheLoadResult([], False, "corrompido", 0)

        if not isinstance(payload, list) or len(payload) == 0:
            self._handle_empty()
            return CacheLoadResult([], False, "vazio", 0)

        valid_rows: list[dict[str, Any]] = []
        for item in payload:
            if self._validate_row(item):
                valid_rows.append(item)

        if not valid_rows:
            self._handle_corrupt("nenhuma vela válida no arquivo")
            return CacheLoadResult([], False, "corrompido", 0)

        fresh_rows, freshness_msg = self.apply_freshness_policy(valid_rows)
        if not fresh_rows:
            archived = self._archive_stale_file(freshness_msg, valid_rows)
            self.last_status = freshness_msg
            return CacheLoadResult(
                [],
                False,
                freshness_msg,
                0,
                archived_stale=archived,
            )

        self._rows = fresh_rows
        candles = self._rows_to_candles(self._rows)
        if not candles:
            self._handle_corrupt("falha ao converter velas do cache")
            return CacheLoadResult([], False, "corrompido", 0)

        dropped = len(payload) - len(fresh_rows)
        if dropped:
            self._dirty = True
            logger.info(
                "Cache ajustado: %d velas operacionais (removidas %d antigas/descontinuas).",
                len(fresh_rows),
                dropped,
            )

        self.last_status = "ok"
        logger.info(
            "Cache operacional: %d velas M5 (última %s)",
            len(candles),
            fresh_rows[-1]["timestamp"],
        )
        return CacheLoadResult(candles, True, "ok", len(candles))

    def _rows_to_candles(self, rows: list[dict[str, Any]]) -> list[Any]:
        if not self._parse_row:
            return []
        candles: list[Any] = []
        for row in rows:
            candle = self._parse_row(row)
            if candle is not None:
                candles.append(candle)
        return candles

    def _detect_session_break(self, new_ts: datetime) -> bool:
        if not self._rows:
            return False
        last_ts = parse_row_timestamp(self._rows[-1])
        if last_ts is None:
            return True
        gap = (new_ts - last_ts).total_seconds()
        return gap > self.policy.max_intrabar_gap_seconds

    def append(self, candle: Any) -> None:
        """FIFO + reinício de sessão se houver lacuna longa."""
        row = self.candle_to_row(candle)
        new_ts = parse_row_timestamp(row)
        if new_ts is None:
            return

        if self._detect_session_break(new_ts):
            if self._rows:
                gap_min = (
                    new_ts - parse_row_timestamp(self._rows[-1])
                ).total_seconds() / 60
                logger.warning(
                    "Nova sessão M5 — cache reiniciado (lacuna de %.0f min). "
                    "Velas antigas não serão usadas nos indicadores.",
                    gap_min,
                )
            self._rows = [row]
        elif self._rows and self._rows[-1].get("timestamp") == row["timestamp"]:
            self._rows[-1] = row
        else:
            self._rows.append(row)
            if len(self._rows) > self.max_candles:
                self._rows = self._rows[-self.max_candles :]
        self._dirty = True

    def replace_all(self, candles: list[Any]) -> None:
        """Substitui buffer aplicando política de frescor."""
        rows = [self.candle_to_row(c) for c in candles]
        fresh_rows, msg = self.apply_freshness_policy(rows)
        self._rows = fresh_rows if fresh_rows else rows[-self.max_candles :]
        self._dirty = True
        if msg != "ok":
            logger.warning("Histórico externo filtrado: %s", msg)

    def flush(self) -> bool:
        if not self._dirty and self.path.is_file():
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(".json.tmp")
            data = json.dumps(self._rows, ensure_ascii=False, separators=(",", ":"))
            tmp_path.write_text(data, encoding="utf-8")
            os.replace(tmp_path, self.path)
            self._dirty = False
            logger.debug("Cache salvo: %d velas em %s", len(self._rows), self.path)
            return True
        except OSError as exc:
            logger.error("Falha ao salvar cache %s: %s", self.path, exc)
            return False

    def _archive_stale_file(
        self, reason: str, old_rows: list[dict[str, Any]]
    ) -> bool:
        """Move cache expirado para backup e zera arquivo ativo."""
        logger.warning(
            "CACHE EXPIRADO (%s): dados não pertencem ao período operacional atual — "
            "backup criado; coleta M5 reiniciada do zero.",
            reason,
        )
        if self.path.is_file():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup = self.path.with_name(
                f"{self.path.stem}.expired_{reason}_{stamp}{self.path.suffix}"
            )
            try:
                os.replace(self.path, backup)
                logger.info("Backup cache expirado: %s (%d velas arquivadas)", backup, len(old_rows))
            except OSError as exc:
                logger.error("Falha ao arquivar cache expirado: %s", exc)

        self._rows = []
        self._dirty = True
        self.flush()
        return True

    def _handle_corrupt(self, reason: str) -> None:
        self.last_status = "corrompido"
        logger.warning(
            "CACHE CORROMPIDO (%s): %s — backup criado; coleta reiniciada.",
            self.path,
            reason,
        )
        self._backup_corrupt_file()
        self._rows = []
        self._dirty = False

    def _handle_empty(self) -> None:
        self.last_status = "vazio"
        logger.warning("Cache vazio — aguardando novas velas M5 do stream.")
        self._rows = []
        self._dirty = False

    def _backup_corrupt_file(self) -> None:
        if not self.path.is_file():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup = self.path.with_name(f"{self.path.stem}.corrupt_{stamp}{self.path.suffix}")
        try:
            os.replace(self.path, backup)
            logger.info("Backup cache corrompido: %s", backup)
        except OSError as exc:
            logger.error("Não foi possível mover cache corrompido: %s", exc)
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
