@echo off
setlocal
cd /d "%~dp0"

if /I "%~1"=="--cli" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
) else (
    powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer_gui.ps1"
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Installation exited with an error code: %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)
exit /b 0
