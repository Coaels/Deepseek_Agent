@echo off
setlocal
cd /d "%~dp0"

whoami /groups | findstr /C:"S-1-16-12288" >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "PYDIR="
for /f "delims=" %%I in ('where python 2^>nul') do (
    for %%J in ("%%I") do set "PYDIR=%%~dpJ"
    goto :gotpython
)
:gotpython

if not defined PYDIR (
    echo Python not found in PATH. Please install Python 3.9+ and add it to PATH.
    pause
    exit /b 1
)

if exist "%PYDIR%pythonw.exe" (
    start "" "%PYDIR%pythonw.exe" "%~dp0app.py" %*
) else (
    start "" "%PYDIR%python.exe" "%~dp0app.py" %*
)
