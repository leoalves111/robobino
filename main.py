"""
Ponto de entrada — Gerador de Sinais Binomo (READ-ONLY).
Resiliente para AWS/pm2: retry de conexão, logging estruturado, modo headless.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Any, Protocol

from alert_system import Dashboard, DashboardState
from api_connection import (
    check_connect,
    classify_connection_error,
    conectar_com_retry,
    safe_get_candles,
)
from config import AppConfig, load_env
from data_engine import DataEngine
from logging_config import setup_logging
from bot_settings import DEFAULT_RUN_MODE, log_strategy_catalog, read_bot_settings
from order_guard import OrderGuard
from orchestrator import StrategyOrchestrator
from simulation_engine import SimulationEngine
from strategy_manager import StrategyManager
from strategy_registry import validate_compiled_dir
from strategy_validator import (
    StrategyValidationError,
    exit_on_validation_error,
    run_startup_validation,
)

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


def resolve_startup(
    config: AppConfig,
    strategy_mgr: StrategyManager,
) -> str:
    """
    Arranque 100% autónomo — orquestrador escolhe a melhor estratégia a cada vela M5.
    Sem estratégia fixa, sem perguntas, sem números no bot_settings.
    """
    read_bot_settings()

    mode = DEFAULT_RUN_MODE
    if config.run_mode and config.run_mode != "normal":
        logger.warning(
            "RUN_MODE=%s ignorado — modo normal M5 real.",
            config.run_mode,
        )

    missing = validate_compiled_dir(config.compiled_dir)
    if missing:
        raise EnvironmentError(
            f"Estrategias ausentes em compiled_strategies/: {', '.join(missing)}"
        )

    log_strategy_catalog(config.compiled_dir)
    logger.info(
        "Arranque autonomo | orquestrador inteligente | 8 estrategias | 24/7 | sem input()"
    )
    return mode


def create_engine(
    config: AppConfig,
    mode: str,
    order_guard: OrderGuard | None = None,
) -> MarketEngine:
    if mode == "simulation":
        return SimulationEngine(asset_name=config.asset_name)
    config.validate_auth()
    return DataEngine(
        auth_token=config.auth_token,
        device_id=config.device_id,
        asset_name=config.asset_name,
        timeframe_seconds=config.timeframe_seconds,
        demo=config.demo_mode,
        order_guard=order_guard,
    )


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


async def headless_status_loop(
    engine: MarketEngine,
    strategy: StrategyManager,
    stop_event: asyncio.Event,
    orchestrator: StrategyOrchestrator | None = None,
    interval: int = 30,
) -> None:
    """Log periódico para pm2 logs (sem painel Rich)."""
    while not stop_event.is_set():
        try:
            connected = engine.state.connected
            if isinstance(engine, DataEngine):
                connected = engine.is_connected()
            orch_hint = ""
            if orchestrator and orchestrator.last_decision:
                d = orchestrator.last_decision
                orch_hint = f" | orch={d.market.market_type.value}→{d.strategy_file}"
            logger.info(
                "STATUS | conectado=%s | preço=%s | velas=%d | ticks=%d | rsi=%.0f | ordem=%s%s | %s",
                connected,
                f"{engine.state.last_price:.5f}" if engine.state.last_price else "—",
                engine.state.candles_count,
                engine.state.price_ticks,
                strategy.market_context.rsi,
                getattr(engine.state, "order_status", "NONE"),
                orch_hint,
                strategy.last_analysis.get("reason", "aguardando")[:50]
                if strategy.last_analysis
                else "aguardando",
            )
        except Exception as exc:
            logger.warning("Erro no status loop: %s", exc)
        await asyncio.sleep(interval)


async def dashboard_loop(
    dashboard: Dashboard,
    engine: MarketEngine,
    strategy: StrategyManager,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            sync_dashboard(dashboard.state, engine, strategy)
            dashboard.refresh()
        except Exception as exc:
            logger.warning("Erro no dashboard: %s", exc)
        await asyncio.sleep(1)


async def balance_loop(engine: MarketEngine, stop_event: asyncio.Event, interval: int = 60) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.sleep(interval)
            await engine.refresh_balance()
        except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
            logger.warning("Falha ao atualizar saldo (rede): %s", exc)
        except Exception as exc:
            logger.error("Erro inesperado no balance_loop: %s", exc)


async def ensure_engine_connected(engine: MarketEngine, config: AppConfig) -> bool:
    if not isinstance(engine, DataEngine):
        return True
    if engine.is_connected() and check_connect(engine._api):
        return True
    logger.warning("Conexão perdida — reconectando com retry...")
    return await conectar_com_retry(
        engine,
        max_tentativas=config.connect_max_retries,
        intervalo_seg=config.connect_retry_seconds,
    )


async def analysis_loop(
    dashboard: Dashboard | None,
    engine: MarketEngine,
    strategy: StrategyManager,
    config: AppConfig,
    stop_event: asyncio.Event,
    orchestrator: StrategyOrchestrator,
    order_guard: OrderGuard,
    *,
    simulation: bool = False,
) -> None:
    last_bucket: datetime | None = None

    while not stop_event.is_set():
        try:
            candle = await asyncio.wait_for(engine.wait_closed_candle(), timeout=2.0)
        except asyncio.TimeoutError:
            continue
        except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
            logger.warning("Rede instável no loop de análise: %s", exc)
            await asyncio.sleep(3)
            continue

        if candle is None or last_bucket == candle.timestamp:
            continue
        last_bucket = candle.timestamp

        try:
            if not simulation and not await ensure_engine_connected(engine, config):
                if dashboard:
                    dashboard.state.analysis_hint = "Reconectando à Binomo..."
                await asyncio.sleep(5)
                continue

            tag = "SIM" if simulation else "LIVE"
            logger.info(
                "[%s] Vela fechada @ %s | close=%.5f | velas=%d | ticks=%d",
                tag,
                candle.timestamp.strftime("%H:%M"),
                candle.close,
                engine.state.candles_count,
                engine.state.price_ticks,
            )

            df = await safe_get_candles(engine)
            result = orchestrator.analyze(df)

            if result.get("signal"):
                direction = result["signal"]
                price = result.get("price", candle.close)
                confidence = int(result.get("confidence") or 0)
                reason = str(result.get("reason", ""))
                if order_guard is not None:
                    order_guard.on_signal_emitted()
                if dashboard:
                    dashboard.register_signal(
                        direction=direction,
                        price=price,
                        confidence=confidence,
                        reason=reason,
                    )
                else:
                    logger.critical(
                        "SINAL %s | conf=%d%% | preço=%.5f | %s",
                        direction,
                        confidence,
                        price,
                        reason,
                    )
            elif dashboard:
                dashboard.state.analysis_hint = str(
                    result.get("reason") or "Aguardando sinal..."
                )
            else:
                hint = str(result.get("reason") or "aguardando")[:80]
                orch = result.get("orchestrator") or {}
                strat = orch.get("strategy_file", "?")
                logger.info("ANALISE | sem sinal | %s | %s", strat, hint)
        except Exception as exc:
            logger.exception("Erro temporário na análise: %s", exc)
            if dashboard:
                dashboard.state.analysis_hint = f"Erro temporário — {exc}"
            await asyncio.sleep(2)


async def run_monitor_loop(
    engine: MarketEngine,
    config: AppConfig,
    strategy_mgr: StrategyManager,
    order_guard: OrderGuard,
    *,
    mode: str = "normal",
) -> None:
    simulation = mode == "simulation"
    stop_event = asyncio.Event()

    def _shutdown() -> None:
        logger.info("Sinal de encerramento recebido")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _shutdown)
            except NotImplementedError:
                pass

    dashboard: Dashboard | None = None
    state: DashboardState | None = None

    orchestrator = StrategyOrchestrator(strategy_mgr, order_guard, config.compiled_dir)

    if not config.headless:
        state = DashboardState(asset_name=config.asset_name)
        state.mode = "SIMULAÇÃO" if simulation else "READ-ONLY"
        dashboard = Dashboard(state)
        state.strategy_name = strategy_mgr.name
        state.strategy_file = (
            strategy_mgr.module_path.name if strategy_mgr.module_path else "—"
        )
        state.connected = engine.state.connected
        state.balance = engine.state.balance
        state.asset_ric = engine.asset_ric
        state.status_message = (
            engine.state.status_message
            if simulation
            else "Monitorando Crypto IDX M5"
        )
        sync_dashboard(state, engine, strategy_mgr)

        bootstrap_df = await safe_get_candles(engine)
        bootstrap = orchestrator.analyze(bootstrap_df)
        state.analysis_hint = str(bootstrap.get("reason") or "Aguardando sinal...")
        if bootstrap.get("signal") and dashboard:
            dashboard.register_signal(
                direction=bootstrap["signal"],
                price=bootstrap.get("price", engine.state.last_price or 0),
                confidence=int(bootstrap.get("confidence") or 0),
                reason=str(bootstrap.get("reason", "")),
            )

    tasks = [
        asyncio.create_task(engine.run(), name="stream"),
        asyncio.create_task(engine.rate_poll_loop(), name="rate_poll"),
        asyncio.create_task(engine.clock_loop(), name="clock"),
        asyncio.create_task(
            analysis_loop(
                dashboard,
                engine,
                strategy_mgr,
                config,
                stop_event,
                simulation=simulation,
                orchestrator=orchestrator,
                order_guard=order_guard,
            ),
            name="analysis",
        ),
    ]

    if config.headless:
        tasks.append(
            asyncio.create_task(
                headless_status_loop(
                    engine, strategy_mgr, stop_event, orchestrator=orchestrator
                ),
                name="status",
            )
        )
    elif dashboard and state:
        tasks.append(
            asyncio.create_task(
                dashboard_loop(dashboard, engine, strategy_mgr, stop_event), name="ui"
            )
        )

    if not simulation:
        tasks.append(asyncio.create_task(balance_loop(engine, stop_event), name="balance"))
        if hasattr(engine, "cache_flush_loop"):
            tasks.append(asyncio.create_task(engine.cache_flush_loop(), name="cache_flush"))

    async def _run_tasks() -> None:
        try:
            await asyncio.gather(*tasks)
        except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
            logger.error("Falha de rede no loop principal: %s", exc)
            raise
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Erro no loop principal: %s", exc)
            raise
        finally:
            stop_event.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if hasattr(engine, "flush_cache"):
                await engine.flush_cache()
            await engine.stop()

    if dashboard:
        with dashboard.start():
            await _run_tasks()
    else:
        await _run_tasks()


async def run_resilient(
    config: AppConfig,
    strategy_mgr: StrategyManager,
    order_guard: OrderGuard,
    mode: str,
) -> None:
    """
    Loop externo AWS: reconecta em falhas sem encerrar o processo (pm2 keep-alive).
    """
    tentativa_global = 0
    while True:
        tentativa_global += 1
        engine: MarketEngine | None = None
        try:
            logger.info(
                "=== Sessao #%d | modo=%s | orquestrador=ativo ===",
                tentativa_global,
                mode,
            )
            engine = create_engine(config, mode, order_guard=order_guard)

            ok = await conectar_com_retry(
                engine,
                max_tentativas=config.connect_max_retries,
                intervalo_seg=config.connect_retry_seconds,
            )
            if not ok:
                logger.error("Sessão #%d: conexão falhou — retry global em 60s", tentativa_global)
                await asyncio.sleep(60)
                continue

            await run_monitor_loop(
                engine,
                config,
                strategy_mgr,
                order_guard,
                mode=mode,
            )
            logger.info("Sessão #%d encerrada normalmente", tentativa_global)
            break

        except KeyboardInterrupt:
            logger.info("Interrompido pelo usuário")
            break
        except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
            logger.error(
                "Sessão #%d — erro de rede: %s — reiniciando em 30s",
                tentativa_global,
                exc,
            )
            await asyncio.sleep(30)
        except Exception as exc:
            logger.exception(
                "Sessão #%d — erro inesperado: %s — reiniciando em 45s",
                tentativa_global,
                classify_connection_error(exc),
            )
            await asyncio.sleep(45)
        finally:
            if engine is not None:
                try:
                    if hasattr(engine, "flush_cache"):
                        await engine.flush_cache()
                    await engine.stop()
                except Exception as exc:
                    logger.warning("Erro ao encerrar engine: %s", exc)


async def main() -> None:
    load_env()
    config = AppConfig.from_env()
    setup_logging(log_dir=config.log_dir, level=config.log_level)

    read_bot_settings()
    logger.info(
        "Binomo Signal Generator | headless=%s | orquestrador autonomo 24/7",
        config.headless,
    )

    try:
        run_startup_validation(
            strategies_dir=config.strategies_dir,
            compiled_dir=config.compiled_dir,
            headless=config.headless,
        )
    except StrategyValidationError as exc:
        exit_on_validation_error(exc, headless=config.headless)

    strategy_mgr = StrategyManager(compiled_dir=config.compiled_dir)
    order_guard = OrderGuard(
        lock_on_signal_seconds=config.timeframe_seconds,
        lock_on_signal=True,
    )
    mode = resolve_startup(config, strategy_mgr)

    if mode == "normal":
        config.validate_auth()
        logger.info("Credenciais .env validadas (AUTH_TOKEN + DEVICE_ID)")

    logger.info("Loop resiliente 24/7 — pm2 logs para monitorar")
    await run_resilient(config, strategy_mgr, order_guard, mode)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except EnvironmentError as exc:
        logger.critical("Configuração inválida: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Encerrado por KeyboardInterrupt")
    except Exception as exc:
        logger.critical("Erro fatal: %s", classify_connection_error(exc))
        sys.exit(1)
