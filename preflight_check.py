"""
Pré-validação one-click — verifica estratégias .txt vs .py compilado (mtime).
Executado pelo run.bat antes de main.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

STRATEGIES_DIR = Path("strategies")
COMPILED_DIR = Path("compiled_strategies")

ERROR_ALTERADA = (
    "ERRO: Estratégia alterada. Por favor, solicite a compilação no Cursor IA."
)
ERROR_AUSENTE = (
    "ERRO: Estratégia sem compilação. Por favor, solicite a compilação no Cursor IA."
)


def _banner(title: str) -> None:
    line = "=" * 60
    print()
    print(line)
    print(title)
    print(line)
    print()


def check_strategies() -> int:
    if not STRATEGIES_DIR.is_dir():
        _banner("ERRO: Pasta strategies/ não encontrada.")
        return 1

    txt_files = sorted(STRATEGIES_DIR.glob("*.txt"))
    if not txt_files:
        _banner("ERRO: Nenhum arquivo .txt encontrado em strategies/.")
        return 1

    COMPILED_DIR.mkdir(exist_ok=True)

    for source in txt_files:
        compiled = COMPILED_DIR / f"{source.stem}.py"
        name = source.name

        if not compiled.is_file():
            _banner(ERROR_AUSENTE)
            print(f"  Documento:  strategies/{name}")
            print(f"  Esperado:   compiled_strategies/{compiled.name}")
            print()
            print("  Arraste o .txt para o chat do Cursor e peça o arquivo .py")
            print(f"  com a função analisar(df) salvo em compiled_strategies/{compiled.name}")
            print()
            return 1

        if os.path.getmtime(source) > os.path.getmtime(compiled):
            _banner(ERROR_ALTERADA)
            print(f"  Arquivo modificado: strategies/{name}")
            print(f"  Compilado desatualizado: compiled_strategies/{compiled.name}")
            print()
            print("  Arraste o .txt atualizado para o Cursor IA e peça a recompilação.")
            print()
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(check_strategies())
