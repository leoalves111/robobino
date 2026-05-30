"""Testes do cache de velas M5."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from candle_cache import CacheFreshnessPolicy, CandleCache
from data_engine import Candle


def _parse(row: dict):
    ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
    return Candle(
        timestamp=ts,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume", 0)),
    )


def _row(ts: datetime, close: float) -> dict:
    return {
        "timestamp": ts.isoformat(),
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "volume": 0,
    }


def test_fifo_window(base: Path) -> None:
    path = base / "candles_m5.json"
    policy = CacheFreshnessPolicy(interval_seconds=8, max_last_candle_age_multiplier=100)
    cache = CandleCache(path=path, max_candles=5, parse_row=_parse, policy=policy)
    now = datetime.now(timezone.utc)

    for i in range(7):
        cache.append(
            Candle(
                timestamp=now - timedelta(seconds=8 * (6 - i)),
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5 + i,
            )
        )

    assert cache.count == 5
    cache.flush()


def test_stale_cache_rejected(tmp_path: Path) -> None:
    path = tmp_path / "stale.json"
    policy = CacheFreshnessPolicy(interval_seconds=300, max_last_candle_age_multiplier=3)
    old = datetime.now(timezone.utc) - timedelta(days=2)
    rows = [_row(old + timedelta(minutes=5 * i), 640.0 + i) for i in range(10)]
    path.write_text(json.dumps(rows), encoding="utf-8")

    cache = CandleCache(path=path, parse_row=_parse, policy=policy)
    result = cache.load()
    assert not result.valid
    assert result.message == "expirado_idade"
    assert result.archived_stale


def test_gap_splits_session(tmp_path: Path) -> None:
    path = tmp_path / "gap.json"
    policy = CacheFreshnessPolicy(interval_seconds=300, max_gap_multiplier=2)
    now = datetime.now(timezone.utc)
    rows = [
        _row(now - timedelta(hours=5), 640.0),
        _row(now - timedelta(hours=4, minutes=55), 640.5),
        _row(now - timedelta(minutes=20), 641.0),
        _row(now - timedelta(minutes=15), 641.5),
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")

    cache = CandleCache(path=path, parse_row=_parse, policy=policy)
    result = cache.load()
    assert result.valid
    assert result.count == 2


def test_append_after_long_stop(tmp_path: Path) -> None:
    policy = CacheFreshnessPolicy(interval_seconds=300, max_gap_multiplier=2)
    cache = CandleCache(path=tmp_path / "live.json", parse_row=_parse, policy=policy)
    now = datetime.now(timezone.utc)
    cache._rows = [_row(now - timedelta(hours=3), 640.0)]

    cache.append(
        Candle(
            timestamp=now,
            open=641.0,
            high=642.0,
            low=640.0,
            close=641.5,
        )
    )
    assert cache.count == 1


def test_corrupt_json(path: Path) -> None:
    path.write_text("{invalid", encoding="utf-8")
    cache = CandleCache(path=path, parse_row=_parse)
    result = cache.load()
    assert not result.valid
    assert result.message == "corrompido"


def test_empty_file(path: Path) -> None:
    path.write_text("[]", encoding="utf-8")
    cache = CandleCache(path=path, parse_row=_parse)
    result = cache.load()
    assert not result.valid
    assert result.message == "vazio"


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        test_fifo_window(base)
        test_stale_cache_rejected(base)
        test_gap_splits_session(base)
        test_append_after_long_stop(base)
        test_corrupt_json(base / "bad.json")
        test_empty_file(base / "empty.json")
    print("OK: todos os testes do cache passaram")
