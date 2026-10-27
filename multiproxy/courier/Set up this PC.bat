@echo off
setlocal
title MultiProxy Courier - set up this device
cd /d "%~dp0"

echo ======================================================================
echo  MultiProxy Courier
echo.
echo  This sets up this computer to run AI models for your main device, so
echo  Your main device can keep all its graphics card power for Skyrim.
echo  You only need to do this once. After setup, you manage everything
echo  right from your main device.
echo.
echo  Setup needs PowerShell 7, a free update from Microsoft.
echo ======================================================================
echo.

where pwsh >nul 2>nul
if %ERRORLEVEL% EQU 0 goto :run

echo PowerShell 7 isn't on this computer yet.
echo I can install it for you now. It takes about
echo a minute. (Windows may ask for permission.)
echo.
choice /C YN /N /M "Install PowerShell 7 now? [Y/N] "
if errorlevel 2 goto :declined

where winget >nul 2>nul
if %ERRORLEVEL% NEQ 0 goto :nowinget

echo.
echo Installing PowerShell 7 (please wait)...
call winget install --id Microsoft.PowerShell -e --accept-source-agreements --accept-package-agreements >nul 2>&1
if %ERRORLEVEL% NEQ 0 goto :installfailed

rem The new program isn't on this window's PATH yet, so look for it where it installs.
if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" (
    set "PATH=%ProgramFiles%\PowerShell\7;%PATH%"
    goto :run
)
goto :installfailed

:run
echo.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-courier.ps1" %*
goto :end

:declined
echo.
echo Setup can't continue without PowerShell 7.
echo You can install it yourself from https://aka.ms/powershell and then
echo double-click "Set up this PC" again.
goto :end

:nowinget
echo.
echo The Windows app installer is missing, so I can't install PowerShell 7.
echo Please install it from https://aka.ms/powershell and then
echo double-click "Set up this PC" again.
goto :end

:installfailed
echo.
echo PowerShell 7 didn't install. You can try again, or install it manually
echo from https://aka.ms/powershell and double-click "Set up this PC".

:end
echo.
pause
