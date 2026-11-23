@echo off
cd /d "%~dp0"
title Qwen VS2005 RAG Backend (API Logs)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set "PY_EXE="
if exist "%~dp0..\python\python.exe" set "PY_EXE=%~dp0..\python\python.exe"
if not defined PY_EXE (
    if exist "%~dp0runtime\python\python.exe" set "PY_EXE=%~dp0runtime\python\python.exe"
)
if not defined PY_EXE (
    for /f "delims=" %%i in ('where python.exe 2^>nul') do (
        if not defined PY_EXE set "PY_EXE=%%i"
    )
)

if not defined PY_EXE (
    echo [ERROR] Python not found! Please run install.bat.
    pause
    exit /b 1
)

rem Free port 18080 if lingering from previous run
for /f "tokens=5" %%a in ('netstat -aon ^| find ":18080" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo ======================================================================
echo    Qwen VS2005 Code Graph and RAG API (Live Request Logs)
echo    Port: http://127.0.0.1:18080
echo ======================================================================
echo.

"%PY_EXE%" backend\main.py

echo.
echo [SERVER STOPPED]
pause
