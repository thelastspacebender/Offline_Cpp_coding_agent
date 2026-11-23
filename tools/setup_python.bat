@echo off
setlocal
cd /d "%~dp0.."

echo ======================================================================
echo [PYTHON] Checking and configuring Python environment
echo ======================================================================

set "PY_EXE="

rem 1. Check parent workspace python
if exist "%~dp0..\..\python\python.exe" (
    set "PY_EXE=%~dp0..\..\python\python.exe"
    goto :FoundPython
)

rem 2. Check system PATH python
where python.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PY_EXE=python.exe"
    goto :FoundPython
)

rem 3. Check py launcher
where py.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PY_EXE=py.exe -3"
    goto :FoundPython
)

rem 4. Portable embeddable Python fallback
set "PORTABLE_DIR=%~dp0..\runtime\python"
set "PORTABLE_EXE=%PORTABLE_DIR%\python.exe"
if exist "%PORTABLE_EXE%" (
    set "PY_EXE=%PORTABLE_EXE%"
    goto :FoundPython
)

echo [INFO] Python not found on system. Downloading portable Python 3.11 embeddable...
if not exist "%PORTABLE_DIR%" mkdir "%PORTABLE_DIR%"
set "PY_ZIP=%PORTABLE_DIR%\python-embed.zip"
curl.exe -L -C - "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" -o "%PY_ZIP%"
powershell -NoProfile -Command "Expand-Archive -Path '%PY_ZIP%' -DestinationPath '%PORTABLE_DIR%' -Force"
del /f /q "%PY_ZIP%" 2>nul

rem Enable site-packages in embeddable python
powershell -NoProfile -Command "(Get-Content '%PORTABLE_DIR%\python311._pth') -replace '#import site', 'import site' | Set-Content '%PORTABLE_DIR%\python311._pth'"
curl.exe -sSL "https://bootstrap.pypa.io/get-pip.py" -o "%PORTABLE_DIR%\get-pip.py"
"%PORTABLE_EXE%" "%PORTABLE_DIR%\get-pip.py"
set "PY_EXE=%PORTABLE_EXE%"

:FoundPython
echo [OK] Using Python: %PY_EXE%

echo [INSTALL] Installing Python dependencies from requirements.txt...
%PY_EXE% -m pip install -r "%~dp0..\requirements.txt"

if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Python dependencies installed successfully!
    exit /b 0
) else (
    echo [ERROR] Failed to install Python dependencies!
    exit /b 1
)
