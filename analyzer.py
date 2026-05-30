"""
Cálculo de indicadores (Pandas-TA) e avaliação de condições de sinal.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)


class SignalAnalyzer:
    """Calcula indicadores e avalia regras da estratégia."""

    MIN_CANDLES = 60

    def __init__(self, strategy: dict[str, Any]) -> None:
        self.strategy = strategy

    def analyze(self, df: pd.DataFrame) -> dict[str, Any]:
        if df is None or len(df) < self.MIN_CANDLES:
            return {
                "signal": None,
                "reason": f"Dados insuficientes ({len(df) if df is not None else 0}/{self.MIN_CANDLES} velas)",
            }

        work = df.copy()
        work = self._compute_indicators(work)

        if not self._passes_filters(work):
            return {"signal": None, "reason": "Filtros de mercado não atendidos"}

        buy_ok, buy_reasons = self._evaluate_conditions(
            work, self.strategy.get("buy_conditions", [])
        )
        sell_ok, sell_reasons = self._evaluate_conditions(
            work, self.strategy.get("sell_conditions", [])
        )

        last_close = float(work.iloc[-1]["close"])

        if buy_ok and not sell_ok:
            return {
                "signal": "COMPRA",
                "price": last_close,
                "reason": "; ".join(buy_reasons),
            }
        if sell_ok and not buy_ok:
            return {
                "signal": "VENDA",
                "price": last_close,
                "reason": "; ".join(sell_reasons),
            }

        return {"signal": None, "reason": "Nenhuma condição atendida"}

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        for spec in self.strategy.get("indicators", []):
            ind_type = spec.get("type", "").upper()
            ind_id = spec.get("id", ind_type.lower())
            column = spec.get("column", "close")
            length = spec.get("length", 14)

            try:
                if ind_type == "RSI":
                    df[ind_id] = ta.rsi(df[column], length=length)
                elif ind_type == "EMA":
                    df[ind_id] = ta.ema(df[column], length=length)
                elif ind_type == "SMA":
                    df[ind_id] = ta.sma(df[column], length=length)
                elif ind_type == "ATR":
                    df[ind_id] = ta.atr(df["high"], df["low"], df["close"], length=length)
                elif ind_type == "ADX":
                    adx = ta.adx(df["high"], df["low"], df["close"], length=length)
                    if adx is not None and "ADX_14" in adx.columns:
                        df[ind_id] = adx[f"ADX_{length}"]
                elif ind_type == "MACD":
                    fast = spec.get("fast", 12)
                    slow = spec.get("slow", 26)
                    signal = spec.get("signal", 9)
                    macd = ta.macd(df[column], fast=fast, slow=slow, signal=signal)
                    if macd is not None:
                        hist_col = f"MACDh_{fast}_{slow}_{signal}"
                        macd_col = f"MACD_{fast}_{slow}_{signal}"
                        sig_col = f"MACDs_{fast}_{slow}_{signal}"
                        if hist_col in macd.columns:
                            df["macd_hist"] = macd[hist_col]
                        if macd_col in macd.columns:
                            df["macd"] = macd[macd_col]
                        if sig_col in macd.columns:
                            df["macd_signal"] = macd[sig_col]
                elif ind_type == "BBANDS":
                    bb = ta.bbands(df[column], length=length)
                    if bb is not None:
                        df[f"{ind_id}_lower"] = bb.iloc[:, 0]
                        df[f"{ind_id}_mid"] = bb.iloc[:, 1]
                        df[f"{ind_id}_upper"] = bb.iloc[:, 2]
                elif ind_type == "STOCH":
                    stoch = ta.stoch(df["high"], df["low"], df["close"])
                    if stoch is not None:
                        df[f"{ind_id}_k"] = stoch.iloc[:, 0]
                        df[f"{ind_id}_d"] = stoch.iloc[:, 1]
            except Exception as exc:
                logger.warning("Erro ao calcular %s: %s", ind_type, exc)

        return df

    def _passes_filters(self, df: pd.DataFrame) -> bool:
        row = df.iloc[-1]
        for filt in self.strategy.get("filters", []):
            ftype = filt.get("type")
            if ftype == "min_atr_pct":
                atr_col = filt.get("indicator", "atr")
                min_pct = float(filt.get("min_pct", 0.0005))
                if atr_col in row and pd.notna(row[atr_col]):
                    atr_pct = float(row[atr_col]) / float(row["close"])
                    if atr_pct < min_pct:
                        return False
        return True

    def _evaluate_conditions(
        self, df: pd.DataFrame, conditions: list[dict[str, Any]]
    ) -> tuple[bool, list[str]]:
        if not conditions:
            return False, []

        logic = self.strategy.get("logic", "AND").upper()
        results: list[bool] = []
        reasons: list[str] = []

        prev = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
        curr = df.iloc[-1]

        for cond in conditions:
            ok, reason = self._check_condition(prev, curr, cond)
            results.append(ok)
            if ok and reason:
                reasons.append(reason)

        if logic == "OR":
            passed = any(results)
        else:
            passed = all(results)

        return passed, reasons

    def _check_condition(
        self, prev: pd.Series, curr: pd.Series, cond: dict[str, Any]
    ) -> tuple[bool, str]:
        indicator = cond.get("indicator", "close")
        operator = cond.get("operator", ">")
        value = cond.get("value")
        reference = cond.get("reference")

        left = self._resolve_value(curr, indicator)
        if reference:
            right = self._resolve_value(curr, reference)
        elif value is not None:
            right = float(value)
        else:
            return False, ""

        if left is None or right is None or pd.isna(left) or pd.isna(right):
            return False, ""

        if operator == "crossover_above":
            prev_left = self._resolve_value(prev, indicator)
            prev_right = self._resolve_value(prev, reference) if reference else float(value)
            ok = prev_left is not None and prev_right is not None and prev_left <= prev_right and left > right
        elif operator == "crossover_below":
            prev_left = self._resolve_value(prev, indicator)
            prev_right = self._resolve_value(prev, reference) if reference else float(value)
            ok = prev_left is not None and prev_right is not None and prev_left >= prev_right and left < right
        elif operator == "<":
            ok = left < right
        elif operator == "<=":
            ok = left <= right
        elif operator == ">":
            ok = left > right
        elif operator == ">=":
            ok = left >= right
        elif operator == "==":
            ok = abs(left - right) < 1e-9
        else:
            ok = False

        reason = f"{indicator} {operator} {reference or value}" if ok else ""
        return ok, reason

    @staticmethod
    def _resolve_value(row: pd.Series, key: str) -> float | None:
        if key == "close":
            return float(row["close"]) if "close" in row else None
        if key in row.index and pd.notna(row[key]):
            return float(row[key])
        return None
