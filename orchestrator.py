"""
Orquestrador inteligente — pontua todas as estratégias e escolhe a melhor para o mercado atual.
Sem estratégia fixa; decisão a cada vela M5.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd
import pandas_ta as ta

from order_guard import OrderGuard
from strategy_manager import StrategyManager
from strategy_registry import FALLBACK_STRATEGY_FILE, STRATEGY_CATALOG, entry_for_file
from trade_log import OrchestratorLogState, log_orchestrator_change

logger = logging.getLogger(__name__)

LOOKBACK_CANDLES = 100
SWITCH_COOLDOWN_CANDLES = int(os.getenv("ORCHESTRATOR_SWITCH_COOLDOWN", "3"))
MIN_SCORE_TO_SWITCH = float(os.getenv("ORCH_MIN_SCORE", "22"))
SCORE_HYSTERESIS = float(os.getenv("ORCH_SCORE_HYSTERESIS", "4"))


class MarketType(str, Enum):
    ROMPIMENTO = "ROMPIMENTO"
    BAIXA_VOLATILIDADE_LATERAL = "BAIXA_VOLATILIDADE_LATERAL"
    ALTA_VOLATILIDADE_TENDENCIAL = "ALTA_VOLATILIDADE_TENDENCIAL"
    TENDENCIAL_MODERADA = "TENDENCIAL_MODERADA"
    INDEFINIDO = "INDEFINIDO"


@dataclass(frozen=True)
class MarketState:
    market_type: MarketType
    atr_pct: float
    adx: float
    bb_width_pct: float
    bb_squeeze: bool
    breakout_up: bool
    breakout_down: bool
    ema_bias: str
    rsi: float
    summary: str


@dataclass
class OrchestratorDecision:
    market: MarketState
    strategy_file: str
    strategy_name: str
    switched: bool
    score: float
    ranking: list[tuple[str, float]]
    blocked_reason: str | None = None


class StrategyOrchestrator:
    """Classifica mercado (ATR/ADX/BB/Donchian) e escolhe a estratégia com maior score."""

    def __init__(
        self,
        strategy_mgr: StrategyManager,
        order_guard: OrderGuard,
        compiled_dir: Path | str = "compiled_strategies",
    ) -> None:
        self.strategy_mgr = strategy_mgr
        self.order_guard = order_guard
        self.compiled_dir = Path(compiled_dir)
        self._current_file: str = FALLBACK_STRATEGY_FILE
        self._candles_since_switch: int = SWITCH_COOLDOWN_CANDLES
        self._cycle_index: int = 0
        self._last_decision: OrchestratorDecision | None = None
        self._orch_log = OrchestratorLogState()

        self._atr_low = float(os.getenv("ORCH_ATR_LOW_PCT", "0.00018"))
        self._atr_high = float(os.getenv("ORCH_ATR_HIGH_PCT", "0.00035"))
        self._adx_low = float(os.getenv("ORCH_ADX_LOW", "20"))
        self._adx_high = float(os.getenv("ORCH_ADX_HIGH", "25"))
        self._bb_squeeze = float(os.getenv("ORCH_BB_SQUEEZE_PCT", "0.004"))

    @property
    def current_strategy_file(self) -> str:
        return self._current_file

    @property
    def last_decision(self) -> OrchestratorDecision | None:
        return self._last_decision

    def analyze_market(self, df: pd.DataFrame) -> MarketState:
        if df is None or len(df) < 30:
            return MarketState(
                market_type=MarketType.INDEFINIDO,
                atr_pct=0.0,
                adx=0.0,
                bb_width_pct=0.0,
                bb_squeeze=False,
                breakout_up=False,
                breakout_down=False,
                ema_bias="neutro",
                rsi=50.0,
                summary="Dados insuficientes — aguardando velas M5",
            )

        work = df.tail(LOOKBACK_CANDLES).copy()
        work.ta.atr(length=14, append=True)
        work.ta.adx(length=14, append=True)
        work.ta.bbands(length=20, std=2.0, append=True)
        work.ta.ema(length=20, append=True)
        work.ta.ema(length=50, append=True)
        work.ta.rsi(length=14, append=True)

        curr = work.iloc[-1]
        close = float(curr["close"])
        atr_pct = float(curr.get("ATRr_14", 0) or 0) / close if close else 0.0
        adx = float(curr.get("ADX_14", 0) or 0)
        if pd.isna(adx):
            adx = 0.0
        rsi = float(curr.get("RSI_14", 50) or 50)
        if pd.isna(rsi):
            rsi = 50.0

        bbl = _find_col(work, "BBL_")
        bbm = _find_col(work, "BBM_")
        bbu = _find_col(work, "BBU_")
        bb_width_pct = 0.0
        bb_squeeze = False
        if bbl and bbu and bbm and float(curr[bbm]) > 0:
            bb_width_pct = (float(curr[bbu]) - float(curr[bbl])) / float(curr[bbm])
            bb_squeeze = bb_width_pct < self._bb_squeeze

        high_20 = work["high"].rolling(20).max()
        low_20 = work["low"].rolling(20).min()
        breakout_up = len(work) >= 22 and close > float(high_20.iloc[-2])
        breakout_down = len(work) >= 22 and close < float(low_20.iloc[-2])

        ema20 = float(curr.get("EMA_20", close))
        ema50 = float(curr.get("EMA_50", close))
        if ema20 > ema50 * 1.0005:
            ema_bias = "alta"
        elif ema20 < ema50 * 0.9995:
            ema_bias = "baixa"
        else:
            ema_bias = "neutro"

        low_vol = atr_pct < self._atr_low
        high_vol = atr_pct >= self._atr_high
        low_adx = adx < self._adx_low
        high_adx = adx >= self._adx_high

        atr_series = work["ATRr_14"] / work["close"]
        atr_median = float(atr_series.tail(50).median()) if len(atr_series) >= 20 else atr_pct
        if atr_median > 0:
            if atr_pct < atr_median * 0.85:
                low_vol = True
            if atr_pct > atr_median * 1.15:
                high_vol = True

        if breakout_up or breakout_down:
            mtype = MarketType.ROMPIMENTO
            summary = (
                f"Rompimento {'alta' if breakout_up else 'baixa'} | "
                f"ATR={atr_pct:.4%} ADX={adx:.0f} RSI={rsi:.0f}"
            )
        elif low_vol and low_adx:
            mtype = MarketType.BAIXA_VOLATILIDADE_LATERAL
            summary = (
                f"Range/consolidacao | ATR {atr_pct:.4%} ADX {adx:.0f} | "
                f"squeeze={bb_squeeze}"
            )
        elif high_vol and high_adx:
            mtype = MarketType.ALTA_VOLATILIDADE_TENDENCIAL
            summary = (
                f"Tendencia forte | ATR {atr_pct:.4%} ADX {adx:.0f} | viés {ema_bias}"
            )
        elif adx >= self._adx_low:
            mtype = MarketType.TENDENCIAL_MODERADA
            summary = f"Tendencia moderada | ADX {adx:.0f} | viés {ema_bias} RSI {rsi:.0f}"
        else:
            mtype = MarketType.INDEFINIDO
            summary = f"Misto | ATR {atr_pct:.4%} ADX {adx:.0f} RSI {rsi:.0f}"

        return MarketState(
            market_type=mtype,
            atr_pct=atr_pct,
            adx=adx,
            bb_width_pct=bb_width_pct,
            bb_squeeze=bb_squeeze,
            breakout_up=breakout_up,
            breakout_down=breakout_down,
            ema_bias=ema_bias,
            rsi=rsi,
            summary=summary,
        )

    def _score_strategy(self, filename: str, market: MarketState) -> float:
        if not (self.compiled_dir / filename).is_file():
            return 0.0

        entry = entry_for_file(filename)
        if entry is None:
            return 0.0

        score = 0.0
        m = market.market_type

        if filename == "breakout_donchian_m5.py":
            if m == MarketType.ROMPIMENTO:
                score += 48
            if market.breakout_up or market.breakout_down:
                score += 25
            if market.adx >= 18:
                score += 8

        elif filename == "baixa_volatilidade_m5.py":
            if m == MarketType.BAIXA_VOLATILIDADE_LATERAL:
                score += 45
            if market.adx < 22:
                score += 12
            if not market.bb_squeeze:
                score += 8
            if market.rsi < 35 or market.rsi > 65:
                score += 6

        elif filename == "retracao_m5.py":
            if m == MarketType.BAIXA_VOLATILIDADE_LATERAL:
                score += 38
            if market.bb_squeeze:
                score += 22
            if market.rsi < 35 or market.rsi > 65:
                score += 10

        elif filename == "reversao_rsi_volume_m5.py":
            if m == MarketType.BAIXA_VOLATILIDADE_LATERAL:
                score += 35
            if market.rsi < 32 or market.rsi > 68:
                score += 18
            if market.adx < 25:
                score += 8

        elif filename == "alta_volatilidade_m5.py":
            if m == MarketType.ALTA_VOLATILIDADE_TENDENCIAL:
                score += 46
            if market.adx >= 25:
                score += 15
            if market.atr_pct >= self._atr_high:
                score += 10

        elif filename == "fluxo_pullback_m5.py":
            if m in (MarketType.ALTA_VOLATILIDADE_TENDENCIAL, MarketType.TENDENCIAL_MODERADA):
                score += 40
            if market.ema_bias in ("alta", "baixa"):
                score += 12
            if 35 <= market.rsi <= 55 and market.ema_bias == "alta":
                score += 10
            if 45 <= market.rsi <= 65 and market.ema_bias == "baixa":
                score += 10

        elif filename == "tendencia_crossover_m5.py":
            if m == MarketType.TENDENCIAL_MODERADA:
                score += 42
            if market.adx >= 20:
                score += 10
            if market.ema_bias != "neutro":
                score += 8

        elif filename == "engolfo_volume_m5.py":
            if m == MarketType.ROMPIMENTO:
                score += 30
            if market.atr_pct >= self._atr_high:
                score += 15
            if market.adx >= 20:
                score += 8

        # INDEFINIDO: pontuação parcial por indicadores
        if m == MarketType.INDEFINIDO:
            if entry.role == "range" and market.adx < 22:
                score += 15
            if entry.role == "tendencia" and market.adx >= 18:
                score += 12
            if entry.role == "breakout" and (market.breakout_up or market.breakout_down):
                score += 20

        return score

    def _rank_strategies(self, market: MarketState) -> list[tuple[str, float]]:
        ranked = [
            (entry.filename, self._score_strategy(entry.filename, market))
            for entry in STRATEGY_CATALOG
        ]
        ranked = [(f, s) for f, s in ranked if s > 0]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def select_strategy(self, market: MarketState) -> tuple[str, float, list[tuple[str, float]]]:
        ranking = self._rank_strategies(market)
        if not ranking:
            return FALLBACK_STRATEGY_FILE, 0.0, []

        best_file, best_score = ranking[0]
        if best_score < MIN_SCORE_TO_SWITCH and ranking:
            # Mercado ambíguo — preferir pullback (equilibrado) se disponível
            for name, sc in ranking:
                if name == "fluxo_pullback_m5.py":
                    return name, sc, ranking
            return best_file, best_score, ranking

        current_score = self._score_strategy(self._current_file, market)
        if (
            best_file != self._current_file
            and best_score < current_score + SCORE_HYSTERESIS
        ):
            return self._current_file, current_score, ranking

        return best_file, best_score, ranking

    def prepare_cycle(self, df: pd.DataFrame) -> OrchestratorDecision:
        self._cycle_index += 1
        market = self.analyze_market(df)
        target_file, target_score, ranking = self.select_strategy(market)
        blocked: str | None = None
        switched = False

        if self.order_guard.order_status == "OPEN":
            target_file = self._current_file
            target_score = self._score_strategy(self._current_file, market)
            blocked = "Ordem/exposicao aberta — estrategia mantida"
        elif self._candles_since_switch < SWITCH_COOLDOWN_CANDLES:
            target_file = self._current_file
            target_score = self._score_strategy(self._current_file, market)
            remaining = SWITCH_COOLDOWN_CANDLES - self._candles_since_switch
            blocked = f"Cooldown pos-troca ({remaining} velas)"
        elif target_file != self._current_file:
            if not (self.compiled_dir / target_file).is_file():
                logger.warning("Estrategia %s ausente — mantendo %s", target_file, self._current_file)
                target_file = self._current_file
            else:
                switched = True

        if self._cycle_index == 1:
            if (self.compiled_dir / target_file).is_file():
                self.strategy_mgr.load_compiled(target_file)
                self._current_file = target_file
                self._candles_since_switch = 0
        elif switched:
            self.strategy_mgr.load_compiled(target_file)
            self._current_file = target_file
            self._candles_since_switch = 0
            logger.info(
                "Troca estrategia -> %s (%s) score=%.0f",
                target_file,
                _strategy_label(target_file),
                target_score,
            )
        else:
            self._candles_since_switch += 1
            self.strategy_mgr.load_compiled(self._current_file)

        entry = entry_for_file(self._current_file)
        decision = OrchestratorDecision(
            market=market,
            strategy_file=self._current_file,
            strategy_name=entry.name if entry else self._current_file,
            switched=switched,
            score=target_score,
            ranking=ranking[:3],
            blocked_reason=blocked,
        )
        self._last_decision = decision

        mtype_val = market.market_type.value
        if self._orch_log.should_log(
            mtype_val, self._current_file, switched=switched, first=self._cycle_index == 1
        ):
            log_orchestrator_change(
                mtype_val,
                self._current_file,
                target_score,
                switched=switched,
                summary=market.summary,
                top3=ranking[:3],
                blocked=blocked,
            )
            self._orch_log.update(mtype_val, self._current_file)
        elif blocked:
            logger.debug("ORCH bloqueado: %s", blocked)

        logger.debug(
            "ORCH ciclo | %s | %s | score=%.0f | Top3=%s",
            mtype_val,
            self._current_file,
            target_score,
            ranking[:3],
        )

        return decision

    def _align_signal_with_market(
        self, result: dict[str, Any], market: MarketState
    ) -> dict[str, Any]:
        signal = result.get("signal")
        if signal is None:
            return result

        out = dict(result)
        conf = int(out.get("confidence") or 0)

        if market.market_type == MarketType.ROMPIMENTO:
            if market.breakout_up and signal == "VENDA":
                out["signal"] = None
                out["reason"] = "Filtro: VENDA contra rompimento de alta"
            elif market.breakout_down and signal == "COMPRA":
                out["signal"] = None
                out["reason"] = "Filtro: COMPRA contra rompimento de baixa"
        elif market.market_type == MarketType.BAIXA_VOLATILIDADE_LATERAL and conf < 58:
            out["signal"] = None
            out["reason"] = "Filtro: confianca baixa em mercado lateral"
        elif market.market_type == MarketType.ALTA_VOLATILIDADE_TENDENCIAL:
            if market.ema_bias == "alta" and signal == "VENDA" and conf < 85:
                out["signal"] = None
                out["reason"] = "Filtro: VENDA contra tendencia de alta"
            elif market.ema_bias == "baixa" and signal == "COMPRA" and conf < 85:
                out["signal"] = None
                out["reason"] = "Filtro: COMPRA contra tendencia de baixa"

        return out

    def analyze(self, df: pd.DataFrame) -> dict[str, Any]:
        from binary_master import apply_binary_options_filters, warmup_result

        decision = self.prepare_cycle(df)
        warm = warmup_result(df, self._current_file)
        if warm is not None:
            warm["orchestrator"] = {
                "market_type": decision.market.market_type.value,
                "strategy_file": decision.strategy_file,
                "strategy_name": decision.strategy_name,
                "score": decision.score,
                "ranking": decision.ranking,
                "switched": decision.switched,
                "blocked": decision.blocked_reason,
                "summary": decision.market.summary,
            }
            return warm

        result = self.strategy_mgr.analyze(
            df, market_type=decision.market.market_type.value
        )
        result = self._align_signal_with_market(result, decision.market)
        result = apply_binary_options_filters(
            result, decision.market, df, self._current_file
        )
        result["orchestrator"] = {
            "market_type": decision.market.market_type.value,
            "strategy_file": decision.strategy_file,
            "strategy_name": decision.strategy_name,
            "score": decision.score,
            "ranking": decision.ranking,
            "switched": decision.switched,
            "blocked": decision.blocked_reason,
            "summary": decision.market.summary,
        }
        return result


def _find_col(df: pd.DataFrame, prefix: str) -> str | None:
    for col in df.columns:
        if col.startswith(prefix):
            return col
    return None


def _strategy_label(filename: str) -> str:
    entry = entry_for_file(filename)
    return entry.name if entry else filename
