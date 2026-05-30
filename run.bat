@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Binomo Signal Generator
cd /d "%~dp0"

set "PY=.\.venv\Scripts\python.exe"

echo.
echo  Binomo Signal Generator - One-Click Launcher
echo  --------------------------------------------
echo.

if not exist "%PY%" (
    echo  [ERRO] Python do ambiente virtual nao encontrado.
    echo.
    echo  Caminho esperado: .venv\Scripts\python.exe
    echo.
    echo  Configure uma unica vez no terminal:
    echo    python -m venv .venv
    echo    .\.venv\Scripts\pip.exe install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo  [ERRO] Arquivo .env nao encontrado na raiz do projeto.
    echo  Copie .env.example para .env e preencha AUTH_TOKEN e DEVICE_ID.
    echo.
    pause
    exit /b 1
)

echo  [1/2] Verificando estrategias locais...
"%PY%" preflight_check.py
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

echo  [OK] Estrategias validadas.
echo.
echo  [2/2] Iniciando monitoramento...
echo.

"%PY%" main.py

echo.
echo  Robo encerrado.
pause
