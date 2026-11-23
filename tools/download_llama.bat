@echo off
setlocal
cd /d "%~dp0.."

set "BIN_DIR=%~dp0..\bin"
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"
set "LLAMA_SERVER=%BIN_DIR%\llama-server.exe"

if exist "%LLAMA_SERVER%" (
    echo [OK] llama-server.exe already exists in bin\
    exit /b 0
)

echo ======================================================================
echo [DOWNLOAD] Downloading llama.cpp with Vulkan GPU Acceleration
echo ======================================================================

set "ZIP_FILE=%BIN_DIR%\llama-vulkan-win-x64.zip"
set "URL=https://github.com/ggml-org/llama.cpp/releases/download/b11750/llama-b11750-bin-win-vulkan-x64.zip"

echo Downloading %URL%...
curl.exe -L -C - --retry 5 "%URL%" -o "%ZIP_FILE%"

if not exist "%ZIP_FILE%" (
    echo [ERROR] Failed to download llama.cpp Vulkan release!
    exit /b 1
)

echo [EXTRACT] Extracting llama-server.exe to bin\...
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%BIN_DIR%' -Force"
del /f /q "%ZIP_FILE%" 2>nul

if exist "%LLAMA_SERVER%" (
    echo [SUCCESS] llama-server.exe (Vulkan GPU enabled) is ready!
    exit /b 0
) else (
    echo [ERROR] llama-server.exe was not found after extraction!
    exit /b 1
)
