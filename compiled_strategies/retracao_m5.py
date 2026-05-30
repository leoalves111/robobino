from __future__ import annotations

import pandas as pd
import pandas_ta as ta

STRATEGY_NAME = "Retracao Bollinger M5"
MIN_CANDLES = 25


def _bb_col(df: pd.DataFrame, prefix: str) -> str | None:
    for col in df.columns:
        if col.startswith(prefix):
            return col
    return None


def analisar(df: pd.DataFrame) -> dict:
    """
    Estrategia: Retracao de Vela M5 (Bandas de Bollinger + RSI)
    """
    if df is None or len(df) < MIN_CANDLES:
        return {
            "signal": None,
            "price": float(df.iloc[-1]["close"]) if df is not None and len(df) else 0.0,
            "reason": f"Dados insuficientes ({len(df) if df is not None else 0}/{MIN_CANDLES})",
        }

    work = df.copy()
    work.ta.bbands(length=20, std=2.0, append=True)
    work.ta.rsi(length=14, append=True)

    bbl = _bb_col(work, "BBL_")
    bbu = _bb_col(work, "BBU_")
    if not bbl or not bbu or "RSI_14" not in work.columns:
        return {"signal": None, "price": float(work.iloc[-1]["close"]), "reason": "Indicadores indisponiveis"}

    curr = work.iloc[-1]

    if curr["low"] <= curr[bbl] and curr["RSI_14"] < 32:
        conf = 58
        if curr["RSI_14"] < 28:
            conf += 12
        return {
            "signal": "COMPRA",
            "price": float(curr["close"]),
            "reason": "Retracao: Banda Inferior + RSI Sobrevendido",
            "confidence": conf,
        }

    if curr["high"] >= curr[bbu] and curr["RSI_14"] > 68:
        conf = 58
        if curr["RSI_14"] > 72:
            conf += 12
        return {
            "signal": "VENDA",
            "price": float(curr["close"]),
            "reason": "Retracao: Banda Superior + RSI Sobrecomprado",
            "confidence": conf,
        }

    return {"signal": None, "price": float(curr["close"]), "reason": "Aguardando setup de retracao"}
