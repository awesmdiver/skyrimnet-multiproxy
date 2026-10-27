@echo off
rem Starts MultiProxy in the system tray (no terminal window). A second run just opens the dashboard.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -c "import pystray, PIL" >nul 2>nul
if errorlevel 1 (
    echo Setting up files for the tray icon, please wait...
    %PY% -m pip install -r requirements-tray.txt >nul 2>&1
)
for /f "delims=" %%i in ('%PY% -c "import sys; print(sys.executable.replace('python.exe','pythonw.exe'))"') do set PYW=%%i
start "" "%PYW%" "%~dp0multiproxy_tray.py"
