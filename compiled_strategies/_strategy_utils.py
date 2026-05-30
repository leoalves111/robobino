"""Helpers compartilhados pelas estratégias compiladas."""

from __future__ import annotations

import pandas as pd


def find_col(df: pd.DataFrame, prefix: str) -> str | None:
    for col in df.columns:
        if col.startswith(prefix):
            return col
    return None


def volume_sma(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Média móvel de volume — compatível com pandas_ta."""
    work = df.copy()
    work.ta.sma(length=length, close="volume", prefix="VOL", append=True)
    col = find_col(work, "VOL_SMA_") or find_col(work, "VOL_")
    if col is None:
        return work["volume"].rolling(length).mean()
    return work[col]
