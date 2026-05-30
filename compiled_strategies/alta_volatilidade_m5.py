import pandas as pd
import pandas_ta as ta

STRATEGY_NAME = "Alta Volatilidade M5 (Tendencia)"
MIN_CANDLES = 40


def analisar(df: pd.DataFrame) -> dict:
    if df is None or len(df) < MIN_CANDLES:
        return {"signal": None, "price": 0.0, "reason": "Dados insuficientes"}

    work = df.copy()
    work.ta.adx(length=14, append=True)
    work.ta.ema(length=20, append=True)
    work.ta.ema(length=50, append=True)
    work.ta.rsi(length=14, append=True)
    work.ta.atr(length=14, append=True)

    curr = work.iloc[-1]
    prev = work.iloc[-2]

    adx = float(curr.get("ADX_14", 0) or 0)
    adx_prev = float(prev.get("ADX_14", 0) or 0)
    if pd.isna(adx):
        adx = 0.0
    if pd.isna(adx_prev):
        adx_prev = 0.0
    rsi = float(curr.get("RSI_14", 50) or 50)
    rsi_prev = float(prev.get("RSI_14", 50) or 50)
    if pd.isna(rsi):
        rsi = 50.0
    if pd.isna(rsi_prev):
        rsi_prev = 50.0
    atr_pct = float(curr.get("ATRr_14", 0) or 0) / float(curr["close"])

    if atr_pct < 0.0002:
        return {"signal": None, "price": float(curr["close"]), "reason": "Volatilidade insuficiente"}

    adx_forte = adx >= 25 and adx >= adx_prev
    tendencia_alta = curr["EMA_20"] > curr["EMA_50"] and curr["close"] > curr["EMA_20"]
    tendencia_baixa = curr["EMA_20"] < curr["EMA_50"] and curr["close"] < curr["EMA_20"]

    if adx_forte and tendencia_alta and 40 <= rsi <= 55 and rsi > rsi_prev:
        return {
            "signal": "COMPRA",
            "price": float(curr["close"]),
            "reason": "Alta Vol: Tendencia ADX + pullback RSI",
            "confidence": 82,
        }

    if adx_forte and tendencia_baixa and 45 <= rsi <= 60 and rsi < rsi_prev:
        return {
            "signal": "VENDA",
            "price": float(curr["close"]),
            "reason": "Alta Vol: Tendencia ADX + pullback RSI",
            "confidence": 82,
        }

    return {"signal": None, "price": float(curr["close"]), "reason": "Sem setup de tendencia forte"}
