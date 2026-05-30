"""
Seleção de modo de execução após escolha da estratégia.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

RUN_MODES = {
    "1": ("normal", "Modo Normal", "Igual ao site: velas M5 reais (5 min) + preço Binomo ao vivo"),
    "2": ("simulation", "Modo Simulação", "Apenas teste: velas sintéticas a cada 8s (NAO e o M5 do site)"),
}


def prompt_run_mode() -> str:
    """Retorna 'normal' ou 'simulation'."""
    table = Table(show_header=True, header_style="bold cyan", expand=True)
    table.add_column("#", style="bold yellow", width=4, justify="right")
    table.add_column("Modo", style="white")
    table.add_column("Descrição", style="dim")

    for key, (_, label, desc) in RUN_MODES.items():
        table.add_row(key, label, desc)

    console.print()
    console.print(
        Panel(
            table,
            title="[bold cyan]MODO DE EXECUÇÃO[/bold cyan]",
            subtitle="Escolha como deseja monitorar",
            border_style="cyan",
        )
    )
    console.print()

    while True:
        raw = console.input("[bold]Digite o número do modo:[/bold] ").strip()
        if raw in RUN_MODES:
            mode_id, label, _ = RUN_MODES[raw]
            console.print()
            console.print(f"[bold green]{label} selecionado. Iniciando...[/bold green]")
            console.print()
            return mode_id
        console.print("[red]Opção inválida. Digite 1 (Normal) ou 2 (Simulação).[/red]\n")


def exit_if_missing_auth_for_normal() -> None:
    """Encerra se .env não tiver credenciais (somente modo normal)."""
    from dotenv import load_dotenv
    import os

    load_dotenv()
    auth = os.getenv("AUTH_TOKEN", "").strip()
    device = os.getenv("DEVICE_ID", "").strip()
    if not auth or not device:
        console.print(
            "\n[bold red]Modo Normal exige AUTH_TOKEN e DEVICE_ID no .env[/bold red]\n"
            "Use o Modo Simulação (2) para testar sem credenciais.\n"
        )
        sys.exit(1)
