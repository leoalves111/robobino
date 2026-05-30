"""
Ponto de entrada — Gerador de Sinais Binomo (READ-ONLY).
Validação local + menu de estratégias + painel Live.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

from alert_system import Dashboard, DashboardState
from api_connection import classify_connection_error, safe_get_candles
from data_engine import DataEngine
from mode_selector import exit_if_missing_auth_for_normal, prompt_run_mode
from simulation_engine import SimulationEngine
from strategy_manager import StrategyManager
from strategy_selector import prompt_strategy_selection
from strategy_validator import (
    StrategyValidationError,
    exit_on_validation_error,
    run_startup_validation,
)

load_dotenv()

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger(__name__)


class MarketEngine(Protocol):
    state: Any
    asset_ric: str
    timeframe_seconds: int

    def get_dataframe(self): ...
    def seconds_to_candle_close(self) -> int: ...
    async def wait_closed_candle(self, timeout: float = 310.0): ...
    async def run(self) -> None: ...
    async def rate_poll_loop(self, interval: float = 2.0) -> None: ...
    async def clock_loop(self, interval: float = 1.0) -> None: ...
    async def stop(self) -> None: ...
    async def test_connection(self) -> dict: ...
    async def refresh_balance(self): ...


def setup_logging() -> None:
    """Console: apenas CRITICAL. Arquivo: WARNING+. BinomoAPI silenciado."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(LOG_DIR / "signal_generator.log", encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.CRITICAL)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    logging.getLogger("BinomoAPI").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def get_config() -> dict[str, str]:
    return {
        "AUTH_TOKEN": os.getenv("AUTH_TOKEN", "").strip(),
        "DEVICE_ID": os.getenv("DEVICE_ID", "").strip(),
        "DEMO_MODE": os.getenv("DEMO_MODE", "true"),
        "ASSET_NAME": os.getenv("ASSET_NAME", "Crypto IDX"),
        "TIMEFRAME_SECONDS": os.getenv("TIMEFRAME_SECONDS", "300"),
        "STRATEGIES_DIR": os.getenv("STRATEGIES_DIR", "strategies"),
        "COMPILED_DIR": os.getenv("COMPILED_DIR", "compiled_strategies"),
        "MIN_CONFIDENCE": os.getenv("MIN_CONFIDENCE", "52"),
        "SIGNAL_COOLDOWN_CANDLES": os.getenv("SIGNAL_COOLDOWN_CANDLES", "2"),
        "MIN_ADX": os.getenv("MIN_ADX", "14"),
    }


def sync_dashboard(
    state: DashboardState,
    engine: MarketEngine,
    strategy: StrategyManager,
) -> None:
    state.connected = engine.state.connected
    state.price = engine.state.last_price
    state.balance = engine.state.balance
    state.candles_count = engine.state.candles_count
    state.candle_remaining_sec = engine.seconds_to_candle_close()
    state.candle_total_sec = engine.timeframe_seconds
    state.asset_ric = engine.asset_ric
    state.status_message = engine.state.status_message
    state.strategy_name = strategy.name
    state.strategy_file = strategy.module_path.name if strategy.module_path else "—"
    state.price_ticks = engine.state.price_ticks
    state.history_loaded = engine.state.history_loaded
    state.cache_status = getattr(engine.state, "cache_status", "—")
    if engine.state.last_message_at:
        age = (datetime.now(timezone.utc) - engine.state.last_message_at).total_seconds()
        state.price_age_sec = int(age)
    else:
        state.price_age_sec = -1

    ctx = strategy.market_context
    state.market_regime = ctx.regime
    state.market_rsi = ctx.rsi
    state.market_adx = ctx.adx
    state.market_summary = ctx.summary

    analysis = strategy.last_analysis
    if analysis:
        state.last_confidence = int(analysis.get("confidence") or 0)
        state.analysis_hint = str(analysis.get("reason") or "Aguardando sinal...")
    elif state.last_signal_direction is None:
        state.analysis_hint = "Aguardando sinal..."


