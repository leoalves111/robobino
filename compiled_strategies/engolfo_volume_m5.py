import pandas as pd

STRATEGY_NAME = "Engolfo com Volume M5"
MIN_CANDLES = 20


def _volume_strength(work: pd.DataFrame) -> bool:
    """Volume real ou amplitude da vela (Crypto IDX costuma ter volume=0)."""
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
    curr = work.iloc[-1]
    prev = work.iloc[-2]
    volume_alto = _volume_strength(work)

    is_engolfo_alta = (
        prev["close"] < prev["open"]
        and curr["close"] > prev["open"]
        and curr["open"] < prev["close"]
    )
    is_engolfo_baixa = (
        prev["close"] > prev["open"]
        and curr["close"] < prev["open"]
        and curr["open"] > prev["close"]
    )

    if is_engolfo_alta and volume_alto:
        return {
            "signal": "COMPRA",
            "price": float(curr["close"]),
            "reason": "Engolfo Alta + Forca (volume/amplitude)",
            "confidence": 85,
        }

    if is_engolfo_baixa and volume_alto:
        return {
            "signal": "VENDA",
            "price": float(curr["close"]),
            "reason": "Engolfo Baixa + Forca (volume/amplitude)",
            "confidence": 85,
        }

    return {"signal": None, "price": float(curr["close"]), "reason": "Aguardando sinal de Price Action"}
