@echo off
setlocal
title MultiProxy Courier
cd /d "%~dp0"
rem Starts the MultiProxy Courier from this folder, with its own Python, without running setup again.
rem (Setup puts this file here and a Start menu shortcut to it. It also works after the new program files were copied over this folder by hand.)

set "PY=%~dp0.venv\Scripts\python.exe"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "FINDSTR=%SystemRoot%\System32\findstr.exe"
if not exist "%PY%" goto :notsetup
if not exist "%~dp0courier.py" goto :notsetup

rem Newer program files may need a Python package this folder's environment doesn't have yet: say so plainly, not with a Python error.
"%PY%" -c "import importlib.metadata as m,re;[m.version(re.split('[<>=;~ ]',l.strip())[0]) for l in open('requirements-courier.txt') if l.strip() and l[0]!='#']" >nul 2>nul
if errorlevel 1 goto :needsstep

"%PY%" courier.py status 2>nul | "%FINDSTR%" /C:"is running" >nul
if not errorlevel 1 goto :already

start "" "%PYW%" courier.py run
set /a tries=0
:wait
ping -n 2 127.0.0.1 >nul
"%PY%" courier.py status 2>nul | "%FINDSTR%" /C:"is running" >nul
if not errorlevel 1 goto :up
set /a tries+=1
if %tries% LSS 40 goto :wait
echo.
echo Courier didn't start. Double-click "Set up this PC" to fix it.
echo.
pause
exit /b 1

:up
echo Courier is running quietly by your clock.
ping -n 4 127.0.0.1 >nul
exit /b 0

:already
echo Courier is already running quietly by your clock.
ping -n 4 127.0.0.1 >nul
exit /b 0

:needsstep
echo.
echo This update needs one more step. Double-click "Set up this PC" in this folder.
echo.
pause
exit /b 1

:notsetup
echo.
echo Courier isn't set up here yet. Double-click "Set up this PC" first.
echo.
pause
exit /b 1
