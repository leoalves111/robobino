"""
Menu interativo de seleção de estratégias compiladas.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from strategy_loader import LoadedStrategy, list_compiled_files, load_strategy

console = Console()

HELPER_PREFIXES = ("_",)
HELPER_NAMES = frozenset({"__init__.py"})


def list_selectable_strategies(compiled_dir: Path | str) -> list[Path]:
    """Lista .py de estratégia, excluindo helpers e arquivos de suporte."""
    base = Path(compiled_dir)
    return [
        p
        for p in list_compiled_files(base)
        if p.name not in HELPER_NAMES and not p.name.startswith(HELPER_PREFIXES)
    ]


def _peek_strategy_name(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8")[:4096]
        match = re.search(r'STRATEGY_NAME\s*=\s*["\'](.+?)["\']', head)
        if match:
            return match.group(1)
    except OSError:
        pass
    return path.stem.replace("_", " ").title()


def _build_menu_table(strategies: list[Path]) -> Table:
    table = Table(show_header=True, header_style="bold cyan", expand=True)
    table.add_column("#", style="bold yellow", width=4, justify="right")
    table.add_column("Arquivo", style="white")
    table.add_column("Nome", style="magenta")

    for index, path in enumerate(strategies, start=1):
        table.add_row(str(index), path.name, _peek_strategy_name(path))
    return table


def load_auto_strategy(
    compiled_dir: Path | str = "compiled_strategies",
    strategy_number: int | None = None,
) -> LoadedStrategy:
    """Carrega estratégia pelo número em bot_settings.txt (sem input())."""
    from bot_settings import DEFAULT_STRATEGY_NUMBER, load_strategy_by_number, read_bot_settings

    n = strategy_number
    if n is None:
        n = read_bot_settings().strategy_number
    return load_strategy_by_number(compiled_dir, n or DEFAULT_STRATEGY_NUMBER)


def prompt_strategy_selection(
    compiled_dir: Path | str = "compiled_strategies",
) -> LoadedStrategy:
    """
    Exibe menu numerado e retorna a estratégia escolhida.
    Repete a pergunta até receber uma opção válida.
    """
    base = Path(compiled_dir)
    strategies = list_selectable_strategies(base)

    if not strategies:
        console.print(
            f"\n[bold red]Nenhuma estratégia encontrada em {base}/[/bold red]\n"
            "Compile um .py em compiled_strategies/ antes de iniciar.\n"
        )
        sys.exit(1)

    console.print()
    console.print(
        Panel(
            _build_menu_table(strategies),
            title="[bold cyan]SELEÇÃO DE ESTRATÉGIA[/bold cyan]",
            subtitle="Escolha qual módulo usar no monitoramento",
            border_style="cyan",
        )
    )
    console.print()

    while True:
        raw = console.input("[bold]Digite o número da estratégia:[/bold] ").strip()

        if not raw.isdigit():
            console.print("[red]Opção inválida. Informe apenas o número da lista.[/red]\n")
            continue

        choice = int(raw)
        if choice < 1 or choice > len(strategies):
            console.print(
                f"[red]Opção inválida. Escolha entre 1 e {len(strategies)}.[/red]\n"
            )
            continue

        selected_path = strategies[choice - 1]
        loaded = load_strategy(selected_path, base)

        console.print()
        console.print(
            f"[bold green]Estratégia [{loaded.name}] carregada com sucesso.[/bold green]"
        )
        console.print()
        return loaded
