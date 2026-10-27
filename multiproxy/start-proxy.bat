@echo off
title SkyrimNet MultiProxy
cd /d "%~dp0"
echo ======================================================================
echo  Keep this window open while Skyrim is using MultiProxy.
echo  To stop it when you're done, either:
echo    - Press Ctrl+C in this window
echo    - Click the X to close this window
echo ======================================================================
echo.

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 multiproxy.py
    goto :end
)

python multiproxy.py
if %ERRORLEVEL% EQU 49 goto :no_python
if %ERRORLEVEL% EQU 9009 goto :no_python
goto :end

:no_python
echo.
echo ======================================================================
echo  Couldn't find Python on this computer.
echo  Install Python from https://www.python.org/downloads/ and tick
echo  Tick "Add Python to PATH" during setup, then run this again.
echo ======================================================================

:end
pause
