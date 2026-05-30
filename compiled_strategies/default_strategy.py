"""
Estratégia compilada — default_strategy.txt (Crypto IDX M5).
RSI + EMA(21) + MACD + SMA(50) + filtro ATR.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta

STRATEGY_NAME = "RSI + EMA + MACD (Padrão)"
MIN_CANDLES = 30


def analisar(df: pd.DataFrame) -> dict:
    if df is None or len(df) < MIN_CANDLES:
        return {
            "signal": None,
            "price": 0.0,
            "reason": f"Dados insuficientes ({len(df) if df is not None else 0}/{MIN_CANDLES})",
        }

    work = df.copy()
    close = work["close"]
    work["rsi"] = ta.rsi(close, length=14)
    work["ema21"] = ta.ema(close, length=21)
    work["sma50"] = ta.sma(close, length=50)
    work["atr"] = ta.atr(work["high"], work["low"], close, length=14)

    macd = ta.macd(close, fast=12, slow=26, signal=9)
    if macd is not None and "MACDh_12_26_9" in macd.columns:
        work["macd_hist"] = macd["MACDh_12_26_9"]
        work["macd_hist_prev"] = work["macd_hist"].shift(1)

    row = work.iloc[-1]
    prev = work.iloc[-2]
    price = float(row["close"])

    atr = float(row.get("atr", 0) or 0)
    if atr / price < 0.0005:
        return {"signal": None, "price": price, "reason": "Mercado lateral (ATR < 0,05%)"}

    rsi = float(row["rsi"])
    ema21 = float(row["ema21"])
    sma50 = float(row["sma50"])
    macd_hist = float(row.get("macd_hist", 0) or 0)
    macd_prev = float(row.get("macd_hist_prev", macd_hist) or macd_hist)
    macd_rising = macd_hist > macd_prev
    macd_falling = macd_hist < macd_prev

    if rsi < 30 and price > ema21 and price > sma50:
        if macd_hist > 0 or macd_rising:
            conf = 55
            if rsi < 25:
                conf += 10
            if macd_hist > 0 and macd_rising:
                conf += 15
            return {
                "signal": "COMPRA",
                "price": price,
                "reason": "RSI sobrevenda + acima EMA21/SMA50 + MACD",
                "confidence": min(90, conf),
            }

    if rsi > 70 and price < ema21 and price < sma50:
        if macd_hist < 0 or macd_falling:
            conf = 55
            if rsi > 75:
                conf += 10
            if macd_hist < 0 and macd_falling:
                conf += 15
            return {
                "signal": "VENDA",
                "price": price,
                "reason": "RSI sobrecompra + abaixo EMA21/SMA50 + MACD",
                "confidence": min(90, conf),
            }

    return {
        "signal": None,
        "price": price,
        "reason": f"Aguardando setup (RSI {rsi:.0f})",
    }
