@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Run: py -3.13 -m venv .venv
    pause
    exit /b 1
)

echo ==========================================
echo Running bot tests (pytest)
echo ==========================================
".venv\Scripts\python.exe" -m pytest -q
if %ERRORLEVEL% neq 0 (
    echo Tests failed. Fix errors before starting the bot.
    pause
    exit /b 1
)
echo Tests passed successfully.
echo ==========================================

".venv\Scripts\python.exe" -m app.main
pause