"""Testes offline do database_manager (sem Supabase real)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from database_manager import DatabaseManager, PendingQueue, SupabaseConfig


def test_pending_queue_roundtrip(tmp_path: Path) -> None:
    q = PendingQueue(tmp_path / "pending.jsonl")
    q.enqueue("save_trade_log", {"signal": "COMPRA", "price": 641.0})
    rows = q.read_all()
    assert len(rows) == 1
    assert rows[0]["op"] == "save_trade_log"
    q.rewrite([])
    assert not (tmp_path / "pending.jsonl").is_file()


def test_config_disabled_by_default() -> None:
    cfg = SupabaseConfig(
        url="",
        key="",
        enabled=False,
        instance_id="test",
        instance_label="test",
        role="primary",
        asset_ric="Z-CRY/IDX",
        timeframe_seconds=300,
        peer_ttl_seconds=120,
        sync_interval_seconds=30,
        signal_lock_seconds=300,
    )
    assert not cfg.is_configured()
    mgr = DatabaseManager(cfg)
    assert not mgr.is_online


def test_local_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("database_manager.PENDING_FILE", tmp_path / "p.jsonl")
    monkeypatch.setattr("database_manager.LOCAL_SNAPSHOT_DIR", tmp_path / "snap")
    cfg = SupabaseConfig(
        url="http://invalid",
        key="x",
        enabled=True,
        instance_id="pc",
        instance_label="PC",
        role="primary",
        asset_ric="Z-CRY/IDX",
        timeframe_seconds=300,
        peer_ttl_seconds=120,
        sync_interval_seconds=30,
        signal_lock_seconds=300,
    )
    mgr = DatabaseManager(cfg)
    mgr._save_local_snapshot("upsert_market_cache", {"candles": [{"close": 641.0}]})
    loaded = mgr._load_local_market_cache()
    assert len(loaded) == 1
