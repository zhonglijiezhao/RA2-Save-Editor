@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==================================================
echo   FOREGROUND / DEBUG MODE
echo   Interface errors will stay visible on this window.
echo ==================================================
echo.
if exist "D:\py\python.exe" (
    "D:\py\python.exe" "launcher.py"
) else (
    python "launcher.py"
)
echo.
echo ---- exited with code %errorlevel% ----
pause
