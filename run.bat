@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Binomo Signal Generator
cd /d "%~dp0"

set "PY=.\.venv\Scripts\python.exe"

echo.
echo  Binomo Signal Generator - Launcher autonomo
echo  ---------------------------------------------
echo.

if not exist "%PY%" (
    echo  [ERRO] Python do ambiente virtual nao encontrado.
    echo  Caminho: .venv\Scripts\python.exe
    echo  Configure: python -m venv .venv ^& pip install -r requirements.txt
    exit /b 1
)

if not exist ".env" (
    echo  [ERRO] Arquivo .env nao encontrado.
    echo  Copie .env.example para .env e preencha AUTH_TOKEN e DEVICE_ID.
    exit /b 1
)

echo  [1/2] Verificando estrategias .py...
"%PY%" preflight_check.py
if errorlevel 1 exit /b 1

echo.
echo  [2/2] Iniciando robô (24/7 autonomo)...
echo.

"%PY%" main.py
exit /b %ERRORLEVEL%
