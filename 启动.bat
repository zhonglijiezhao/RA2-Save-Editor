@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 1) packaged exe (recursively under build_dist) -- no Python needed
set "EXE="
for /R "build_dist" %%F in ("RA2*.exe") do if not defined EXE set "EXE=%%~fF"
if defined EXE (
    start "" "%EXE%"
    exit /b 0
)

rem 2) fallback: Python source launcher
set "PYW=D:\py\pythonw.exe"
set "PY=D:\py\python.exe"

if exist "%PYW%" goto :runw
if exist "%PY%" goto :runp
goto :bypath

:runw
start "" "%PYW%" "launcher.py"
goto :done

:runp
start "" "%PY%" "launcher.py"
goto :done

:bypath
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw "launcher.py"
    goto :done
)
where python >nul 2>nul
if not errorlevel 1 (
    start "" python "launcher.py"
    goto :done
)
echo.
echo  [ERROR] No packaged exe found and no Python interpreter available.
echo  Fix: keep the "build_dist" folder next to this file, or install Python 3.9+.
echo.
pause

:done
