"""
Carregamento dinâmico de estratégias compiladas (.py) em compiled_strategies/.
Cada módulo deve expor a função analisar(df).
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

logger = logging.getLogger(__name__)

COMPILED_DIR = Path("compiled_strategies")
AnalisarFn = Callable[[pd.DataFrame], dict[str, Any]]

_MODULE_CACHE: dict[str, LoadedStrategy] = {}


@dataclass(frozen=True)
class LoadedStrategy:
    name: str
    module_path: Path
    source_file: str
    analisar: AnalisarFn


def list_compiled_files(directory: Path | str = COMPILED_DIR) -> list[Path]:
    base = Path(directory)
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.glob("*.py")
        if p.name != "__init__.py" and not p.name.startswith("_")
    )


def load_strategy(
    strategy_path: str | Path | None = None,
    directory: Path | str = COMPILED_DIR,
) -> LoadedStrategy:
    """
    Carrega módulo .py compilado e retorna analisar(df).

    strategy_path: nome do .py compilado, stem ou caminho absoluto.
    """
    base = Path(directory)
    path = _resolve_compiled_path(strategy_path, base)

    if not path.exists():
        available = ", ".join(p.name for p in list_compiled_files(base)) or "(nenhum)"
        raise FileNotFoundError(
            f"Estratégia compilada não encontrada: {path}. Disponíveis: {available}"
        )

    cache_key = str(path.resolve())
    cached = _MODULE_CACHE.get(cache_key)
    if cached is not None:
        logger.debug("Estratégia em cache: %s", path.name)
        return cached

    module = _import_module(path)
    analisar = getattr(module, "analisar", None)
    if not callable(analisar):
        raise AttributeError(f"{path.name} deve definir analisar(df).")

    name = getattr(module, "STRATEGY_NAME", path.stem)
    source = getattr(module, "SOURCE_FILE", path.stem + ".txt")

    loaded = LoadedStrategy(
        name=str(name),
        module_path=path,
        source_file=str(source),
        analisar=analisar,
    )
    _MODULE_CACHE[cache_key] = loaded
    logger.debug("Estratégia carregada: %s (%s)", name, path)
    return loaded


def load_by_source_name(
    source_filename: str,
    compiled_dir: Path | str = COMPILED_DIR,
) -> LoadedStrategy:
    """Carrega estratégia compilada a partir do documento fonte (ex: default_strategy.txt)."""
    stem = Path(source_filename).stem
    return load_strategy(f"{stem}.py", compiled_dir)


def _resolve_compiled_path(strategy_path: str | Path | None, base: Path) -> Path:
    if strategy_path is None or str(strategy_path).strip() == "":
        files = list_compiled_files(base)
        if files:
            return files[0]
        return base / "default_strategy.py"

    path = Path(strategy_path)
    if path.is_file():
        return path

    if path.suffix.lower() in {".txt", ".pdf"}:
        return base / f"{path.stem}.py"

    if not path.suffix:
        candidate = base / f"{path.name}.py"
        if candidate.exists():
            return candidate
        candidate = base / f"{path.stem}.py"
        if candidate.exists():
            return candidate

    if path.exists():
        return path

    return base / path.name


def _import_module(path: Path) -> Any:
    module_name = f"compiled_strategy_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Não foi possível importar: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
