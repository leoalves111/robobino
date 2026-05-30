"""
Estratégia compilada — RSI + EMA + MACD + Stochastic (M5 Crypto IDX).
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta

STRATEGY_NAME = "RSI + EMA + MACD Pro"
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
    work["ema9"] = ta.ema(close, length=9)
    work["ema21"] = ta.ema(close, length=21)
    work["sma50"] = ta.sma(close, length=50)
    work["atr"] = ta.atr(work["high"], work["low"], close, length=14)

    macd = ta.macd(close, fast=12, slow=26, signal=9)
    if macd is not None and "MACDh_12_26_9" in macd.columns:
        work["macd_hist"] = macd["MACDh_12_26_9"]
        work["macd_hist_prev"] = work["macd_hist"].shift(1)

    stoch = ta.stoch(work["high"], work["low"], close, k=14, d=3)
    if stoch is not None:
        work["stoch_k"] = stoch.iloc[:, 0]
        work["stoch_d"] = stoch.iloc[:, 1]

    row = work.iloc[-1]
    prev = work.iloc[-2]
    price = float(row["close"])

    atr = float(row.get("atr", 0) or 0)
    if atr / price < 0.0002:
        return {"signal": None, "price": price, "reason": "Mercado lateral (ATR baixo)"}

    rsi = float(row["rsi"])
    ema21 = float(row["ema21"])
    sma50 = float(row["sma50"])
    macd_hist = float(row.get("macd_hist", 0) or 0)
    macd_rising = macd_hist > float(row.get("macd_hist_prev", macd_hist) or macd_hist)
    stoch_k = float(row.get("stoch_k", 50) or 50)

    trend_up = float(row["ema9"]) > ema21 > sma50
    trend_down = float(row["ema9"]) < ema21 < sma50

    buy_score = 0
    if rsi < 32:
        buy_score += 25
    elif rsi < 40:
        buy_score += 12
    if price > ema21:
        buy_score += 15
    if trend_up:
        buy_score += 20
    if macd_hist > 0 and macd_rising:
        buy_score += 20
    if stoch_k < 25 and stoch_k > float(prev.get("stoch_k", stoch_k) or stoch_k):
        buy_score += 15
    if price > sma50:
        buy_score += 10

    sell_score = 0
    if rsi > 68:
        sell_score += 25
    elif rsi > 60:
        sell_score += 12
    if price < ema21:
        sell_score += 15
    if trend_down:
        sell_score += 20
    if macd_hist < 0 and not macd_rising:
        sell_score += 20
    if stoch_k > 75 and stoch_k < float(prev.get("stoch_k", stoch_k) or stoch_k):
        sell_score += 15
    if price < sma50:
        sell_score += 10

    if buy_score >= 45 and buy_score > sell_score + 8:
        return {
            "signal": "COMPRA",
            "price": price,
            "reason": "Pullback em tendência + MACD/Stoch",
            "confidence": min(95, buy_score),
        }
    if sell_score >= 45 and sell_score > buy_score + 8:
        return {
            "signal": "VENDA",
            "price": price,
            "reason": "Pullback em tendência + MACD/Stoch",
            "confidence": min(95, sell_score),
        }

    return {
        "signal": None,
        "price": price,
        "reason": f"Setup incompleto (C:{buy_score} V:{sell_score})",
    }
