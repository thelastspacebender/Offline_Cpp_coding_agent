@echo off
cd /d "%~dp0"
title Offline C++ Agent - Local LLM Server

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
    echo [ERROR] Python not found! Run install.bat first.
    pause
    exit /b 1
)

"%PY_EXE%" backend\llama_launcher.py

echo.
echo [LLM SERVER STOPPED]
pause
