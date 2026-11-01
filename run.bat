@echo off
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0app.py"
) else (
    python "%~dp0app.py"
)
