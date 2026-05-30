"""
Catálogo oficial de estratégias compiladas — reconhecido pelo orquestrador.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from strategy_selector import list_selectable_strategies


@dataclass(frozen=True)
class StrategyEntry:
    filename: str
    name: str
    role: str  # tendencia | range | breakout | price_action | reversao
    min_candles: int


STRATEGY_CATALOG: tuple[StrategyEntry, ...] = (
    StrategyEntry("alta_volatilidade_m5.py", "Alta Volatilidade M5 (Tendencia)", "tendencia", 40),
    StrategyEntry("baixa_volatilidade_m5.py", "Baixa Volatilidade M5 (Range)", "range", 30),
    StrategyEntry("breakout_donchian_m5.py", "Breakout Donchian M5", "breakout", 25),
    StrategyEntry("engolfo_volume_m5.py", "Engolfo com Volume M5", "price_action", 20),
    StrategyEntry("fluxo_pullback_m5.py", "Fluxo & Pullback M5 Pro", "tendencia", 30),
    StrategyEntry("retracao_m5.py", "Retracao Bollinger M5", "range", 25),
    StrategyEntry("reversao_rsi_volume_m5.py", "Reversao RSI + Volume M5", "reversao", 30),
    StrategyEntry("tendencia_crossover_m5.py", "Crossover Tendencia M5", "tendencia", 55),
)

STRATEGY_MIN_CANDLES: dict[str, int] = {e.filename: e.min_candles for e in STRATEGY_CATALOG}

BOOTSTRAP_STRATEGY_FILE = "fluxo_pullback_m5.py"
FALLBACK_STRATEGY_FILE = BOOTSTRAP_STRATEGY_FILE


def catalog_filenames() -> list[str]:
    return [e.filename for e in STRATEGY_CATALOG]


def validate_compiled_dir(compiled_dir: Path | str) -> list[str]:
    base = Path(compiled_dir)
    return [e.filename for e in STRATEGY_CATALOG if not (base / e.filename).is_file()]


def list_registered_strategies(compiled_dir: Path | str) -> list[Path]:
    base = Path(compiled_dir)
    on_disk = {p.name for p in list_selectable_strategies(base)}
    return [base / name for name in catalog_filenames() if name in on_disk]


def entry_for_file(filename: str) -> StrategyEntry | None:
    for entry in STRATEGY_CATALOG:
        if entry.filename == filename:
            return entry
    return None
