@echo off
chcp 65001 >nul
title Music Helper - Instalacion
cd /d "%~dp0"

echo ========================================
echo   Music Helper - Instalacion automatica
echo ========================================
echo.

set PYCMD=
py -3.12 --version >nul 2>&1 && set PYCMD=py -3.12
if not defined PYCMD (
    py -3.11 --version >nul 2>&1 && set PYCMD=py -3.11
)
if not defined PYCMD (
    python --version >nul 2>&1 && set PYCMD=python
)
if not defined PYCMD (
    echo [ERROR] No encontre Python.
    echo Instala Python 3.11 o 3.12 desde https://www.python.org/downloads/
    echo y marca la casilla "Add Python to PATH".
    pause
    exit /b 1
)

echo Usando: %PYCMD%
%PYCMD% --version
echo.

if not exist "venv" (
    echo Creando entorno virtual...
    %PYCMD% -m venv venv
    if errorlevel 1 (
        echo [ERROR] No pude crear el entorno virtual.
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat

echo Instalando dependencias (la primera vez tarda unos minutos)...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la instalacion de dependencias.
    echo Si usas Python 3.13 o superior, instala Python 3.11 o 3.12 y borra la carpeta "venv".
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Iniciando Music Helper...
echo ========================================
python main.py

if errorlevel 1 (
    echo.
    echo La aplicacion se cerro con un error.
    pause
)
