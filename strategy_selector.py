"""
Listagem de estrategias compiladas (helpers para registry/validacao).
Selecao dinamica: orchestrator.py — sem menu interativo.
"""

from __future__ import annotations

from pathlib import Path

from strategy_loader import LoadedStrategy, list_compiled_files, load_strategy

HELPER_PREFIXES = ("_",)
HELPER_NAMES = frozenset({"__init__.py"})


def list_selectable_strategies(compiled_dir: Path | str) -> list[Path]:
    base = Path(compiled_dir)
    return [
        p
        for p in list_compiled_files(base)
        if p.name not in HELPER_NAMES and not p.name.startswith(HELPER_PREFIXES)
    ]


def load_auto_strategy(compiled_dir: Path | str = "compiled_strategies") -> LoadedStrategy:
    from strategy_registry import BOOTSTRAP_STRATEGY_FILE

    return load_strategy(BOOTSTRAP_STRATEGY_FILE, compiled_dir)
