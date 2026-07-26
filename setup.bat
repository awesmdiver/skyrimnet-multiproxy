@echo off
rem Double-click launcher for setup.ps1 -- always uses PowerShell 7 (pwsh.exe),
rem never Windows PowerShell 5.1, regardless of file associations.
setlocal

set "SCRIPT=%~dp0setup.ps1"

where pwsh.exe >nul 2>&1
if %ERRORLEVEL%==0 (
    pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
) else if exist "C:\Program Files\PowerShell\7\pwsh.exe" (
    "C:\Program Files\PowerShell\7\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
) else (
    echo PowerShell 7's pwsh.exe was not found on PATH or at the default install location.
    echo Install it from https://aka.ms/powershell-release?tag=stable and try again.
    echo.
    echo Or, if you'd rather not install anything extra, just open a terminal in this
    echo folder and run: pip install -r requirements.txt
    pause
)
