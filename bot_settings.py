"""
Configuração do robô — 100% autónomo via orquestrador (sem estratégia fixa).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from strategy_registry import STRATEGY_CATALOG, list_registered_strategies, validate_compiled_dir

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = PROJECT_ROOT / "bot_settings.txt"
DEFAULT_RUN_MODE = "normal"


@dataclass(frozen=True)
class BotSettings:
    """Robô sempre em modo orquestrador — escolhe a melhor estratégia por vela M5."""

    run_mode: str = DEFAULT_RUN_MODE
    autonomous: bool = True


def read_bot_settings(path: Path | None = None) -> BotSettings:
    """
    Lê bot_settings.txt (opcional). Modo fixo/números são ignorados — só orquestrador.
    """
    settings_path = path or SETTINGS_FILE
    if settings_path.is_file():
        try:
            text = settings_path.read_text(encoding="utf-8").lower()
            if "modo=fixo" in text or "estrategia=" in text:
                logger.warning(
                    "bot_settings.txt: MODO=fixo/ESTRATEGIA ignorados — "
                    "o robô usa orquestrador autónomo 24/7."
                )
        except OSError as exc:
            logger.warning("Não foi possível ler %s: %s", settings_path, exc)
    else:
        logger.info("%s ausente — orquestrador autónomo (padrão).", settings_path.name)

    return BotSettings(run_mode=DEFAULT_RUN_MODE, autonomous=True)


def log_strategy_catalog(compiled_dir: Path | str) -> None:
    missing = validate_compiled_dir(compiled_dir)
    if missing:
        logger.warning("Estratégias ausentes: %s", ", ".join(missing))

    lines = [
        "Pool de estratégias (orquestrador escolhe a melhor a cada vela M5):",
    ]
    for entry in STRATEGY_CATALOG:
        present = (Path(compiled_dir) / entry.filename).is_file()
        tag = "OK" if present else "AUSENTE"
        lines.append(f"  [{tag}] {entry.name} — {entry.role} ({entry.filename})")
    lines.append("  Nenhuma estratégia fixa — decisão 100% pelo mercado atual.")
    logger.info("\n".join(lines))
