import pandas as pd

import pandas_ta as ta



STRATEGY_NAME = "Baixa Volatilidade M5 (Range)"

MIN_CANDLES = 30





def _col(df: pd.DataFrame, prefix: str) -> str | None:

    for c in df.columns:

        if c.startswith(prefix):

            return c

    return None





def analisar(df: pd.DataFrame) -> dict:

    if df is None or len(df) < MIN_CANDLES:

        return {"signal": None, "price": 0.0, "reason": "Dados insuficientes"}



    work = df.copy()

    work.ta.bbands(length=20, std=2.0, append=True)

    work.ta.stoch(k=14, d=3, append=True)



    curr = work.iloc[-1]

    bbl = _col(work, "BBL_")

    bbu = _col(work, "BBU_")

    stoch_k = _col(work, "STOCHk_")



    if not bbl or not bbu or not stoch_k:

        return {"signal": None, "price": float(curr["close"]), "reason": "Indicadores indisponiveis"}



    if curr["low"] <= curr[bbl] and curr[stoch_k] < 20:

        return {

            "signal": "COMPRA",

            "price": float(curr["close"]),

            "reason": "Range: Tocou BBL + Estocastico Sobrevendido",

            "confidence": 75,

        }



    if curr["high"] >= curr[bbu] and curr[stoch_k] > 80:

        return {

            "signal": "VENDA",

            "price": float(curr["close"]),

            "reason": "Range: Tocou BBU + Estocastico Sobrecomprado",

            "confidence": 75,

        }



    return {"signal": None, "price": float(curr["close"]), "reason": "Aguardando toque em bandas"}

