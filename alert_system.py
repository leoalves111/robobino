"""
Painel visual estático com Rich Live — sem spam no terminal.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

logger = logging.getLogger(__name__)


@dataclass
class DashboardState:
    """Estado compartilhado lido pelo painel a cada segundo."""

    connected: bool = False
    asset_name: str = "Crypto IDX"
    asset_ric: str = "—"
    price: Optional[float] = None
    balance: Optional[float] = None
    candle_remaining_sec: int = 0
    candle_total_sec: int = 300
    candles_count: int = 0
    strategy_name: str = "—"
    strategy_file: str = "—"
    last_signal_direction: Optional[str] = None
    last_signal_time: Optional[datetime] = None
    last_signal_price: Optional[float] = None
    signal_status: str = "Aguardando sinal..."
    status_message: str = "Inicializando"
    mode: str = "READ-ONLY"
    market_regime: str = "—"
    market_rsi: float = 0.0
    market_adx: float = 0.0
    market_summary: str = "—"
    last_confidence: int = 0
    analysis_hint: str = "Aguardando sinal..."
    price_ticks: int = 0
    price_age_sec: int = -1
    history_loaded: bool = False
    cache_status: str = "—"


class Dashboard:
    """Painel Rich Live atualizado a cada segundo."""

    def __init__(self, state: DashboardState) -> None:
        self.state = state
        self._console = Console()
        self._live: Live | None = None

    def build(self) -> Panel:
        return Panel(
            Group(self._header(), self._metrics_table(), self._signal_section()),
            title="[bold cyan]BINOMO SIGNAL GENERATOR[/bold cyan]",
            subtitle=f"[dim]{self.state.mode}[/dim]",
            border_style="cyan",
            box=box.DOUBLE_EDGE,
            padding=(1, 2),
        )

    def _header(self) -> Text:
        conn = (
            "[bold green]● CONECTADO[/bold green]"
            if self.state.connected
            else "[bold red]● DESCONECTADO[/bold red]"
        )
        return Text.from_markup(
            f"{conn}  │  [white]{self.state.status_message}[/white]"
        )

    def _metrics_table(self) -> Table:
        table = Table(box=box.SIMPLE, show_header=True, expand=True, padding=(0, 1))
        table.add_column("Campo", style="dim", width=22)
        table.add_column("Valor", style="bold white")

        price = f"{self.state.price:.5f}" if self.state.price else "—"
        balance = f"${self.state.balance:,.2f}" if self.state.balance is not None else "—"

        mins, secs = divmod(max(0, self.state.candle_remaining_sec), 60)
        is_sim = self.state.mode.upper() == "SIMULAÇÃO"
        if is_sim:
            tf_label = f"{self.state.candle_total_sec}s (acelerado)"
            vela_label = "Vela simulada"
        else:
            tf_label = "M5 (5 min — igual ao site)"
            vela_label = "Vela M5"
        candle_status = f"{mins:02d}:{secs:02d} restantes ({tf_label})"
        progress = self._candle_progress_bar()

        table.add_row("Ativo", f"[cyan]{self.state.asset_name}[/cyan] ({self.state.asset_ric})")
        table.add_row("Preço atual", f"[yellow]{price}[/yellow]")
        ticks = str(self.state.price_ticks)
        if self.state.price_age_sec >= 0:
            ticks += f"  [dim](atualizado há {self.state.price_age_sec}s)[/dim]"
        table.add_row("Ticks de preço", ticks)
        hist = "[green]sim[/green]" if self.state.history_loaded else "[yellow]stream[/yellow]"
        table.add_row("Histórico", f"{hist}  [dim](cache: {self.state.cache_status})[/dim]")
        table.add_row(vela_label, f"{candle_status}\n{progress}")
        table.add_row("Velas carregadas", str(self.state.candles_count))
        table.add_row("Saldo", f"[green]{balance}[/green]")
        table.add_row("Estratégia", f"[magenta]{self.state.strategy_name}[/magenta]")
        table.add_row("Arquivo", f"[dim]{self.state.strategy_file}[/dim]")
        table.add_row("Mercado", self._format_regime())
        table.add_row("RSI / ADX", f"{self.state.market_rsi:.0f} / {self.state.market_adx:.0f}")
        table.add_row("Análise", f"[dim]{self.state.analysis_hint}[/dim]")
        return table

    def _format_regime(self) -> str:
        regime = self.state.market_regime.upper()
        colors = {"ALTA": "green", "BAIXA": "red", "LATERAL": "yellow"}
        color = colors.get(regime, "white")
        return f"[{color}]{regime}[/{color}]  [dim]{self.state.market_summary}[/dim]"

    def _candle_progress_bar(self) -> str:
        total = max(1, self.state.candle_total_sec)
        elapsed = total - max(0, self.state.candle_remaining_sec)
        pct = min(1.0, elapsed / total)
        filled = int(pct * 20)
        bar = "[green]" + "█" * filled + "[/green][dim]" + "░" * (20 - filled) + "[/dim]"
        return f"{bar} {pct * 100:.0f}%"

    def _signal_section(self) -> Panel:
        if self.state.last_signal_direction:
            direction = self.state.last_signal_direction.upper()
            color = "green" if direction == "COMPRA" else "red"
            arrow = "▲" if direction == "COMPRA" else "▼"
            ts = (
                self.state.last_signal_time.strftime("%Y-%m-%d %H:%M:%S")
                if self.state.last_signal_time
                else "—"
            )
            price = (
                f"{self.state.last_signal_price:.5f}"
                if self.state.last_signal_price
                else "—"
            )
            body = Text.from_markup(
                f"[bold {color}]{direction} {arrow}[/bold {color}]\n"
                f"Confiança: [bold]{self.state.last_confidence}%[/bold]\n"
                f"Horário: [cyan]{ts}[/cyan]\n"
                f"Preço:   [yellow]{price}[/yellow]"
            )
        else:
            hint = self.state.analysis_hint or self.state.signal_status
            body = Text.from_markup(f"[dim italic]{hint}[/dim italic]")

        return Panel(body, title="[bold]Último Sinal[/bold]", border_style="blue", padding=(0, 1))

    def start(self) -> Live:
        self._live = Live(
            self.build(),
            console=self._console,
            refresh_per_second=4,
            screen=True,
        )
        return self._live

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self.build())

    @staticmethod
    def play_signal_sound(direction: str) -> None:
        try:
            if sys.platform != "win32":
                return
            import winsound

            if direction.upper() in ("COMPRA", "CALL", "BUY"):
                winsound.Beep(880, 300)
                winsound.Beep(1100, 300)
            else:
                winsound.Beep(600, 300)
                winsound.Beep(440, 400)
        except Exception as exc:
            logger.debug("Som indisponível: %s", exc)

    def register_signal(self, direction: str, price: float, confidence: int = 0, reason: str = "") -> None:
        """Atualiza estado e emite som — sem print no terminal."""
        self.state.last_signal_direction = direction.upper()
        self.state.last_signal_time = datetime.now()
        self.state.last_signal_price = price
        self.state.last_confidence = confidence
        self.state.signal_status = reason or f"Sinal {direction.upper()} detectado"
        self.play_signal_sound(direction)
        logger.critical(
            "SINAL %s | conf=%d%% | %s | preço=%.5f",
            direction.upper(),
            confidence,
            self.state.last_signal_time.strftime("%Y-%m-%d %H:%M:%S"),
            price,
        )
