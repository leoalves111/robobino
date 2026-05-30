"""
Filtros de mestre para opções binárias M5 (Crypto IDX).
Aplicados após estratégia + SignalBrain — reduz sinais fracos e falsos positivos.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def min_candles_for_strategy(filename: str) -> int:
    from strategy_registry import STRATEGY_MIN_CANDLES

    return STRATEGY_MIN_CANDLES.get(filename, 30)


def warmup_result(df: pd.DataFrame, strategy_file: str, price: float = 0.0) -> dict[str, Any] | None:
    """Retorna dict de 'sem sinal' se ainda não há velas suficientes para a estratégia ativa."""
    need = min_candles_for_strategy(strategy_file)
    have = len(df) if df is not None else 0
    if have < need:
        if df is not None and len(df):
            price = float(df.iloc[-1]["close"])
        return {
            "signal": None,
            "price": price,
            "confidence": 0,
            "reason": f"Aquecimento M5: {have}/{need} velas (aguarde ~{(need - have) * 5} min)",
        }
    return None


def apply_binary_options_filters(
    result: dict[str, Any],
    market: Any,
    df: pd.DataFrame,
    strategy_file: str,
) -> dict[str, Any]:
    """Filtros finais alinhados a turbo M5 na Binomo."""
    signal = result.get("signal")
    if signal is None or df is None or len(df) < 2:
        return result

    out = dict(result)
    curr = df.iloc[-1]
    close = float(curr["close"])
    open_p = float(curr["open"])
    high = float(curr["high"])
    low = float(curr["low"])

    if close <= 0:
        return out

    body_pct = abs(close - open_p) / close
    range_pct = (high - low) / close

    # Vela indecisa (doji) — opção binária M5 precisa de direção clara
    if body_pct < 0.00004 and range_pct < 0.00015:
        out["signal"] = None
        out["reason"] = "Mestre M5: vela indecisa (doji) — sem entrada"
        out["blocked_signal"] = signal
        return out

    # Pavio dominante contra o sinal (rejeição)
    if signal == "COMPRA":
        upper_wick = high - max(open_p, close)
        if upper_wick / close > body_pct * 2.5 and upper_wick / close > 0.0002:
            out["signal"] = None
            out["reason"] = "Mestre M5: rejeicao na maxima (pavio superior)"
            out["blocked_signal"] = signal
            return out
    elif signal == "VENDA":
        lower_wick = min(open_p, close) - low
        if lower_wick / close > body_pct * 2.5 and lower_wick / close > 0.0002:
            out["signal"] = None
            out["reason"] = "Mestre M5: rejeicao na minima (pavio inferior)"
            out["blocked_signal"] = signal
            return out

    # RSI extremo sem estratégia de reversão/range
    mtype = getattr(getattr(market, "market_type", None), "value", None) or str(
        getattr(market, "market_type", "")
    )
    adx = float(getattr(market, "adx", 0) or 0)
    rsi = float(getattr(market, "rsi", 50) or 50)

    is_reversal = (
        "reversao" in strategy_file
        or "retracao" in strategy_file
        or "baixa_vol" in strategy_file
    )
    if not is_reversal:
        if signal == "COMPRA" and rsi > 72:
            out["signal"] = None
            out["reason"] = f"Mestre M5: COMPRA com RSI sobrecomprado ({rsi:.0f})"
            out["blocked_signal"] = signal
            return out
        if signal == "VENDA" and rsi < 28:
            out["signal"] = None
            out["reason"] = f"Mestre M5: VENDA com RSI sobrevendido ({rsi:.0f})"
            out["blocked_signal"] = signal
            return out

    # Tendência fraca + sinal direcional agressivo
    if (
        adx < 14
        and mtype not in ("BAIXA_VOLATILIDADE_LATERAL", "INDEFINIDO")
        and int(out.get("confidence") or 0) < 78
    ):
        out["signal"] = None
        out["reason"] = f"Mestre M5: ADX fraco ({adx:.0f}) para entrada direcional"
        out["blocked_signal"] = signal
        return out

    # Rompimento: exige confiança mínima mais alta
    if mtype == "ROMPIMENTO" and int(out.get("confidence") or 0) < 62:
        out["signal"] = None
        out["reason"] = "Mestre M5: rompimento sem confianca suficiente"
        out["blocked_signal"] = signal
        return out

    return out
