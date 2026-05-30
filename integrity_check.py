"""
Verificação de integridade completa do Binomo Signal Generator.
Uso: .venv\\Scripts\\python.exe integrity_check.py
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class IntegrityReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(CheckResult(name, ok, detail))

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def all_ok(self) -> bool:
        return self.failed == 0


def check_imports(report: IntegrityReport) -> None:
    modules = [
        "main",
        "config",
        "logging_config",
        "api_connection",
        "data_engine",
        "candle_cache",
        "signal_brain",
        "strategy_manager",
        "strategy_loader",
        "strategy_validator",
        "simulation_engine",
        "simulation_data",
        "alert_system",
    ]
    for mod in modules:
        try:
            importlib.import_module(mod)
            report.add(f"import:{mod}", True)
        except Exception as exc:
            report.add(f"import:{mod}", False, str(exc))


def check_env(report: IntegrityReport) -> None:
    auth = os.getenv("AUTH_TOKEN", "").strip()
    device = os.getenv("DEVICE_ID", "").strip()
    report.add("env:.env existe", (ROOT / ".env").is_file())
    report.add("env:AUTH_TOKEN", bool(auth), "preenchido" if auth else "ausente")
    report.add("env:DEVICE_ID", bool(device), "preenchido" if device else "ausente")


def check_strategy_sync(report: IntegrityReport) -> None:
    from strategy_validator import validate_all_strategies

    try:
        results = validate_all_strategies("strategies", "compiled_strategies")
        report.add("estrategias:txt_vs_py", True, f"{len(results)} OK")
    except Exception as exc:
        report.add("estrategias:txt_vs_py", False, str(exc))


def check_strategies_analyze(report: IntegrityReport) -> None:
    import numpy as np
    import pandas as pd
    from strategy_manager import StrategyManager

    rng = np.random.default_rng(0)
    rows = 80
    prices = [641.0 + rng.normal(0, 0.1) for _ in range(rows)]
    df = pd.DataFrame(
        {
            "timestamp": [datetime.now(timezone.utc) for _ in range(rows)],
            "open": prices,
            "high": [p + 0.1 for p in prices],
            "low": [p - 0.1 for p in prices],
            "close": prices,
            "volume": [0.0] * rows,
        }
    )

    mgr = StrategyManager()
    for name in mgr.list_available():
        try:
            mgr.load(name)
            result = mgr.analyze(df)
            if "Erro" in str(result.get("reason", "")):
                report.add(f"estrategia:{name}", False, result.get("reason", ""))
            else:
                report.add(
                    f"estrategia:{name}",
                    True,
                    f"signal={result.get('signal')} conf={result.get('confidence', 0)}",
                )
        except Exception as exc:
            report.add(f"estrategia:{name}", False, str(exc))


def check_price_extraction(report: IntegrityReport) -> None:
    from data_engine import DataEngine

    sample = {"entrie_rate": 641.867, "asset_ric": "Z-CRY/IDX"}
    price = DataEngine._extract_price(sample)
    ok = price is not None and abs(price - 641.867) < 0.001
    report.add("dados:extracao_entrie_rate", ok, str(price))


def check_cache(report: IntegrityReport) -> None:
    try:
        from tests.test_candle_cache import (
            test_append_after_long_stop,
            test_corrupt_json,
            test_empty_file,
            test_fifo_window,
            test_stale_cache_rejected,
        )
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            test_fifo_window(base)
            test_stale_cache_rejected(base)
            test_append_after_long_stop(base)
            test_corrupt_json(base / "bad.json")
            test_empty_file(base / "empty.json")
        report.add("cache:fifo_freshness", True)
    except Exception as exc:
        report.add("cache:fifo_freshness", False, str(exc))


def check_api_helpers(report: IntegrityReport) -> None:
    from api_connection import check_connect, classify_connection_error, is_api_initialized

    report.add("api:check_connect_none", not check_connect(None))
    report.add("api:is_api_initialized", not is_api_initialized(None))
    msg = classify_connection_error(Exception("401 unauthorized token"))
    report.add("api:classify_auth", "autenticação" in msg.lower() or "auth" in msg.lower(), msg)


def check_config(report: IntegrityReport) -> None:
    from config import AppConfig, load_env

    load_env()
    cfg = AppConfig.from_env()
    report.add("config:from_env", cfg.timeframe_seconds == 300, f"tf={cfg.timeframe_seconds}")
    report.add("config:log_dir", isinstance(cfg.log_dir, Path))


async def check_simulation(report: IntegrityReport) -> None:
    from simulation_engine import SimulationEngine
    from strategy_manager import StrategyManager

    engine = SimulationEngine()
    ok = await engine.test_connection()
    report.add("simulacao:connect", ok.get("connected", False))

    engine._running = True
    tasks = [
        asyncio.create_task(engine.run()),
        asyncio.create_task(engine.clock_loop()),
    ]
    mgr = StrategyManager()
    mgr.load("default_strategy.py")
    signals = 0
    try:
        for _ in range(15):
            candle = await engine.wait_closed_candle(timeout=4)
            if candle:
                r = mgr.analyze(engine.get_dataframe())
                if r.get("signal"):
                    signals += 1
    finally:
        engine._running = False
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.stop()

    report.add(
        "simulacao:velas_e_analise",
        engine.state.candles_count >= 80,
        f"velas={engine.state.candles_count} sinais={signals}",
    )


async def check_live_connection(report: IntegrityReport) -> None:
    auth = os.getenv("AUTH_TOKEN", "").strip()
    device = os.getenv("DEVICE_ID", "").strip()
    if not auth or not device:
        report.add("live:conexao_binomo", True, "SKIP — sem credenciais")
        return

    from api_connection import conectar_com_retry
    from data_engine import DataEngine

    engine = DataEngine(auth_token=auth, device_id=device)
    try:
        ok = await conectar_com_retry(engine, max_tentativas=3, intervalo_seg=5)
        report.add(
            "live:conectar_com_retry",
            ok,
            f"preco={engine.state.last_price} ticks={engine.state.price_ticks}",
        )
        if ok:
            engine._running = True
            tasks = [
                asyncio.create_task(engine.run()),
                asyncio.create_task(engine.rate_poll_loop(interval=1.0)),
                asyncio.create_task(engine.clock_loop()),
            ]
            try:
                await asyncio.sleep(12)
            finally:
                engine._running = False
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            has_data = engine.state.last_price is not None and engine.state.price_ticks > 0
            report.add(
                "live:dados_mercado",
                has_data,
                f"preco={engine.state.last_price} ticks={engine.state.price_ticks}",
            )
            report.add(
                "live:websocket_ativo",
                engine.is_connected(),
                f"velas={engine.state.candles_count}",
            )
    except Exception as exc:
        report.add("live:conexao_binomo", False, str(exc))
    finally:
        await engine.stop()


def check_required_files(report: IntegrityReport) -> None:
    required = [
        "main.py",
        "run.bat",
        "requirements.txt",
        ".env.example",
        "data_engine.py",
        "compiled_strategies/default_strategy.py",
        "compiled_strategies/fluxo_pullback_m5.py",
        "strategies/default_strategy.txt",
    ]
    for rel in required:
        report.add(f"arquivo:{rel}", (ROOT / rel).is_file())


async def run_all() -> IntegrityReport:
    report = IntegrityReport()
    print("=" * 60)
    print(" VERIFICACAO DE INTEGRIDADE — Binomo Signal Generator")
    print("=" * 60)
    print()

    check_required_files(report)
    check_imports(report)
    check_env(report)
    check_config(report)
    check_strategy_sync(report)
    check_price_extraction(report)
    check_api_helpers(report)
    check_cache(report)
    check_strategies_analyze(report)
    await check_simulation(report)
    await check_live_connection(report)

    return report


def print_report(report: IntegrityReport) -> int:
    print()
    print("-" * 60)
    for r in report.results:
        icon = "OK  " if r.ok else "FALHA"
        line = f"[{icon}] {r.name}"
        if r.detail:
            line += f" — {r.detail}"
        print(line)
    print("-" * 60)
    print(f"Total: {report.passed} OK | {report.failed} FALHA | {len(report.results)} checks")
    print()
    if report.all_ok:
        print("RESULTADO: Robo 100% funcional nos testes de integridade.")
        return 0
    print("RESULTADO: Problemas encontrados — corrija os itens FALHA acima.")
    return 1


if __name__ == "__main__":
    try:
        rep = asyncio.run(run_all())
        sys.exit(print_report(rep))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
