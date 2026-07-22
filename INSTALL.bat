@echo off
REM Double-click this. It finds Python and runs the installer.
cd /d "%~dp0"
title thesolution crosshair installer

set "PY="

REM The py launcher ships with every python.org install and is the most
REM reliable. Fall back to python / python3 on PATH.
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"

if not defined PY (
    python --version >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    python3 --version >nul 2>nul
    if not errorlevel 1 set "PY=python3"
)

if not defined PY goto nopython

%PY% "%~dp0install-thesolution.py" %*
goto end

:nopython
echo.
echo   Python isn't installed, so the installer can't run.
echo.
echo   Get it from:  https://www.python.org/downloads/
echo   During setup, TICK the box that says "Add python.exe to PATH".
echo.
echo   Then double-click this file again.
echo.
echo   (If you just installed it, close this window and open a new one --
echo    it only picks up new programs on a fresh window.)
echo.
pause

:end
