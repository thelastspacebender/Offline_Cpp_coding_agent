@echo off
setlocal
cd /d "%~dp0"

echo ======================================================================
echo    Starting Offline C++ Coding Agent (Local LLM + Code-Graph API)
echo ======================================================================
echo.

echo [0/2] Cleaning up previous instances (if any)...
taskkill /F /IM llama-server.exe >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| find ":18080" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
ping 127.0.0.1 -n 2 >nul

echo [1/2] Launching Qwen 7B Vulkan Engine console...
start "Qwen 7B Vulkan Engine" cmd.exe /c call "%~dp0run_llama.bat"

echo [2/2] Launching Qwen VS2005 RAG Backend console...
start "Qwen VS2005 RAG Backend" cmd.exe /c call "%~dp0run_backend.bat"

ping 127.0.0.1 -n 4 >nul

echo.
echo ======================================================================
echo [READY] Both services running in dedicated console windows with live logs:
echo   1. "Qwen 7B Vulkan Engine" displays GPU offload layers, memory, tokens/sec.
echo   2. "Qwen VS2005 RAG Backend" displays indexing and API calls from VS 2005.
echo ----------------------------------------------------------------------
echo - Open Visual Studio 2005 -^> Tools -^> 'Qwen AI Code Assistant'
echo - (Optional) Web UI & Monitor: http://127.0.0.1:18080
echo - To stop both servers, run stop_assistant.bat
echo ======================================================================
