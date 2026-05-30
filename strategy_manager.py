"""
Gerenciador de estratégias — carrega módulos compilados de compiled_strategies/.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from signal_brain import MarketContext, SignalBrain
from strategy_loader import LoadedStrategy, list_compiled_files, load_by_source_name, load_strategy


def _resolve_path_in_dir(filename: str | Path, compiled_dir: Path) -> Path:
    path = Path(filename)
    if path.is_file():
        return path.resolve()
    candidate = compiled_dir / path.name
    if not candidate.suffix:
        candidate = candidate.with_suffix(".py")
    return candidate.resolve()


logger = logging.getLogger(__name__)


class StrategyManager:
    """Executa estratégias Python compiladas (função analisar)."""

    def __init__(self, compiled_dir: str | Path = "compiled_strategies") -> None:
        self.compiled_dir = Path(compiled_dir)
        self._loaded: LoadedStrategy | None = None
        self.brain = SignalBrain()

    @property
    def market_context(self) -> MarketContext:
        return self.brain.last_context

    @property
    def last_analysis(self) -> dict[str, Any]:
        return self.brain.last_analysis

    @property
    def name(self) -> str:
        return self._loaded.name if self._loaded else "—"

    @property
    def module_path(self) -> Path | None:
        return self._loaded.module_path if self._loaded else None

    @property
    def source_file(self) -> str:
        return self._loaded.source_file if self._loaded else "—"

    def load(self, strategy_ref: str | Path | None = None) -> LoadedStrategy:
        ref = str(strategy_ref or "").strip()
        if ref.endswith((".txt", ".pdf")):
            self._loaded = load_by_source_name(ref, self.compiled_dir)
        else:
            self._loaded = load_strategy(strategy_ref, self.compiled_dir)
        return self._loaded

    def load_compiled(self, compiled_filename: str | Path) -> LoadedStrategy:
        path = _resolve_path_in_dir(compiled_filename, self.compiled_dir)
        if (
            self._loaded is not None
            and self._loaded.module_path.resolve() == path.resolve()
        ):
            return self._loaded
        self._loaded = load_strategy(compiled_filename, self.compiled_dir)
        return self._loaded

    def activate(self, loaded: LoadedStrategy) -> LoadedStrategy:
        """Define estratégia ativa sem recarregar módulo já em memória."""
        if (
            self._loaded is not None
            and self._loaded.module_path.resolve() == loaded.module_path.resolve()
        ):
            return self._loaded
        self._loaded = loaded
        return self._loaded

    def analyze(self, df: pd.DataFrame) -> dict[str, Any]:
        if self._loaded is None:
            raise RuntimeError("Estratégia não carregada. Chame load() primeiro.")

        try:
            raw = self._loaded.analisar(df)
            normalized = _normalize_result(raw, df)
            return self.brain.process(normalized, df)
        except Exception as exc:
            logger.error("Erro na estratégia %s: %s", self._loaded.name, exc)
            return {"signal": None, "price": 0.0, "reason": f"Erro: {exc}"}

    def list_available(self) -> list[str]:
        return [p.name for p in list_compiled_files(self.compiled_dir)]


def _normalize_result(result: Any, df: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise TypeError("analisar(df) deve retornar um dict")

    signal = result.get("signal")
    if signal is not None:
        signal = str(signal).upper()
        if signal in ("CALL", "BUY"):
            signal = "COMPRA"
        elif signal in ("PUT", "SELL"):
            signal = "VENDA"
        elif signal not in ("COMPRA", "VENDA"):
            signal = None

    price = result.get("price")
    if price is None and df is not None and len(df):
        price = float(df.iloc[-1]["close"])
    else:
        price = float(price or 0.0)

    return {
        "signal": signal,
        "price": price,
        "reason": str(result.get("reason", "")),
        "confidence": result.get("confidence"),
    }
