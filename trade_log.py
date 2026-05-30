"""
Logs operacionais limpos para pm2 — sinal em destaque, heartbeat mínimo.
Detalhe completo fica em DEBUG no arquivo logs/signal_generator.log.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("binomo.trade")
_detail = logging.getLogger("orchestrator")


def _short_strategy(filename: str) -> str:
    return filename.replace("_m5.py", "").replace(".py", "")


def log_signal(
    direction: str,
    confidence: int,
    price: float,
    reason: str,
    *,
    strategy_file: str = "",
    market_type: str = "",
) -> None:
    """Sinal de entrada — linhas únicas e fáceis de localizar no pm2 logs."""
    strat = _short_strategy(strategy_file) if strategy_file else "—"
    mercado = market_type or "—"
    bar = "=" * 52
    log.critical(bar)
    log.critical(
        ">>> SINAL %s <<<  %d%%  @ %.5f  |  %s  |  %s",
        direction,
        confidence,
        price,
        strat,
        mercado,
    )
    if reason:
        log.critical("    %s", reason[:120])
    log.critical(bar)


def log_candle_no_signal(
    candle_time: str,
    *,
    market_type: str = "",
    strategy_file: str = "",
    reason: str = "",
) -> None:
    """Uma linha por vela M5 fechada sem sinal."""
    strat = _short_strategy(strategy_file) if strategy_file else "—"
    mercado = _market_short(market_type)
    motivo = _short_reason(reason)
    log.info("M5 %s | — | %s | %s | %s", candle_time, mercado, strat, motivo)


def log_candle_closed(tag: str, candle_time: str, close: float, velas: int) -> None:
    _detail.debug(
        "[%s] Vela fechada @ %s close=%.5f velas=%d",
        tag,
        candle_time,
        close,
        velas,
    )


def log_orchestrator_change(
    market_type: str,
    strategy_file: str,
    score: float,
    *,
    switched: bool = False,
    summary: str = "",
    top3: list[tuple[str, float]] | None = None,
    blocked: str | None = None,
) -> None:
    """Orquestrador — só quando mercado ou estratégia mudam (não a cada vela)."""
    strat = _short_strategy(strategy_file)
    mercado = _market_short(market_type)
    if switched:
        log.info("ORCH | troca -> %s | %s | score=%.0f", strat, mercado, score)
    else:
        log.info("ORCH | %s | %s | score=%.0f", mercado, strat, score)
    if blocked:
        log.info("ORCH | %s", blocked)
    if summary or top3:
        top = " ".join(f"{_short_strategy(f)}({s:.0f})" for f, s in (top3 or [])[:3])
        _detail.debug("ORCH detalhe | %s | Top3: %s", summary, top or "n/a")


def log_heartbeat(
    *,
    connected: bool,
    price: float | None,
    velas: int,
    order_status: str = "NONE",
) -> None:
    """Heartbeat enxuto — preço e estado, sem repetir filtros."""
    px = f"{price:.2f}" if price else "—"
    ok = "OK" if connected else "OFF"
    ordem = "" if order_status in ("NONE", "") else f" | ordem={order_status}"
    log.info("♥ %s | velas=%d | %s%s", px, velas, ok, ordem)


def _market_short(market_type: str) -> str:
    m = (market_type or "").upper()
    return {
        "ALTA_VOLATILIDADE_TENDENCIAL": "tendencia+",
        "TENDENCIAL_MODERADA": "tendencia",
        "BAIXA_VOLATILIDADE_LATERAL": "lateral",
        "ROMPIMENTO": "rompimento",
        "INDEFINIDO": "misto",
    }.get(m, m[:12] or "—")


def _short_reason(reason: str) -> str:
    r = (reason or "aguardando").strip()
    for prefix in ("[",):
        if r.startswith(prefix) and "]" in r:
            r = r.split("]", 1)[-1].strip()
    replacements = (
        ("Filtro: ", ""),
        ("Mestre M5: ", ""),
        ("Mercado sem tendência (ADX ", "ADX baixo ("),
        ("Volatilidade insuficiente — aguardando movimento", "vol baixa"),
        ("Volatilidade insuficiente", "vol baixa"),
        ("Aquecimento M5:", "aquecimento"),
    )
    for old, new in replacements:
        r = r.replace(old, new)
    return r[:55] if len(r) > 55 else r


class HeartbeatTracker:
    """Evita heartbeat repetitivo quando nada mudou."""

    def __init__(self) -> None:
        self._interval = int(os.getenv("STATUS_INTERVAL_SEC", "120"))
        self._min_price_delta = float(os.getenv("STATUS_PRICE_DELTA", "0.05"))
        self._last_price: float | None = None
        self._last_connected: bool | None = None
        self._ticks = 0

    def should_log(self, *, connected: bool, price: float | None) -> bool:
        self._ticks += 1
        if self._last_connected is not None and connected != self._last_connected:
            return True
        if price is not None and self._last_price is not None:
            if abs(price - self._last_price) >= self._min_price_delta:
                return True
        if self._last_price is None:
            return True
        if self._ticks * self._interval >= self._interval * 5:
            self._ticks = 0
            return True
        return False

    def mark(self, *, connected: bool, price: float | None) -> None:
        self._last_connected = connected
        if price is not None:
            self._last_price = price


class OrchestratorLogState:
    """Registra último mercado/estratégia logados."""

    def __init__(self) -> None:
        self.market_type: str = ""
        self.strategy_file: str = ""

    def should_log(self, market_type: str, strategy_file: str, *, switched: bool, first: bool) -> bool:
        if first or switched:
            return True
        if market_type != self.market_type or strategy_file != self.strategy_file:
            return True
        return False

    def update(self, market_type: str, strategy_file: str) -> None:
        self.market_type = market_type
        self.strategy_file = strategy_file
