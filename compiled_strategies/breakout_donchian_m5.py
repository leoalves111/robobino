import pandas as pd
import pandas_ta as ta

STRATEGY_NAME = "Breakout Donchian M5"
MIN_CANDLES = 25

def analisar(df: pd.DataFrame) -> dict:
    if df is None or len(df) < MIN_CANDLES:
        return {"signal": None, "price": 0.0, "reason": "Dados insuficientes"}

    # Calculando os canais de Donchian
    # (Alta dos ultimos 20 periodos e Baixa dos ultimos 20 periodos)
    high_20 = df['high'].rolling(window=20).max()
    low_20 = df['low'].rolling(window=20).min()
    
    curr = df.iloc[-1]
    
    # COMPRA: Rompeu a máxima das últimas 20 velas
    if curr['close'] > high_20.iloc[-2]:
        return {
            "signal": "COMPRA",
            "price": float(curr['close']),
            "reason": "Breakout: Rompimento de maxima (Donchian)",
            "confidence": 80
        }

    # VENDA: Rompeu a mínima das últimas 20 velas
    if curr['close'] < low_20.iloc[-2]:
        return {
            "signal": "VENDA",
            "price": float(curr['close']),
            "reason": "Breakout: Rompimento de minima (Donchian)",
            "confidence": 80
        }

    return {"signal": None, "price": float(curr['close']), "reason": "Aguardando rompimento"}