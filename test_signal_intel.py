"""
Teste rápido: orquestrador + filtros + formato de log de sinal.
Uso: python test_signal_intel.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from logging_config import setup_logging
from order_guard import OrderGuard
from orchestrator import StrategyOrchestrator
from strategy_manager import StrategyManager
from trade_log import log_signal


def _make_ohlc(n: int, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    prices = [641.0]
    for _ in range(n - 1):
        prices.append(prices[-1] + drift + rng.normal(0, 0.08))
    return pd.DataFrame(
        {
            "timestamp": [datetime.now(timezone.utc) for _ in prices],
            "open": prices,
            "high": [p + 0.15 for p in prices],
            "low": [p - 0.15 for p in prices],
            "close": prices,
            "volume": [0.0] * len(prices),
        }
    )


def main() -> int:
    setup_logging(level="WARNING", file=False)
    mgr = StrategyManager(ROOT / "compiled_strategies")
    orch = StrategyOrchestrator(mgr, OrderGuard(lock_on_signal=False), ROOT / "compiled_strategies")

    checks: list[tuple[str, bool, str]] = []

    # 1) Mercado tendencial classificado
    df_trend = _make_ohlc(90, drift=0.03)
    market = orch.analyze_market(df_trend)
    checks.append(
        (
            "classifica_tendencia",
            market.adx > 0 and market.market_type.value != "INDEFINIDO",
            market.market_type.value,
        )
    )

    # 2) Orquestrador escolhe estrategia com score
    result = orch.analyze(df_trend)
    orch_meta = result.get("orchestrator") or {}
    checks.append(
        (
            "orquestrador_ativo",
            bool(orch_meta.get("strategy_file")),
            str(orch_meta.get("strategy_file", "")),
        )
    )

    # 3) Filtros bloqueiam sinal fraco (volatilidade zero artificial)
    df_flat = _make_ohlc(90, drift=0.0)
    flat_result = orch.analyze(df_flat)
    blocked = flat_result.get("signal") is None
    checks.append(
        (
            "filtros_ativos",
            blocked,
            str(flat_result.get("reason", ""))[:60],
        )
    )

    # 4) Log de sinal visível (smoke test)
    try:
        log_signal("COMPRA", 82, 641.87, "teste interno", strategy_file="fluxo_pullback_m5.py")
        checks.append(("log_sinal", True, "formato OK"))
    except Exception as exc:
        checks.append(("log_sinal", False, str(exc)))

    ok = sum(1 for _, passed, _ in checks if passed)
    fail = sum(1 for _, passed, _ in checks if not passed)
    print(f"\nTeste inteligencia + log: {ok} OK | {fail} FALHA\n")
    for name, passed, detail in checks:
        tag = "OK" if passed else "FALHA"
        print(f"  [{tag}] {name}: {detail}")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
