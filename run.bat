@echo off
setlocal
cd /d "%~dp0"
title Gordian Knot

echo.
echo  Gordian Knot
echo  ------------
echo.

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo  First launch: creating a local Python environment...
  call :makevenv
  if errorlevel 1 exit /b 1
  echo  Environment ready.
  echo.
)

"%~dp0.venv\Scripts\python.exe" -c "import pystray, PIL" >nul 2>&1
if errorlevel 1 (
  echo  Installing packages from requirements.txt...
  "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo.
    echo  Could not install the requirements. Check your internet connection,
    echo  then double-click GordianKnot.bat again.
    echo.
    pause
    exit /b 1
  )
  echo  Packages installed.
  echo.
)

"%~dp0.venv\Scripts\python.exe" -c "import gordian.ui" >nul 2>&1
if errorlevel 1 (
  echo  Gordian Knot failed to start.
  echo.
  "%~dp0.venv\Scripts\python.exe" -c "import gordian.ui"
  echo.
  pause
  exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -c "from gordian.branding import ensure_branded_launcher; ensure_branded_launcher()" >nul 2>&1
set "PYTHONPATH=%~dp0;%~dp0.venv\Lib\site-packages"
if exist "%~dp0.venv\Scripts\GordianKnot.exe" (
  start "" "%~dp0.venv\Scripts\GordianKnot.exe" -m gordian
) else (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m gordian
)
exit /b 0

:makevenv
where python >nul 2>&1
if %errorlevel%==0 (
  python -m venv "%~dp0.venv"
) else (
  py -3 -m venv "%~dp0.venv"
)
if errorlevel 1 (
  echo.
  echo  Could not create a Python environment.
  echo  Install Python 3.12 from https://www.python.org/downloads/
  echo  and tick "Add python.exe to PATH" during setup.
  echo  Then double-click GordianKnot.bat again.
  echo.
  pause
  exit /b 1
)
exit /b 0
