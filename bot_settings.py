"""
Configuração simples do robô — edite bot_settings.txt (sem menus nem input()).

Número da estratégia = mesma ordem do menu antigo (arquivos em compiled_strategies/, A→Z).
Padrão: 2 = Fluxo & Pullback M5 Pro.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from strategy_loader import LoadedStrategy, load_strategy
from strategy_selector import list_selectable_strategies

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = PROJECT_ROOT / "bot_settings.txt"

DEFAULT_STRATEGY_NUMBER = 2
DEFAULT_RUN_MODE = "normal"  # sempre M5 real — nunca simulação em produção


@dataclass(frozen=True)
class BotSettings:
    strategy_number: int
    run_mode: str = DEFAULT_RUN_MODE


def _peek_strategy_name(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8")[:4096]
        match = re.search(r'STRATEGY_NAME\s*=\s*["\'](.+?)["\']', head)
        if match:
            return match.group(1)
    except OSError:
        pass
    return path.stem.replace("_", " ").title()


def _parse_strategy_number(text: str) -> int | None:
    text = text.strip()
    if not text or text.startswith("#"):
        return None
    m = re.match(r"^(?:ESTRATEGIA|ESTRATÉGIA|STRATEGY)\s*=\s*(\d+)\s*$", text, re.I)
    if m:
        return int(m.group(1))
    if text.isdigit():
        return int(text)
    return None


def read_bot_settings(path: Path | None = None) -> BotSettings:
    """
    Lê bot_settings.txt na raiz do projeto.
    Aceita: linha só com número (ex: 2) ou ESTRATEGIA=2.
    """
    settings_path = path or SETTINGS_FILE
    number = DEFAULT_STRATEGY_NUMBER

    if settings_path.is_file():
        try:
            for line in settings_path.read_text(encoding="utf-8").splitlines():
                parsed = _parse_strategy_number(line)
                if parsed is not None:
                    number = parsed
                    break
        except OSError as exc:
            logger.warning("Não foi possível ler %s: %s — usando estratégia %d", settings_path, exc, number)
    else:
        logger.info(
            "%s não encontrado — usando estratégia padrão #%d. Copie bot_settings.example.txt se quiser.",
            settings_path.name,
            DEFAULT_STRATEGY_NUMBER,
        )

    if number < 1:
        raise ValueError(f"Número de estratégia inválido em {settings_path.name}: {number}")

    return BotSettings(strategy_number=number, run_mode=DEFAULT_RUN_MODE)


def log_strategy_catalog(compiled_dir: Path | str) -> list[Path]:
    """Registra no log o mapa número → arquivo (para PM2 / diagnóstico)."""
    strategies = list_selectable_strategies(compiled_dir)
    if not strategies:
        logger.error("Nenhuma estratégia em %s", compiled_dir)
        return strategies

    lines = ["Estratégias disponíveis (edite o número em bot_settings.txt):"]
    for index, path in enumerate(strategies, start=1):
        name = _peek_strategy_name(path)
        marker = " ← padrão" if index == DEFAULT_STRATEGY_NUMBER else ""
        lines.append(f"  {index} = {name} ({path.name}){marker}")
    logger.info("\n".join(lines))
    return strategies


def load_strategy_by_number(
    compiled_dir: Path | str,
    number: int,
) -> LoadedStrategy:
    """Carrega estratégia pelo número do catálogo (sem input())."""
    strategies = list_selectable_strategies(compiled_dir)
    if not strategies:
        raise FileNotFoundError(
            f"Nenhuma estratégia em {compiled_dir}. Compile .py em compiled_strategies/."
        )

    log_strategy_catalog(compiled_dir)

    if number < 1 or number > len(strategies):
        available = ", ".join(str(i) for i in range(1, len(strategies) + 1))
        raise ValueError(
            f"Estratégia #{number} inválida em bot_settings.txt. "
            f"Use um número entre 1 e {len(strategies)} ({available})."
        )

    selected = strategies[number - 1]
    loaded = load_strategy(selected, compiled_dir)
    logger.info(
        "Estratégia #%d ativa: %s (%s) | modo=%s (M5 real Binomo)",
        number,
        loaded.name,
        loaded.module_path.name,
        DEFAULT_RUN_MODE,
    )
    return loaded
