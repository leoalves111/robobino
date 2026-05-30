"""Script rápido para validar credenciais Binomo e estratégias locais."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from bot_settings import log_strategy_catalog, read_bot_settings
from data_engine import DataEngine
from strategy_validator import StrategyValidationError, run_startup_validation

load_dotenv()


def setup_logging() -> None:
    logging.basicConfig(level=logging.CRITICAL, stream=sys.stderr)
    logging.getLogger("BinomoAPI").setLevel(logging.WARNING)


async def main() -> None:
    auth = os.getenv("AUTH_TOKEN", "").strip()
    device = os.getenv("DEVICE_ID", "").strip()

    if not auth or not device:
        print("Configure AUTH_TOKEN e DEVICE_ID no arquivo .env", file=sys.stderr)
        sys.exit(1)

    setup_logging()

    strategies_dir = os.getenv("STRATEGIES_DIR", "strategies")
    compiled_dir = os.getenv("COMPILED_DIR", "compiled_strategies")

    try:
        run_startup_validation(strategies_dir, compiled_dir)
    except StrategyValidationError as exc:
        print(f"\n✖ {exc}\n", file=sys.stderr)
        sys.exit(1)

    read_bot_settings()
    log_strategy_catalog(compiled_dir)
    print("Modo: orquestrador autonomo (escolhe estrategia por mercado)")

    engine = DataEngine(
        auth_token=auth,
        device_id=device,
        asset_name=os.getenv("ASSET_NAME", "Crypto IDX"),
        demo=os.getenv("DEMO_MODE", "true").lower() in ("1", "true", "yes"),
    )

    result = await engine.test_connection()
    await engine.stop()

    if result.get("error"):
        print(f"Falha: {result['error']}", file=sys.stderr)
        sys.exit(1)

    balance = result.get("balance")
    print(f"Conexao OK | RIC: {result['asset_ric']}", end="")
    if balance is not None:
        print(f" | Saldo: ${balance:.2f}")
    else:
        print()
    print("Execute: run.bat ou python main.py")


if __name__ == "__main__":
    asyncio.run(main())
