"""
Pré-validação one-click — estratégias .py em compiled_strategies/ (sem .txt obrigatório).
Executado pelo run.bat antes de main.py. Nunca pede input().
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

COMPILED_DIR = Path("compiled_strategies")
STRATEGIES_DIR = Path("strategies")


def _banner(title: str) -> None:
    line = "=" * 60
    print()
    print(line)
    print(title)
    print(line)
    print()


def _module_has_analisar(path: Path) -> bool:
    try:
        spec = importlib.util.spec_from_file_location(f"preflight_{path.stem}", path)
        if spec is None or spec.loader is None:
            return False
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return callable(getattr(mod, "analisar", None))
    except Exception:
        return False


def check_strategies() -> int:
    from strategy_registry import STRATEGY_CATALOG, validate_compiled_dir

    if not COMPILED_DIR.is_dir():
        _banner("ERRO: Pasta compiled_strategies/ nao encontrada.")
        return 1

    missing = validate_compiled_dir(COMPILED_DIR)
    if missing:
        _banner("ERRO: Estrategias .py ausentes no catalogo")
        for name in missing:
            print(f"  - compiled_strategies/{name}")
        print()
        print(f"  Esperadas {len(STRATEGY_CATALOG)} estrategias — veja strategy_registry.py")
        print()
        return 1

    broken: list[str] = []
    for entry in STRATEGY_CATALOG:
        py_path = COMPILED_DIR / entry.filename
        if not _module_has_analisar(py_path):
            broken.append(entry.filename)

    if broken:
        _banner("ERRO: Modulo sem funcao analisar(df)")
        for name in broken:
            print(f"  - {name}")
        return 1

    # Se existir .txt em strategies/, valida sync opcional (nao bloqueia se pasta vazia)
    if STRATEGIES_DIR.is_dir():
        for source in sorted(STRATEGIES_DIR.glob("*.txt")):
            compiled = COMPILED_DIR / f"{source.stem}.py"
            if compiled.is_file() and os.path.getmtime(source) > os.path.getmtime(compiled):
                _banner("ERRO: Estrategia .txt mais nova que o .py compilado")
                print(f"  strategies/{source.name}")
                print(f"  Recompile compiled_strategies/{compiled.name} no Cursor")
                return 1

    print(f"  OK — {len(STRATEGY_CATALOG)} estrategias .py validadas (MODO=auto / orquestrador)")
    return 0


if __name__ == "__main__":
    sys.exit(check_strategies())
