@echo off
setlocal
cd /d "%~dp0.."

set "MODELS_DIR=%~dp0..\models"
if not exist "%MODELS_DIR%" mkdir "%MODELS_DIR%"
set "MODEL_FILE=%MODELS_DIR%\qwen2.5-coder-7b-instruct-q4_k_m.gguf"

if exist "%MODEL_FILE%" (
    echo [OK] Model file already exists: %MODEL_FILE%
    exit /b 0
)

echo ======================================================================
echo [DOWNLOAD] Downloading Qwen2.5-Coder-7B-Instruct (Q4_K_M GGUF)
echo Size: ~4.68 GB (Recommended for 16GB RAM / GPU offload)
echo ======================================================================

set "MODEL_URL=https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf"
set "FALLBACK_URL=https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"

echo Downloading model with resume support (-C -)...
curl.exe -L -C - --retry 5 "%MODEL_URL%" -o "%MODEL_FILE%"

if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Primary mirror failed, trying fallback Hugging Face mirror...
    curl.exe -L -C - --retry 5 "%FALLBACK_URL%" -o "%MODEL_FILE%"
)

if exist "%MODEL_FILE%" (
    echo [SUCCESS] Qwen2.5-Coder-7B GGUF model successfully downloaded!
    exit /b 0
) else (
    echo [ERROR] Model download failed!
    exit /b 1
)
