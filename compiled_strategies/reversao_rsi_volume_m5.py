import pandas as pd
import pandas_ta as ta

STRATEGY_NAME = "Reversao RSI + Volume M5"
MIN_CANDLES = 30


def _volume_strength(work: pd.DataFrame) -> bool:
    vol = work["volume"]
    if float(vol.max() or 0) > 0:
        media = float(vol.tail(20).mean())
        return media > 0 and float(vol.iloc[-1]) > media * 1.05
    rng = (work["high"] - work["low"]) / work["close"].replace(0, pd.NA)
    media_rng = float(rng.tail(20).mean())
    if media_rng <= 0:
        return False
    return float(rng.iloc[-1]) > media_rng * 1.12


def analisar(df: pd.DataFrame) -> dict:
    if df is None or len(df) < MIN_CANDLES:
        return {"signal": None, "price": 0.0, "reason": "Dados insuficientes"}

    work = df.copy()
    work.ta.rsi(length=14, append=True)
    curr = work.iloc[-1]
    volume_forte = _volume_strength(work)

    if curr["RSI_14"] < 30 and volume_forte:
        return {
            "signal": "COMPRA",
            "price": float(curr["close"]),
            "reason": "Reversao RSI + Forca de exaustao",
            "confidence": 85,
        }

    if curr["RSI_14"] > 70 and volume_forte:
        return {
            "signal": "VENDA",
            "price": float(curr["close"]),
            "reason": "Reversao RSI + Forca de exaustao",
            "confidence": 85,
        }

    return {"signal": None, "price": float(curr["close"]), "reason": "Aguardando confluencia RSI/Volume"}
