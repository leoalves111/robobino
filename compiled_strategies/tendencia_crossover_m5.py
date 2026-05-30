import pandas as pd

import pandas_ta as ta



STRATEGY_NAME = "Crossover Tendencia M5"

MIN_CANDLES = 55





def analisar(df: pd.DataFrame) -> dict:

    if df is None or len(df) < MIN_CANDLES:

        return {"signal": None, "price": 0.0, "reason": "Dados insuficientes"}



    work = df.copy()

    work.ta.ema(length=9, append=True)

    work.ta.ema(length=21, append=True)

    work.ta.ema(length=50, append=True)



    curr = work.iloc[-1]

    prev = work.iloc[-2]



    if prev["EMA_9"] < prev["EMA_21"] and curr["EMA_9"] > curr["EMA_21"] and curr["close"] > curr["EMA_50"]:

        return {

            "signal": "COMPRA",

            "price": float(curr["close"]),

            "reason": "Crossover Alta + Filtro Tendencia EMA50",

            "confidence": 80,

        }



    if prev["EMA_9"] > prev["EMA_21"] and curr["EMA_9"] < curr["EMA_21"] and curr["close"] < curr["EMA_50"]:

        return {

            "signal": "VENDA",

            "price": float(curr["close"]),

            "reason": "Crossover Baixa + Filtro Tendencia EMA50",

            "confidence": 80,

        }



    return {"signal": None, "price": float(curr["close"]), "reason": "Sem sinal de crossover"}

