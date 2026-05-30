"""
Camada de inteligência — contexto de mercado, score de confiança e filtros adaptativos.
100% local, sem APIs externas.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import pandas_ta as ta


@dataclass
class MarketContext:
    regime: str = "LATERAL"
    rsi: float = 50.0
    adx: float = 0.0
    atr_pct: float = 0.0
    ema_bias: str = "neutro"
    momentum: str = "neutro"
    summary: str = "Analisando..."


@dataclass
class BrainState:
    last_signal: str | None = None
    last_signal_index: int = -1
    candle_index: int = 0


class SignalBrain:
    """
    Enriquece sinais das estratégias com:
    - Contexto de mercado (tendência, volatilidade, momentum)
    - Score de confiança 0–100
    - Filtros anti-ruído (ADX, cooldown, contra-tendência)
    """

    def __init__(
        self,
        min_confidence: int | None = None,
        cooldown_candles: int | None = None,
        min_adx: float | None = None,
    ) -> None:
        self.min_confidence = min_confidence or int(os.getenv("MIN_CONFIDENCE", "52"))
        self.cooldown_candles = cooldown_candles or int(os.getenv("SIGNAL_COOLDOWN_CANDLES", "2"))
        self.min_adx = min_adx or float(os.getenv("MIN_ADX", "14"))
        self._state = BrainState()
        self.last_context = MarketContext()
        self.last_analysis: dict[str, Any] = {}

    def process(self, result: dict[str, Any], df: pd.DataFrame) -> dict[str, Any]:
        self._state.candle_index += 1
        ctx = self._compute_context(df)
        self.last_context = ctx

        enriched = dict(result)
        enriched["context"] = ctx
        enriched["confidence"] = self._resolve_confidence(enriched, df, ctx)

        signal = enriched.get("signal")
        reason = str(enriched.get("reason", ""))
        market_type = str(enriched.get("_market_type") or "")

        if signal is None:
            enriched["reason"] = reason or ctx.summary
            self.last_analysis = enriched
            return enriched

        block_reason = self._apply_filters(
            signal, enriched["confidence"], ctx, market_type=market_type
        )
        if block_reason:
            enriched["signal"] = None
            enriched["reason"] = block_reason
            enriched["blocked_signal"] = signal
            self.last_analysis = enriched
            return enriched

        self._state.last_signal = signal
        self._state.last_signal_index = self._state.candle_index
        enriched["reason"] = f"[{enriched['confidence']}%] {reason}"
        self.last_analysis = enriched
        return enriched

    def _apply_filters(
        self,
        signal: str,
        confidence: int,
        ctx: MarketContext,
        *,
        market_type: str = "",
    ) -> str | None:
        range_market = market_type == "BAIXA_VOLATILIDADE_LATERAL" or ctx.regime == "LATERAL"
        trend_market = market_type in (
            "ALTA_VOLATILIDADE_TENDENCIAL",
            "TENDENCIAL_MODERADA",
            "ROMPIMENTO",
        )

        # Range: osciladores — não exige ADX alto
        if not range_market and ctx.adx < self.min_adx and confidence < 80:
            return f"Mercado sem tendência (ADX {ctx.adx:.0f}) — sinal ignorado"

        # Tendência: exige volatilidade mínima
        min_atr = 0.00015 if range_market else 0.0002
        if ctx.atr_pct < min_atr:
            return "Volatilidade insuficiente — aguardando movimento"

        if signal == "COMPRA" and ctx.regime == "BAIXA" and confidence < 75 and trend_market:
            return f"COMPRA contra tendência de baixa ({confidence}% confiança)"

        if signal == "VENDA" and ctx.regime == "ALTA" and confidence < 75 and trend_market:
            return f"VENDA contra tendência de alta ({confidence}% confiança)"

        if self._state.last_signal == signal:
            elapsed = self._state.candle_index - self._state.last_signal_index
            if elapsed <= self.cooldown_candles and confidence < 85:
                return f"Cooldown ativo — {signal} recente ({elapsed} velas)"

        min_conf = self.min_confidence
        if range_market and confidence >= 58:
            min_conf = min(min_conf, 58)

        if confidence < min_conf:
            return f"Confiança insuficiente ({confidence}% < {min_conf}%)"

        return None

    def _resolve_confidence(
        self, result: dict[str, Any], df: pd.DataFrame, ctx: MarketContext
    ) -> int:
        raw = result.get("confidence")
        if raw is not None:
            try:
                return max(0, min(100, int(raw)))
            except (TypeError, ValueError):
                pass

        signal = result.get("signal")
        if signal is None:
            return 0
        return self._score_signal(signal, df, ctx)

    def _score_signal(self, signal: str, df: pd.DataFrame, ctx: MarketContext) -> int:
        score = 50
        if signal == "COMPRA":
            if ctx.regime == "ALTA":
                score += 18
            elif ctx.regime == "LATERAL":
                score += 8
            else:
                score -= 12
            if ctx.rsi < 35:
                score += 12
            elif ctx.rsi < 45:
                score += 6
            if ctx.ema_bias == "alta":
                score += 10
            if ctx.momentum == "alta":
                score += 8
        elif signal == "VENDA":
            if ctx.regime == "BAIXA":
                score += 18
            elif ctx.regime == "LATERAL":
                score += 8
            else:
                score -= 12
            if ctx.rsi > 65:
                score += 12
            elif ctx.rsi > 55:
                score += 6
            if ctx.ema_bias == "baixa":
                score += 10
            if ctx.momentum == "baixa":
                score += 8

        if ctx.adx >= 25:
            score += 10
        elif ctx.adx >= self.min_adx:
            score += 5

        if ctx.atr_pct >= 0.001:
            score += 5

        return max(0, min(100, score))

    def _compute_context(self, df: pd.DataFrame) -> MarketContext:
        if df is None or len(df) < 30:
            return MarketContext(summary="Dados insuficientes para contexto")

        work = df.copy()
        close = work["close"]
        work["ema9"] = ta.ema(close, length=9)
        work["ema21"] = ta.ema(close, length=21)
        work["ema50"] = ta.sma(close, length=50)
        work["rsi"] = ta.rsi(close, length=14)
        work["atr"] = ta.atr(work["high"], work["low"], close, length=14)

        adx_df = ta.adx(work["high"], work["low"], close, length=14)
        adx_col = "ADX_14" if adx_df is not None and "ADX_14" in adx_df.columns else None

        row = work.iloc[-1]
        prev = work.iloc[-2]

        rsi = float(row["rsi"]) if pd.notna(row.get("rsi")) else 50.0
        adx = float(adx_df[adx_col].iloc[-1]) if adx_col and pd.notna(adx_df[adx_col].iloc[-1]) else 0.0
        atr = float(row["atr"]) if pd.notna(row.get("atr")) else 0.0
        price = float(row["close"])
        atr_pct = atr / price if price else 0.0

        ema9 = float(row["ema9"]) if pd.notna(row.get("ema9")) else price
        ema21 = float(row["ema21"]) if pd.notna(row.get("ema21")) else price
        ema50 = float(row["ema50"]) if pd.notna(row.get("ema50")) else price

        if ema9 > ema21 > ema50:
            ema_bias = "alta"
        elif ema9 < ema21 < ema50:
            ema_bias = "baixa"
        else:
            ema_bias = "neutro"

        if adx < self.min_adx:
            regime = "LATERAL"
        elif ema_bias == "alta" and price > ema21:
            regime = "ALTA"
        elif ema_bias == "baixa" and price < ema21:
            regime = "BAIXA"
        else:
            regime = "LATERAL"

        delta = price - float(prev["close"])
        if delta > 0 and rsi > 50:
            momentum = "alta"
        elif delta < 0 and rsi < 50:
            momentum = "baixa"
        else:
            momentum = "neutro"

        summary = (
            f"{regime} | RSI {rsi:.0f} | ADX {adx:.0f} | "
            f"Vol {atr_pct * 100:.3f}%"
        )

        return MarketContext(
            regime=regime,
            rsi=rsi,
            adx=adx,
            atr_pct=atr_pct,
            ema_bias=ema_bias,
            momentum=momentum,
            summary=summary,
        )
