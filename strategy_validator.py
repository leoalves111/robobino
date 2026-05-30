"""
Validação local de estratégias — comparação por data de modificação (mtime).
Sem dependências de IA ou rede externa.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

console = Console()
SUPPORTED_EXTENSIONS = {".txt", ".pdf"}


@dataclass
class ValidationResult:
    ok: bool
    source_file: Path
    compiled_file: Path
    strategy_name: str
    message: str


class StrategyValidationError(Exception):
    """Estratégia ausente ou desatualizada — compilação manual necessária."""


def scan_source_documents(strategies_dir: Path | str) -> list[Path]:
    base = Path(strategies_dir)
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def compiled_path_for(source: Path, compiled_dir: Path | str) -> Path:
    return Path(compiled_dir) / f"{source.stem}.py"


def needs_compilation(source: Path, compiled: Path) -> bool:
    if not compiled.is_file():
        return True
    return os.path.getmtime(source) > os.path.getmtime(compiled)


def compilation_message(source: Path) -> str:
    name = source.stem
    ext = source.suffix or ".txt"
    return (
        f"Estratégia [{name}] precisa de compilação.\n"
        f"Por favor, arraste o arquivo [{name}{ext}] para o chat do Cursor e peça "
        f"para ele gerar o código Python com a função analisar(df) e salvar em "
        f"/compiled_strategies/{name}.py"
    )


def check_strategy(source: Path, compiled_dir: Path | str) -> str | None:
    """Retorna mensagem de erro se compilação for necessária; None se OK."""
    compiled = compiled_path_for(source, compiled_dir)
    if needs_compilation(source, compiled):
        return compilation_message(source)
    return None


def resolve_source_file(strategies_dir: Path | str, strategy_ref: str) -> Path:
    base = Path(strategies_dir)
    ref = strategy_ref.strip()
    if not ref:
        raise StrategyValidationError(
            "STRATEGY_FILE não definido no .env. "
            "Orquestrador autonomo — STRATEGY_FILE nao e necessario."
        )

    path = Path(ref)
    if path.is_file():
        return path

    if not path.suffix:
        for ext in SUPPORTED_EXTENSIONS:
            candidate = base / f"{path.name}{ext}"
            if candidate.is_file():
                return candidate
        candidate = base / f"{path.name}.txt"
        if candidate.is_file():
            return candidate
    else:
        candidate = base / path.name
        if candidate.is_file():
            return candidate

    available = ", ".join(p.name for p in scan_source_documents(base)) or "(nenhum)"
    raise StrategyValidationError(
        f"Estratégia '{strategy_ref}' não encontrada em {base}.\n"
        f"Disponíveis: {available}\n"
        f"Coloque o documento (.txt/.pdf) em strategies/ e o .py compilado em compiled_strategies/."
    )


def validate_selected_strategy(
    strategy_ref: str,
    strategies_dir: Path | str = "strategies",
    compiled_dir: Path | str = "compiled_strategies",
) -> ValidationResult:
    source = resolve_source_file(strategies_dir, strategy_ref)
    compiled = compiled_path_for(source, compiled_dir)

    error = check_strategy(source, compiled_dir)
    if error:
        raise StrategyValidationError(error)

    return ValidationResult(
        ok=True,
        source_file=source,
        compiled_file=compiled,
        strategy_name=source.stem,
        message="Estratégia validada — compilado atualizado",
    )


def validate_registered_compiled(
    compiled_dir: Path | str = "compiled_strategies",
) -> list[ValidationResult]:
    """Valida catálogo oficial — só .py em compiled_strategies/ (sem .txt obrigatório)."""
    from strategy_registry import STRATEGY_CATALOG, validate_compiled_dir

    base = Path(compiled_dir)
    missing = validate_compiled_dir(base)
    if missing:
        raise StrategyValidationError(
            "Estrategias ausentes em compiled_strategies/:\n"
            + "\n".join(f"  - {n}" for n in missing)
            + "\n\nVeja strategy_registry.py e adicione os .py faltantes."
        )

    results: list[ValidationResult] = []
    for entry in STRATEGY_CATALOG:
        compiled = base / entry.filename
        results.append(
            ValidationResult(
                ok=True,
                source_file=compiled,
                compiled_file=compiled,
                strategy_name=entry.name,
                message="OK",
            )
        )
    return results


def validate_all_strategies(
    strategies_dir: Path | str = "strategies",
    compiled_dir: Path | str = "compiled_strategies",
) -> list[ValidationResult]:
    documents = scan_source_documents(strategies_dir)
    if not documents:
        return validate_registered_compiled(compiled_dir)

    pending: list[str] = []
    results: list[ValidationResult] = []

    for source in documents:
        compiled = compiled_path_for(source, compiled_dir)
        error = check_strategy(source, compiled_dir)
        if error:
            pending.append(error)
        else:
            results.append(
                ValidationResult(
                    ok=True,
                    source_file=source,
                    compiled_file=compiled,
                    strategy_name=source.stem,
                    message="OK",
                )
            )

    if pending:
        raise StrategyValidationError("\n\n".join(pending))

    registry = validate_registered_compiled(compiled_dir)
    results.extend(registry)
    return results


def run_startup_validation(
    strategies_dir: str | Path = "strategies",
    compiled_dir: str | Path = "compiled_strategies",
    *,
    headless: bool = False,
) -> list[ValidationResult]:
    """Valida integridade local de todos os documentos fonte."""
    import logging

    log = logging.getLogger(__name__)
    results = validate_all_strategies(strategies_dir, compiled_dir)

    if headless:
        log.info("Validacao de estrategias: %d modulo(s) .py OK", len(results))
        for item in results:
            log.info("  OK %s", item.compiled_file.name)
    else:
        import logging

        log = logging.getLogger(__name__)
        log.info("Validacao: %d estrategia(s) .py OK", len(results))
    return results


def exit_on_validation_error(exc: StrategyValidationError, *, headless: bool = False) -> None:
    import logging

    if headless:
        logging.getLogger(__name__).critical("Compilacao necessaria: %s", exc)
    else:
        console.print(f"\n[bold red]✖ COMPILAÇÃO NECESSÁRIA[/bold red]\n")
        console.print(str(exc))
        console.print()
    sys.exit(1)
