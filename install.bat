@echo off
setlocal
cd /d "%~dp0"
echo === AutoAIConsole install ===

echo [1/6] Checking whether AutoAIConsole (this venv) is already running...
python scripts\check_running.py --venv-dir "%CD%\venv"
if errorlevel 2 goto running_check_warn
if errorlevel 1 goto already_running
goto running_check_done

:already_running
echo.
echo   [ERROR] AutoAIConsole is currently running from this venv.
echo   [ERROR] Please close it first, then run install.bat again.
echo   [ERROR] install.bat will not terminate it for you.
echo.
exit /b 1

:running_check_warn
echo   [WARN] Could not verify running processes. Continuing anyway.

:running_check_done

echo [2/6] Cleaning up stale install artifacts...
for %%F in ("venv\Scripts\*.deleteme") do (
    echo   Removing leftover file: %%~nxF
    del /q "%%F"
)
for /d %%D in ("venv\Lib\site-packages\~*") do (
    echo   Removing stale pip remnant: %%~nxD
    rd /s /q "%%D"
    if exist "%%D" echo   [WARN] Could not remove %%~nxD -- ignoring
)

echo [3/6] Checking git submodules...
git rev-parse --git-dir >nul 2>&1
if errorlevel 1 (
    echo   [INFO] Not a git checkout or git not installed -- skipping submodules.
) else (
    git submodule update --init --recursive
    if errorlevel 1 echo   [WARN] git submodule update failed -- continuing
)

if not exist "venv\Scripts\python.exe" (
    echo [4/6] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo   [ERROR] Failed to create venv. Check python on PATH.
        exit /b 1
    )
) else (
    echo [4/6] Using existing virtual environment
)

echo [5/6] Installing AutoAIConsole package and core dependencies...
"venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo   [ERROR] pip upgrade failed.
    exit /b 1
)
"venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
    echo   [ERROR] Core installation failed.
    exit /b 1
)

echo   Installing optional desktop GUI shell (pywebview)...
"venv\Scripts\python.exe" -m pip install -e ".[gui]"
if errorlevel 1 (
    echo   [WARN] Optional pywebview install failed. Browser GUI mode still works.
)

echo [6/6] Running environment self-check...
"venv\Scripts\python.exe" scripts\check_env.py --expected-root "%CD%"
if errorlevel 1 (
    echo   [ERROR] Environment self-check failed.
    exit /b 1
)

echo.
echo === AutoAIConsole install completed successfully ===
echo Run launch_gui.bat to start the GUI app.
exit /b 0
