#!/usr/bin/env python3
"""
Migração one-shot: cache/candles_m5.json → Supabase market_data_cache.

Uso (na raiz do projeto):
    python migrate_to_supabase.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = ROOT / "cache" / "candles_m5.json"
TABLE = "market_data_cache"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _progress(current: int, total: int, prefix: str = "") -> None:
    if total <= 0:
        return
    pct = min(100, int(current * 100 / total))
    bar_len = 30
    filled = int(bar_len * pct / 100)
    bar = "#" * filled + "-" * (bar_len - filled)
    line = f"{prefix}[{bar}] {pct:3d}% ({current}/{total})"
    if current < total:
        print(f"\r{line}", end="", flush=True)
    else:
        print(f"\r{line}", flush=True)


def load_candles(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"Arquivo vazio: {path}")

    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("JSON deve ser uma lista de velas")

    candles: list[dict] = []
    required = ("timestamp", "open", "high", "low", "close")
    total = len(data)

    for i, row in enumerate(data, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Item {i} não é um objeto JSON")
        missing = [k for k in required if k not in row]
        if missing:
            raise ValueError(f"Item {i} sem campos: {', '.join(missing)}")
        candles.append(
            {
                "timestamp": str(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0),
            }
        )
        if i % max(1, total // 20) == 0 or i == total:
            _progress(i, total, prefix="Lendo JSON ")

    return candles


def build_payload(candles: list[dict], client, asset_ric: str, timeframe: int, written_by: str) -> dict:
    last_ts = candles[-1]["timestamp"]
    version = 1

    try:
        existing = (
            client.table(TABLE)
            .select("version")
            .eq("asset_ric", asset_ric)
            .eq("timeframe_seconds", timeframe)
            .limit(1)
            .execute()
        )
        if existing.data:
            version = int(existing.data[0].get("version") or 0) + 1
    except Exception:
        pass

    return {
        "asset_ric": asset_ric,
        "timeframe_seconds": timeframe,
        "candles": candles,
        "candle_count": len(candles),
        "last_candle_at": last_ts,
        "written_by": written_by,
        "version": version,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    load_dotenv(ROOT / ".env")

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    asset_ric = os.getenv("ASSET_RIC", "Z-CRY/IDX").strip()
    timeframe = int(os.getenv("TIMEFRAME_SECONDS", "300"))
    written_by = os.getenv("BOT_INSTANCE_ID", "").strip() or socket.gethostname()
    cache_path = Path(os.getenv("MIGRATE_CACHE_PATH", str(DEFAULT_CACHE)))

    if not url or not key:
        _log("ERRO: defina SUPABASE_URL e SUPABASE_KEY no .env")
        return 1

    _log("=" * 60)
    _log("Migração candles_m5.json → Supabase market_data_cache")
    _log("=" * 60)
    _log(f"Arquivo local : {cache_path}")
    _log(f"Asset RIC     : {asset_ric}")
    _log(f"Timeframe     : {timeframe}s")
    _log(f"Instância     : {written_by}")
    _log("")

    try:
        from supabase import create_client

        _log("[1/4] Lendo velas do cache local...")
        candles = load_candles(cache_path)
        _log(f"      → {len(candles)} registros lidos")

        if not candles:
            _log("Nada para migrar (lista vazia).")
            return 0

        _log("[2/4] Conectando ao Supabase...")
        client = create_client(url, key)
        _log("      → conectado")

        _log("[3/4] Preparando upsert (idempotente)...")
        payload = build_payload(candles, client, asset_ric, timeframe, written_by)
        _progress(1, 1, prefix="Enviando    ")

        _log("[4/4] Gravando na tabela market_data_cache...")
        response = (
            client.table(TABLE)
            .upsert(payload, on_conflict="asset_ric,timeframe_seconds")
            .execute()
        )

        rows = response.data or []
        _log("")
        _log("=" * 60)
        _log("MIGRAÇÃO CONCLUÍDA COM SUCESSO")
        _log("=" * 60)
        _log(f"Velas enviadas     : {len(candles)}")
        _log(f"Última vela        : {candles[-1]['timestamp']}")
        _log(f"Versão no Supabase : {payload['version']}")
        _log(f"Linhas retornadas  : {len(rows)}")
        _log("")
        _log("Verifique no Supabase → Table Editor → market_data_cache")
        _log(f"  Filtro: asset_ric = {asset_ric}, timeframe_seconds = {timeframe}")
        return 0

    except FileNotFoundError as exc:
        _log(f"\nERRO: {exc}")
        return 1
    except json.JSONDecodeError as exc:
        _log(f"\nERRO: JSON inválido em {cache_path}: {exc}")
        return 1
    except Exception as exc:
        _log(f"\nERRO na migração: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
