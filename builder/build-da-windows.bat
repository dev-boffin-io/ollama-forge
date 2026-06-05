@echo off
REM =========================================================================
REM  build-da-windows.bat
REM  Builds  da.exe  +  ollama-main.exe  for Windows (x64).
REM  Output: bin\dev-assist\
REM
REM  Full isolation: fresh venv, no system site-packages.
REM  Run ON Windows with Python 3.10+ installed.
REM  Usage: builder\build-da-windows.bat
REM =========================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "DA_DIR=%PROJECT_ROOT%\dev-assist"
set "VENV_DIR=%SCRIPT_DIR%.venv-da-win"
set "OUT_DIR=%PROJECT_ROOT%\bin\dev-assist"

echo.
echo ===========================================================
echo   dev-assist -- Windows Build  (da + ollama-main)
echo ===========================================================
echo.

REM ── Detect Python ──────────────────────────────────────────────────────
set "PY_BIN="
for %%P in (python3.12 python3.11 python3.10 python3 python) do (
    where %%P >nul 2>&1 && set "PY_BIN=%%P" && goto :found_py
)
echo [ERROR] No Python interpreter found.
exit /b 1
:found_py
echo [→] Python: %PY_BIN%

REM ── Validate sources ────────────────────────────────────────────────────
if not exist "%DA_DIR%\main.py" (
    echo [ERROR] dev-assist entry not found: %DA_DIR%\main.py
    exit /b 1
)
if not exist "%DA_DIR%\ollama-main\main.py" (
    echo [ERROR] ollama-main entry not found: %DA_DIR%\ollama-main\main.py
    exit /b 1
)

REM ── Fresh isolated venv ─────────────────────────────────────────────────
echo [→] Creating isolated build environment...
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
%PY_BIN% -m venv "%VENV_DIR%"
if errorlevel 1 ( echo [ERROR] venv creation failed & exit /b 1 )

call "%VENV_DIR%\Scripts\activate.bat"
python -m pip install --quiet --upgrade pip setuptools wheel

REM ── Install deps ────────────────────────────────────────────────────────
echo [→] Installing dependencies...
if exist "%DA_DIR%\requirements.txt" (
    pip install --quiet -r "%DA_DIR%\requirements.txt"
) else (
    pip install --quiet chainlit crewai requests packaging openai anthropic rich typer click httpx
)

REM ── PyInstaller ─────────────────────────────────────────────────────────
echo [→] Installing PyInstaller...
pip install --quiet pyinstaller pyinstaller-hooks-contrib
pip install --quiet --force-reinstall setuptools

set "PYINSTALLER_BIN=%VENV_DIR%\Scripts\pyinstaller.exe"
if not exist "%PYINSTALLER_BIN%" (
    echo [ERROR] PyInstaller not found.
    exit /b 1
)

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

REM ── Shared hidden imports (da) ───────────────────────────────────────────
set DA_HIDDEN=^
    --hidden-import chainlit ^
    --hidden-import chainlit.cli ^
    --hidden-import crewai ^
    --hidden-import crewai.agent ^
    --hidden-import crewai.task ^
    --hidden-import crewai.crew ^
    --hidden-import openai ^
    --hidden-import anthropic ^
    --hidden-import requests ^
    --hidden-import httpx ^
    --hidden-import rich ^
    --hidden-import rich.console ^
    --hidden-import typer ^
    --hidden-import click ^
    --hidden-import asyncio ^
    --hidden-import importlib.metadata ^
    --hidden-import pkg_resources

set DA_EXCLUDED=^
    --exclude-module PyQt5 ^
    --exclude-module PyQt6 ^
    --exclude-module PySide2 ^
    --exclude-module PySide6 ^
    --exclude-module tkinter ^
    --exclude-module _tkinter ^
    --exclude-module pytest ^
    --exclude-module torch ^
    --exclude-module tensorflow ^
    --exclude-module tensorboard ^
    --exclude-module sklearn ^
    --exclude-module scipy ^
    --exclude-module matplotlib

set OLLAMA_HIDDEN=^
    --hidden-import requests ^
    --hidden-import packaging ^
    --hidden-import packaging.version ^
    --hidden-import subprocess ^
    --hidden-import argparse ^
    --hidden-import tempfile ^
    --hidden-import shutil ^
    --hidden-import json ^
    --hidden-import re

set OLLAMA_EXCLUDED=^
    --exclude-module PyQt5 ^
    --exclude-module PyQt6 ^
    --exclude-module tkinter ^
    --exclude-module pytest ^
    --exclude-module torch ^
    --exclude-module tensorflow

REM ── Build da.exe ─────────────────────────────────────────────────────────
echo [→] Building da.exe...
if exist "%DA_DIR%\build" rmdir /s /q "%DA_DIR%\build"
if exist "%DA_DIR%\dist"  rmdir /s /q "%DA_DIR%\dist"

cd /d "%DA_DIR%"
"%PYINSTALLER_BIN%" ^
    --onefile ^
    --console ^
    --name da ^
    --clean ^
    %DA_HIDDEN% ^
    %DA_EXCLUDED% ^
    main.py
if errorlevel 1 ( echo [ERROR] da.exe build failed & exit /b 1 )

if not exist "%DA_DIR%\dist\da.exe" (
    echo [ERROR] da.exe not found in dist\
    exit /b 1
)
move /y "%DA_DIR%\dist\da.exe" "%OUT_DIR%\da.exe" >nul
echo [✓] %OUT_DIR%\da.exe

REM ── Build ollama-main.exe ─────────────────────────────────────────────────
echo [→] Building ollama-main.exe...
if exist "%DA_DIR%\build" rmdir /s /q "%DA_DIR%\build"
if exist "%DA_DIR%\dist"  rmdir /s /q "%DA_DIR%\dist"

"%PYINSTALLER_BIN%" ^
    --onefile ^
    --console ^
    --name ollama-main ^
    --clean ^
    %OLLAMA_HIDDEN% ^
    %OLLAMA_EXCLUDED% ^
    ollama-main\main.py
if errorlevel 1 ( echo [ERROR] ollama-main.exe build failed & exit /b 1 )

if not exist "%DA_DIR%\dist\ollama-main.exe" (
    echo [ERROR] ollama-main.exe not found in dist\
    exit /b 1
)
move /y "%DA_DIR%\dist\ollama-main.exe" "%OUT_DIR%\ollama-main.exe" >nul
echo [✓] %OUT_DIR%\ollama-main.exe

REM ── Cleanup ─────────────────────────────────────────────────────────────
if exist "%DA_DIR%\build" rmdir /s /q "%DA_DIR%\build"
if exist "%DA_DIR%\dist"  rmdir /s /q "%DA_DIR%\dist"
call "%VENV_DIR%\Scripts\deactivate.bat" 2>nul
if exist "%VENV_DIR%"     rmdir /s /q "%VENV_DIR%"

echo.
echo ===========================================================
echo   dev-assist Windows build complete
echo   bin\dev-assist\
echo     da.exe
echo     ollama-main.exe
echo ===========================================================
