import pandas as pd
import pandas_ta as ta

STRATEGY_NAME = "Fluxo & Pullback M5 Pro"
MIN_CANDLES = 30


def analisar(df: pd.DataFrame) -> dict:
    """
    Estratégia: Fluxo & Pullback M5 Pro
    """
    if df is None or len(df) < MIN_CANDLES:
        return {
            "signal": None,
            "price": float(df.iloc[-1]["close"]) if df is not None and len(df) else 0.0,
            "reason": "Dados insuficientes",
        }

    # 1. Calculando os indicadores
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True)

    # 2. Pegando os valores mais recentes (candle atual e anterior)
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    atr_pct = float(curr["ATRr_14"]) / float(curr["close"])
    if atr_pct < 0.00015:
        return {"signal": None, "price": float(curr["close"]), "reason": "Filtro: Volatilidade muito baixa"}

    # Definição de Tendência
    tendencia_alta = curr['EMA_20'] > curr['EMA_50']
    tendencia_baixa = curr['EMA_20'] < curr['EMA_50']

    # 3. Lógica de COMPRA
    # Tendência de alta + RSI abaixo de 40 + RSI virando para cima
    if tendencia_alta and curr['RSI_14'] < 40 and curr['RSI_14'] > prev['RSI_14']:
        conf = 60
        if curr['RSI_14'] < 35:
            conf += 10
        if curr['EMA_20'] > curr['EMA_50'] * 1.001:
            conf += 10
        return {
            "signal": "COMPRA",
            "price": float(curr['close']),
            "reason": "Tendencia Alta + Pullback RSI",
            "confidence": min(90, conf),
        }

    # 4. Lógica de VENDA
    if tendencia_baixa and curr['RSI_14'] > 60 and curr['RSI_14'] < prev['RSI_14']:
        conf = 60
        if curr['RSI_14'] > 65:
            conf += 10
        if curr['EMA_20'] < curr['EMA_50'] * 0.999:
            conf += 10
        return {
            "signal": "VENDA",
            "price": float(curr['close']),
            "reason": "Tendencia Baixa + Pullback RSI",
            "confidence": min(90, conf),
        }

    return {"signal": None, "price": float(curr['close']), "reason": "Aguardando setup"}