"""
Diagnóstico do pipeline de sinais — executa sem painel Live.
Uso: .\.venv\Scripts\python.exe diagnose.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from data_engine import DataEngine
from strategy_manager import StrategyManager

load_dotenv()


def _synthetic_df(rows: int = 80, base: float = 641.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    ts0 = datetime.now(timezone.utc) - timedelta(minutes=5 * rows)
    prices = [base]
    for _ in range(rows - 1):
        prices.append(prices[-1] + rng.normal(0, 0.15))

    data = []
    for i, close in enumerate(prices):
        open_p = close + rng.normal(0, 0.05)
        high = max(open_p, close) + abs(rng.normal(0, 0.08))
        low = min(open_p, close) - abs(rng.normal(0, 0.08))
        data.append(
            {
                "timestamp": ts0 + timedelta(minutes=5 * i),
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
                "volume": 0.0,
            }
        )
    return pd.DataFrame(data)


def test_price_extraction() -> bool:
    sample = {
        "entrie_rate": 641.867,
        "asset_ric": "Z-CRY/IDX",
        "deal_id": 123,
    }
    price = DataEngine._extract_price(sample)
    ok = price is not None and abs(price - 641.867) < 0.001
    print(f"[{'OK' if ok else 'FALHA'}] Extracao entrie_rate -> {price}")
    return ok


def test_strategies() -> bool:
    df = _synthetic_df()
    mgr = StrategyManager()
    all_ok = True
    for name in mgr.list_available():
        mgr.load(name)
        result = mgr.analyze(df)
        signal = result.get("signal")
        reason = result.get("reason", "")
        conf = result.get("confidence", 0)
        print(f"[INFO] {name}: signal={signal} conf={conf} | {reason}")
        if "Erro" in str(reason):
            all_ok = False
    return all_ok


async def test_live_connection() -> bool:
    auth = os.getenv("AUTH_TOKEN", "").strip()
    device = os.getenv("DEVICE_ID", "").strip()
    if not auth or not device:
        print("[SKIP] AUTH_TOKEN/DEVICE_ID ausentes — pulando teste ao vivo")
        return True

    engine = DataEngine(auth_token=auth, device_id=device)
    engine._running = True
    tasks = [
        asyncio.create_task(engine.run()),
        asyncio.create_task(engine.rate_poll_loop(interval=1.0)),
        asyncio.create_task(engine.clock_loop(interval=1.0)),
    ]
    try:
        await asyncio.sleep(10)
    finally:
        engine._running = False
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.stop()

    ok_price = engine.state.last_price is not None and engine.state.price_ticks > 0
    ok_candles = engine.state.candles_count >= 1 or engine.aggregator._current is not None
    print(
        f"[{'OK' if ok_price else 'FALHA'}] Preço ao vivo: {engine.state.last_price} "
        f"(ticks={engine.state.price_ticks})"
    )
    print(
        f"[{'OK' if ok_candles else 'FALHA'}] Velas: {engine.state.candles_count} "
        f"(histórico={'sim' if engine.state.history_loaded else 'não'})"
    )
    return ok_price and ok_candles


async def main() -> int:
    print("=== Diagnóstico Binomo Signal Generator ===\n")
    checks = [
        test_price_extraction(),
        test_strategies(),
        await test_live_connection(),
    ]
    print()
    if all(checks):
        print("Resultado: pipeline operacional.")
        return 0
    print("Resultado: problemas detectados — veja logs/signal_generator.log")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
