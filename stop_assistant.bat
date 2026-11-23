@echo off
setlocal
cd /d "%~dp0"

echo [STOP] Stopping Qwen 7B and RAG servers...

taskkill /F /IM llama-server.exe >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| find ":18080" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo [OK] All assistant processes stopped.
ping 127.0.0.1 -n 2 >nul
