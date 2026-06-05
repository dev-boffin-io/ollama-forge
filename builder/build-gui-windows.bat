@echo off
REM =========================================================================
REM  build-gui-windows.bat
REM  Builds Ollama-ai-gui.exe + Ollama-ai-manager.exe for Windows (x64).
REM  Output: bin\Ollama-GUI\
REM
REM  Full isolation: fresh venv, PyQt6 from pip.
REM  Run ON Windows with Python 3.10+ installed.
REM  Usage: builder\build-gui-windows.bat
REM =========================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "GUI_DIR=%PROJECT_ROOT%\gui"
set "VENV_DIR=%SCRIPT_DIR%.venv-gui-win"
set "OUT_DIR=%PROJECT_ROOT%\bin\Ollama-GUI"

echo.
echo ===========================================================
echo   Ollama GUI -- Windows Build
echo ===========================================================
echo.

REM ── Detect Python ──────────────────────────────────────────────────────
set "PY_BIN="
for %%P in (python3.12 python3.11 python3.10 python3 python) do (
    where %%P >nul 2>&1 && set "PY_BIN=%%P" && goto :found_py
)
echo [ERROR] No Python interpreter found. Install Python 3.10+ first.
exit /b 1
:found_py
echo [→] Python: %PY_BIN%

REM ── Validate source ─────────────────────────────────────────────────────
if not exist "%GUI_DIR%\main.py" (
    echo [ERROR] GUI source not found: %GUI_DIR%\main.py
    exit /b 1
)

REM ── Fresh isolated venv ─────────────────────────────────────────────────
echo [→] Creating isolated build environment...
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
%PY_BIN% -m venv "%VENV_DIR%"
if errorlevel 1 ( echo [ERROR] venv creation failed & exit /b 1 )

call "%VENV_DIR%\Scripts\activate.bat"

python -m pip install --quiet --upgrade pip setuptools wheel

REM ── PyQt6 ──────────────────────────────────────────────────────────────
echo [→] Installing PyQt6...
pip install --quiet "PyQt6>=6.6.0" "PyQt6-Qt6>=6.6.0" "PyQt6-sip>=13.6.0"

echo [→] Installing runtime dependencies...
pip install --quiet requests packaging numpy sentence-transformers faiss-cpu pypdf python-docx

REM ── PyInstaller ─────────────────────────────────────────────────────────
echo [→] Installing PyInstaller...
pip install --quiet pyinstaller pyinstaller-hooks-contrib
pip install --quiet --force-reinstall setuptools

set "PYINSTALLER_BIN=%VENV_DIR%\Scripts\pyinstaller.exe"
if not exist "%PYINSTALLER_BIN%" (
    echo [ERROR] PyInstaller not found at %PYINSTALLER_BIN%
    exit /b 1
)

REM ── Output dir ──────────────────────────────────────────────────────────
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
if exist "%GUI_DIR%\build"  rmdir /s /q "%GUI_DIR%\build"
if exist "%GUI_DIR%\dist"   rmdir /s /q "%GUI_DIR%\dist"

REM ── Shared flags ────────────────────────────────────────────────────────
set HIDDEN=^
    --hidden-import numpy ^
    --hidden-import faiss ^
    --hidden-import sentence_transformers ^
    --hidden-import sentence_transformers.models ^
    --hidden-import sentence_transformers.losses ^
    --hidden-import pypdf ^
    --hidden-import pypdf._reader ^
    --hidden-import docx ^
    --hidden-import docx.oxml ^
    --hidden-import requests ^
    --hidden-import urllib.request ^
    --hidden-import threading ^
    --hidden-import tempfile ^
    --hidden-import shutil ^
    --hidden-import ollama_manager ^
    --hidden-import ollama_manager.window ^
    --hidden-import ollama_manager.workers ^
    --hidden-import ollama_manager.helpers

set EXCLUDED=^
    --exclude-module torch ^
    --exclude-module torchvision ^
    --exclude-module torchaudio ^
    --exclude-module triton ^
    --exclude-module sklearn ^
    --exclude-module scipy ^
    --exclude-module nltk ^
    --exclude-module nvidia ^
    --exclude-module tensorflow ^
    --exclude-module tensorboard ^
    --exclude-module pytest ^
    --exclude-module tkinter ^
    --exclude-module _tkinter

REM ── Build Ollama-ai-gui.exe ─────────────────────────────────────────────
echo [→] Building Ollama-ai-gui.exe...
cd /d "%GUI_DIR%"
"%PYINSTALLER_BIN%" ^
    --onefile ^
    --windowed ^
    --name Ollama-ai-gui ^
    --clean ^
    %HIDDEN% ^
    %EXCLUDED% ^
    main.py
if errorlevel 1 ( echo [ERROR] Ollama-ai-gui build failed & exit /b 1 )

REM ── Build Ollama-ai-manager.exe ─────────────────────────────────────────
echo [→] Building Ollama-ai-manager.exe...
"%PYINSTALLER_BIN%" ^
    --onefile ^
    --windowed ^
    --name Ollama-ai-manager ^
    --clean ^
    %HIDDEN% ^
    %EXCLUDED% ^
    manager_entry.py
if errorlevel 1 ( echo [ERROR] Ollama-ai-manager build failed & exit /b 1 )

REM ── Move binaries ───────────────────────────────────────────────────────
for %%N in (Ollama-ai-gui Ollama-ai-manager) do (
    if not exist "%GUI_DIR%\dist\%%N.exe" (
        echo [ERROR] %%N.exe not found in dist\
        exit /b 1
    )
    move /y "%GUI_DIR%\dist\%%N.exe" "%OUT_DIR%\%%N.exe" >nul
    echo [✓] %OUT_DIR%\%%N.exe
)

REM ── Cleanup ─────────────────────────────────────────────────────────────
if exist "%GUI_DIR%\build" rmdir /s /q "%GUI_DIR%\build"
if exist "%GUI_DIR%\dist"  rmdir /s /q "%GUI_DIR%\dist"
call "%VENV_DIR%\Scripts\deactivate.bat" 2>nul
if exist "%VENV_DIR%"      rmdir /s /q "%VENV_DIR%"

echo.
echo ===========================================================
echo   GUI Windows build complete
echo   bin\Ollama-GUI\
echo     Ollama-ai-gui.exe
echo     Ollama-ai-manager.exe
echo ===========================================================
