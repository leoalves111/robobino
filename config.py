"""
Configuração centralizada — carrega .env e valida credenciais.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ENV_LOADED = False
PROJECT_ROOT = Path(__file__).resolve().parent

# Estratégia: número em bot_settings.txt (padrão 2 = Fluxo & Pullback M5 Pro)
DEFAULT_STRATEGY_NUMBER = 2
DEFAULT_RUN_MODE = "normal"


def load_env(force: bool = False) -> Path:
    """Carrega .env da raiz do projeto (idempotente)."""
    global _ENV_LOADED
    env_path = PROJECT_ROOT / ".env"
    load_dotenv(env_path, override=force)
    _ENV_LOADED = True
    return env_path


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def is_interactive_terminal() -> bool:
    """False em PM2, systemd, Docker ou pipes — evita input() bloqueante."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def resolve_headless() -> bool:
    """
    HEADLESS explícito no .env tem prioridade.
    Caso contrário: ativa automaticamente sem terminal (PM2/Linux).
    """
    raw = os.getenv("HEADLESS", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    if os.getenv("PM2_HOME") or os.getenv("PM2_USAGE"):
        return True
    return not is_interactive_terminal()


@dataclass(frozen=True)
class AppConfig:
    auth_token: str
    device_id: str
    demo_mode: bool
    asset_name: str
    timeframe_seconds: int
    strategies_dir: str
    compiled_dir: str
    min_confidence: int
    signal_cooldown_candles: int
    min_adx: float
    headless: bool
    run_mode: str
    connect_max_retries: int
    connect_retry_seconds: float
    log_level: str
    log_dir: Path

    @classmethod
    def from_env(cls) -> AppConfig:
        if not _ENV_LOADED:
            load_env()

        return cls(
            auth_token=os.getenv("AUTH_TOKEN", "").strip(),
            device_id=os.getenv("DEVICE_ID", "").strip(),
            demo_mode=_flag("DEMO_MODE", True),
            asset_name=os.getenv("ASSET_NAME", "Crypto IDX"),
            timeframe_seconds=int(os.getenv("TIMEFRAME_SECONDS", "300")),
            strategies_dir=os.getenv("STRATEGIES_DIR", "strategies"),
            compiled_dir=os.getenv("COMPILED_DIR", "compiled_strategies"),
            min_confidence=int(os.getenv("MIN_CONFIDENCE", "52")),
            signal_cooldown_candles=int(os.getenv("SIGNAL_COOLDOWN_CANDLES", "2")),
            min_adx=float(os.getenv("MIN_ADX", "14")),
            headless=resolve_headless(),
            run_mode=os.getenv("RUN_MODE", DEFAULT_RUN_MODE).strip().lower(),
            connect_max_retries=int(os.getenv("CONNECT_MAX_RETRIES", "10")),
            connect_retry_seconds=float(os.getenv("CONNECT_RETRY_SECONDS", "15")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_dir=Path(os.getenv("LOG_DIR", "logs")),
        )

    def validate_auth(self) -> None:
        missing = []
        if not self.auth_token:
            missing.append("AUTH_TOKEN")
        if not self.device_id:
            missing.append("DEVICE_ID")
        if missing:
            raise EnvironmentError(
                f"Credenciais ausentes no .env: {', '.join(missing)}"
            )

    def as_dict(self) -> dict[str, str]:
        return {
            "AUTH_TOKEN": self.auth_token,
            "DEVICE_ID": self.device_id,
            "DEMO_MODE": "true" if self.demo_mode else "false",
            "ASSET_NAME": self.asset_name,
            "TIMEFRAME_SECONDS": str(self.timeframe_seconds),
            "STRATEGIES_DIR": self.strategies_dir,
            "COMPILED_DIR": self.compiled_dir,
            "MIN_CONFIDENCE": str(self.min_confidence),
            "SIGNAL_COOLDOWN_CANDLES": str(self.signal_cooldown_candles),
            "MIN_ADX": str(self.min_adx),
        }
