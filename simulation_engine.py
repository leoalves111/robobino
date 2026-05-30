"""
Motor de simulação — velas sintéticas aceleradas, sem conexão Binomo.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from data_engine import Candle, CandleAggregator, DataEngineState
from simulation_data import generate_seed_candles, next_sim_price

logger = logging.getLogger(__name__)

SIM_ASSET_RIC = "Z-CRY/IDX (SIM)"


@dataclass
class SimulationConfig:
    candle_interval_sec: int = 8
    seed_candles: int = 80
    base_price: float = 641.0
    demo_balance: float = 10_000.0


class SimulationEngine:
    """Interface compatível com DataEngine para o loop principal."""

    def __init__(
        self,
        asset_name: str = "Crypto IDX",
        config: SimulationConfig | None = None,
    ) -> None:
        self.config = config or SimulationConfig()
        self.asset_name = asset_name
        self.asset_ric = SIM_ASSET_RIC
        self.timeframe_seconds = self.config.candle_interval_sec

        self.state = DataEngineState()
        self.aggregator = CandleAggregator(interval_seconds=self.timeframe_seconds)
        self._candle_queue: asyncio.Queue[Candle] = asyncio.Queue()
        self._running = False
        self._rng = np.random.default_rng(7)

        self.aggregator.on_candle_close(self._handle_closed_candle)

    def _handle_closed_candle(self, candle: Candle) -> None:
        self.state.candles_count = len(self.aggregator.closed_candles)
        try:
            self._candle_queue.put_nowait(candle)
        except asyncio.QueueFull:
            pass

    async def connect(self) -> None:
        candles = generate_seed_candles(
            count=self.config.seed_candles,
            base=self.config.base_price,
            interval_seconds=self.timeframe_seconds,
        )
        self.aggregator.seed(candles)
        self.state.candles_count = len(candles)
        self.state.history_loaded = True
        self.state.last_price = candles[-1].close
        self.state.last_message_at = datetime.now(timezone.utc)
        self.state.price_ticks = 1
        self.state.balance = self.config.demo_balance
        self.state.connected = True
        self.state.status_message = (
            f"Simulação — velas a cada {self.timeframe_seconds}s (dados sintéticos)"
        )
        if candles:
            self.aggregator.update(candles[-1].close)
        logger.info("Simulação iniciada com %d velas seed", len(candles))

    def _apply_price(self, price: float) -> None:
        self.state.last_price = price
        self.state.last_message_at = datetime.now(timezone.utc)
        self.state.price_ticks += 1
        closed = self.aggregator.update(price)
        if closed:
            self.state.candles_count = len(self.aggregator.closed_candles)

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                if self.state.last_price is not None:
                    price = next_sim_price(self.state.last_price, self._rng)
                    self._apply_price(price)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break

    async def clock_loop(self, interval: float = 1.0) -> None:
        while self._running:
            try:
                if self.state.last_price is not None:
                    self._apply_price(self.state.last_price)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Sim clock falhou: %s", exc)
            await asyncio.sleep(interval)

    async def rate_poll_loop(self, interval: float = 2.0) -> None:
        """Compatibilidade com DataEngine — no-op na simulação."""
        while self._running:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def wait_closed_candle(self, timeout: float = 310.0) -> Optional[Candle]:
        try:
            return await asyncio.wait_for(self._candle_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def get_dataframe(self):
        return self.aggregator.to_dataframe()

    def seconds_to_candle_close(self) -> int:
        now = datetime.now(timezone.utc)
        epoch = int(now.timestamp())
        elapsed = epoch % self.timeframe_seconds
        return self.timeframe_seconds - elapsed

    async def refresh_balance(self) -> Optional[float]:
        return self.state.balance

    async def stop(self) -> None:
        self._running = False
        self.state.connected = False
        self.state.status_message = "Simulação encerrada"

    async def test_connection(self) -> dict:
        await self.connect()
        return {
            "connected": True,
            "asset_ric": self.asset_ric,
            "balance": self.state.balance,
            "error": None,
        }