async def dashboard_loop(
    dashboard: Dashboard,
    engine: MarketEngine,
    strategy: StrategyManager,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        sync_dashboard(dashboard.state, engine, strategy)
        dashboard.refresh()
        await asyncio.sleep(1)


async def balance_loop(engine: MarketEngine, stop_event: asyncio.Event, interval: int = 60) -> None:
    while not stop_event.is_set():
        await asyncio.sleep(interval)
        await engine.refresh_balance()


async def ensure_engine_connected(engine: MarketEngine) -> bool:
    """Garante API ativa antes de operações de mercado (modo normal)."""
    if not isinstance(engine, DataEngine):
        return True
    if engine.is_connected():
        return True
    logger.warning("Conexão perdida — tentando reconectar...")
    try:
        await engine.connect()
        return engine.is_connected()
    except Exception as exc:
        logger.error("%s", classify_connection_error(exc))
        engine.state.status_message = classify_connection_error(exc)
        engine.state.connected = False
        return False


async def analysis_loop(
    dashboard: Dashboard,
    engine: MarketEngine,
    strategy: StrategyManager,
    stop_event: asyncio.Event,
    *,
    simulation: bool = False,
) -> None:
    last_bucket: datetime | None = None

    while not stop_event.is_set():
        try:
            candle = await asyncio.wait_for(engine.wait_closed_candle(), timeout=2.0)
        except asyncio.TimeoutError:
            continue
        except (ConnectionError, OSError) as exc:
            logger.warning("Rede instável no loop de análise: %s", exc)
            await asyncio.sleep(3)
            continue

        if candle is None or last_bucket == candle.timestamp:
            continue
        last_bucket = candle.timestamp

        try:
            if not simulation and not await ensure_engine_connected(engine):
                dashboard.state.analysis_hint = "Reconectando à Binomo..."
                await asyncio.sleep(5)
                continue

            tag = "SIM" if simulation else "LIVE"
            logging.warning(
                "[%s] Vela fechada @ %s | close=%.5f | velas=%d | ticks=%d",
                tag,
                candle.timestamp.strftime("%H:%M"),
                candle.close,
                engine.state.candles_count,
                engine.state.price_ticks,
            )

            df = await safe_get_candles(engine)
            result = strategy.analyze(df)

            if result.get("signal"):
                dashboard.register_signal(
                    direction=result["signal"],
                    price=result.get("price", candle.close),
                    confidence=int(result.get("confidence") or 0),
                    reason=str(result.get("reason", "")),
                )
            else:
                dashboard.state.analysis_hint = str(
                    result.get("reason") or "Aguardando sinal..."
                )
        except Exception as exc:
            logger.exception("Erro temporário na análise de mercado: %s", exc)
            dashboard.state.analysis_hint = f"Erro temporário — {exc}"
            await asyncio.sleep(2)


async def run_monitor_loop(
    engine: MarketEngine,
    config: dict[str, str],
    strategy_mgr: StrategyManager,
    *,
    mode: str = "normal",
) -> None:
    setup_logging()
    simulation = mode == "simulation"

    state = DashboardState(asset_name=config["ASSET_NAME"])
    state.mode = "SIMULAÇÃO" if simulation else "READ-ONLY"
    dashboard = Dashboard(state)
    state.strategy_name = strategy_mgr.name
    state.strategy_file = strategy_mgr.module_path.name if strategy_mgr.module_path else "—"

    try:
        test = await engine.test_connection()
    except Exception as exc:
        msg = classify_connection_error(exc)
        logging.critical(msg)
        print(f"\n[ERRO] {msg}\n", file=sys.stderr)
        sys.exit(1)

    if test.get("error") or not test.get("connected"):
        msg = test.get("error") or "Falha na conexão — não foi possível autenticar"
        logging.critical(msg)
        print(f"\n[ERRO] {msg}\n", file=sys.stderr)
        sys.exit(1)

    state.connected = True
    state.balance = test.get("balance")
    state.asset_ric = test.get("asset_ric", engine.asset_ric)
    if simulation:
        state.status_message = engine.state.status_message
    else:
        state.status_message = "Monitorando Crypto IDX M5"

    stop_event = asyncio.Event()

    def _shutdown() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _shutdown)
            except NotImplementedError:
                pass

    sync_dashboard(state, engine, strategy_mgr)

    if simulation:
        bootstrap = strategy_mgr.analyze(await safe_get_candles(engine))
        state.analysis_hint = str(bootstrap.get("reason") or "Aguardando sinal...")
        if bootstrap.get("signal"):
            dashboard.register_signal(
                direction=bootstrap["signal"],
                price=bootstrap.get("price", engine.state.last_price or 0),
                confidence=int(bootstrap.get("confidence") or 0),
                reason=str(bootstrap.get("reason", "")),
            )

    with dashboard.start():
        tasks = [
            asyncio.create_task(engine.run(), name="stream"),
            asyncio.create_task(engine.rate_poll_loop(), name="rate_poll"),
            asyncio.create_task(engine.clock_loop(), name="clock"),
            asyncio.create_task(
                dashboard_loop(dashboard, engine, strategy_mgr, stop_event), name="ui"
            ),
            asyncio.create_task(
                analysis_loop(
                    dashboard,
                    engine,
                    strategy_mgr,
                    stop_event,
                    simulation=simulation,
                ),
                name="analysis",
            ),
        ]
        if not simulation:
            tasks.append(asyncio.create_task(balance_loop(engine, stop_event), name="balance"))
            if hasattr(engine, "cache_flush_loop"):
                tasks.append(
                    asyncio.create_task(engine.cache_flush_loop(), name="cache_flush")
                )

        try:
            await asyncio.gather(*tasks)
        finally:
            stop_event.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if hasattr(engine, "flush_cache"):
                await engine.flush_cache()
            await engine.stop()


async def main() -> None:
    config = get_config()

    try:
        run_startup_validation(
            strategies_dir=config["STRATEGIES_DIR"],
            compiled_dir=config["COMPILED_DIR"],
        )
    except StrategyValidationError as exc:
        exit_on_validation_error(exc)

    selected = prompt_strategy_selection(config["COMPILED_DIR"])
    run_mode = prompt_run_mode()

    strategy_mgr = StrategyManager(compiled_dir=config["COMPILED_DIR"])
    strategy_mgr.activate(selected)

    if run_mode == "simulation":
        engine = SimulationEngine(asset_name=config["ASSET_NAME"])
        await run_monitor_loop(engine, config, strategy_mgr, mode="simulation")
        return

    exit_if_missing_auth_for_normal()

    try:
        engine = DataEngine(
            auth_token=config["AUTH_TOKEN"],
            device_id=config["DEVICE_ID"],
            asset_name=config["ASSET_NAME"],
            timeframe_seconds=int(config["TIMEFRAME_SECONDS"]),
            demo=config["DEMO_MODE"].lower() in ("1", "true", "yes"),
        )
        await run_monitor_loop(engine, config, strategy_mgr, mode="normal")
    except (ConnectionError, OSError) as exc:
        logger.error("%s", classify_connection_error(exc))
        print(f"\n[ERRO] {classify_connection_error(exc)}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except EnvironmentError as exc:
        print(f"\n[ERRO DE CONFIGURAÇÃO] {exc}\n", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        msg = classify_connection_error(exc)
        print(f"\n[ERRO] {msg}\n", file=sys.stderr)
        sys.exit(1)
