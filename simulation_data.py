"""
Geração de velas sintéticas para modo simulação.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from data_engine import Candle


def generate_seed_candles(
    count: int = 80,
    base: float = 641.0,
    interval_seconds: int = 8,
    seed: int = 42,
) -> list[Candle]:
    """Histórico inicial com tendência + pullback no final (facilita sinais de teste)."""
    rng = np.random.default_rng(seed)
    ts0 = datetime.now(timezone.utc) - timedelta(seconds=interval_seconds * count)

    prices: list[float] = []
    for i in range(count):
        if i < count - 25:
            prices.append(base + rng.normal(0, 0.12))
        elif i < count - 8:
            drift = (i - (count - 25)) * 0.04
            prices.append(base + drift + rng.normal(0, 0.08))
        else:
            pullback = (count - i) * 0.18
            prices.append(base + 0.65 - pullback + rng.normal(0, 0.05))

    candles: list[Candle] = []
    for i, close in enumerate(prices):
        open_p = close + rng.normal(0, 0.04)
        high = max(open_p, close) + abs(rng.normal(0, 0.06))
        low = min(open_p, close) - abs(rng.normal(0, 0.06))
        candles.append(
            Candle(
                timestamp=ts0 + timedelta(seconds=interval_seconds * i),
                open=open_p,
                high=high,
                low=low,
                close=close,
            )
        )
    return candles


def next_sim_price(last: float, rng: np.random.Generator) -> float:
    return last + rng.normal(0, 0.12)


def candles_to_dataframe(candles: list[Candle]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    rows = [c.to_dict() for c in candles]
    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
