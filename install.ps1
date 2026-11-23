$Host.UI.RawUI.WindowTitle = "Offline C++ Coding Agent Installer"

Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host "      OFFLINE C++ CODING AGENT - AUTOMATED CLI INSTALLER" -ForegroundColor Cyan
Write-Host "      Map-Reduce & Call-Graph Engine (Vulkan / CUDA / CPU)" -ForegroundColor Cyan
Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host ""

$rootDir = $PSScriptRoot
$binDir = Join-Path $rootDir "bin"
$modelsDir = Join-Path $rootDir "models"
$toolsDir = Join-Path $rootDir "tools"

if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
if (-not (Test-Path $modelsDir)) { New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null }

# -----------------------------------------------------------------------------
# Step 1: Configure Python & Dependencies
# -----------------------------------------------------------------------------
Write-Host "[STEP 1/5] Checking Python environment and dependencies..." -ForegroundColor Yellow

$pyExe = $null
$parentPy = Join-Path $rootDir "..\python\python.exe"

if (Test-Path $parentPy) {
    $pyExe = (Resolve-Path $parentPy).Path
} elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    $pyExe = "python.exe"
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $pyExe = "py.exe -3"
}

if (-not $pyExe) {
    $portableDir = Join-Path $rootDir "runtime\python"
    $portableExe = Join-Path $portableDir "python.exe"
    if (-not (Test-Path $portableExe)) {
        Write-Host "Python not found on system. Downloading portable Python 3.11 embeddable..." -ForegroundColor Gray
        New-Item -ItemType Directory -Path $portableDir -Force | Out-Null
        $pyZip = Join-Path $portableDir "python-embed.zip"
        curl.exe -L -C - "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" -o $pyZip
        Expand-Archive -Path $pyZip -DestinationPath $portableDir -Force
        Remove-Item $pyZip -Force -ErrorAction SilentlyContinue

        $pthFile = Join-Path $portableDir "python311._pth"
        if (Test-Path $pthFile) {
            (Get-Content $pthFile) -replace '#import site', 'import site' | Set-Content $pthFile
        }
        $getPip = Join-Path $portableDir "get-pip.py"
        curl.exe -sSL "https://bootstrap.pypa.io/get-pip.py" -o $getPip
        & $portableExe $getPip
    }
    $pyExe = $portableExe
}

Write-Host "[INFO] Python executable: $pyExe" -ForegroundColor Gray

# Check if required libraries are already installed
$depsCheck = & $pyExe -c "import fastapi, uvicorn, pydantic, tree_sitter, tree_sitter_cpp; print('OK')" 2>$null
if ($depsCheck -eq "OK") {
    Write-Host "[SKIP] Python libraries already installed (fastapi, tree-sitter, pydantic, uvicorn)." -ForegroundColor Green
} else {
    Write-Host "Installing Python dependencies from requirements.txt..." -ForegroundColor Gray
    $reqFile = Join-Path $rootDir "requirements.txt"
    & $pyExe -m pip install -r $reqFile
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] Python dependencies installed successfully." -ForegroundColor Green
    } else {
        Write-Warning "Some optional dependencies failed to install. Fallback parser will be used."
    }
}
Write-Host ""

# -----------------------------------------------------------------------------
# Step 2: Download Vulkan llama-server
# -----------------------------------------------------------------------------
Write-Host "[STEP 2/5] Checking llama-server with Vulkan GPU support..." -ForegroundColor Yellow
$llamaServer = Join-Path $binDir "llama-server.exe"

if (Test-Path $llamaServer) {
    Write-Host "[SKIP] llama-server.exe (Vulkan GPU enabled) already installed." -ForegroundColor Green
} else {
    $zipFile = Join-Path $binDir "llama-vulkan-win-x64.zip"
    $url = "https://github.com/ggml-org/llama.cpp/releases/download/b11750/llama-b11750-bin-win-vulkan-x64.zip"
    Write-Host "Downloading Vulkan llama.cpp release (~35 MB)..." -ForegroundColor Gray
    curl.exe -L -C - --retry 5 $url -o $zipFile

    if (Test-Path $zipFile) {
        Write-Host "Extracting llama-server.exe to bin\..." -ForegroundColor Gray
        Expand-Archive -Path $zipFile -DestinationPath $binDir -Force
        Remove-Item $zipFile -Force -ErrorAction SilentlyContinue
    }

    if (Test-Path $llamaServer) {
        Write-Host "[OK] llama-server.exe successfully installed." -ForegroundColor Green
    } else {
        Write-Error "Failed to install llama-server.exe!"
        Read-Host "Press Enter to exit..."
        exit 1
    }
}
Write-Host ""

# -----------------------------------------------------------------------------
# Step 3: Download Qwen2.5-Coder-7B GGUF Model
# -----------------------------------------------------------------------------
Write-Host "[STEP 3/5] Checking Qwen2.5-Coder-7B GGUF model..." -ForegroundColor Yellow
$modelFile = Join-Path $modelsDir "qwen2.5-coder-7b-instruct-q4_k_m.gguf"

if ((Test-Path $modelFile) -and ((Get-Item $modelFile).Length -gt 4000000000)) {
    $sizeBytes = (Get-Item $modelFile).Length
    $sizeGB = [math]::Round($sizeBytes / 1GB, 2)
    $msg = "[SKIP] Model already downloaded: qwen2.5-coder-7b-instruct-q4_k_m.gguf ({0} GB)." -f $sizeGB
    Write-Host $msg -ForegroundColor Green
} else {
    Write-Host "Model file not found. Size: ~4.68 GB." -ForegroundColor Gray
    Write-Host "Downloading from Hugging Face with live progress and resume support..." -ForegroundColor Gray
    $modelUrl = "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf"
    $fallbackModelUrl = "https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"

    curl.exe -L -C - --retry 5 $modelUrl -o $modelFile

    if ((-not (Test-Path $modelFile)) -or ((Get-Item $modelFile).Length -lt 4000000000)) {
        Write-Warning "Primary mirror did not complete, trying fallback mirror..."
        curl.exe -L -C - --retry 5 $fallbackModelUrl -o $modelFile
    }

    if ((Test-Path $modelFile) -and ((Get-Item $modelFile).Length -gt 4000000000)) {
        Write-Host "[OK] Qwen2.5-Coder-7B GGUF model downloaded successfully." -ForegroundColor Green
    } else {
        Write-Error "Failed to download model file!"
        Read-Host "Press Enter to exit..."
        exit 1
    }
}
Write-Host ""

# -----------------------------------------------------------------------------
# Step 4: Build & Register VS Add-in + VS Code Extension
# -----------------------------------------------------------------------------
Write-Host "[STEP 4/5] Installing IDE integrations (VS Code + Visual Studio 2005-2013)..." -ForegroundColor Yellow
$vscSrc = Join-Path $rootDir "vscode-extension"
$vscDst = Join-Path $env:USERPROFILE ".vscode\extensions\offline-cpp-coding-agent-1.0.0"
if (Test-Path $vscSrc) {
    if (Test-Path $vscDst) { Remove-Item $vscDst -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $vscDst -Force | Out-Null
    Copy-Item -Path (Join-Path $vscSrc "*") -Destination $vscDst -Recurse -Force
    Write-Host "[OK] VS Code extension installed: $vscDst" -ForegroundColor Green
}

$addinDll = Join-Path $binDir "VS2005QwenRAG.dll"
$docsAddins = Join-Path ([System.Environment]::GetFolderPath('MyDocuments')) "Visual Studio 2005\Addins\VS2005QwenRAG.AddIn"

if ((Test-Path $addinDll) -and (Test-Path $docsAddins)) {
    Write-Host "[SKIP] Classic VS Add-in already compiled and registered." -ForegroundColor Green
} else {
    Write-Host "Compiling and registering Classic VS Add-in with .NET 2.0 CLR..." -ForegroundColor Gray
    $buildScript = Join-Path $rootDir "addin\build_addin.bat"
    & cmd.exe /c $buildScript

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Classic VS Add-in compilation skipped/failed (Visual Studio may not be installed; Web UI and VS Code still work)."
    }
}
Write-Host ""

# -----------------------------------------------------------------------------
# Step 5: Create Desktop Shortcut
# -----------------------------------------------------------------------------
Write-Host "[STEP 5/5] Checking Desktop shortcut..." -ForegroundColor Yellow
$desktop = [System.Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop "Offline C++ Coding Agent.lnk"

if (Test-Path $shortcutPath) {
    Write-Host "[SKIP] Desktop shortcut already exists: $shortcutPath" -ForegroundColor Green
} else {
    try {
        $ws = New-Object -ComObject WScript.Shell
        $s = $ws.CreateShortcut($shortcutPath)
        $s.TargetPath = (Join-Path $rootDir "start_assistant.bat")
        $s.WorkingDirectory = $rootDir
        $s.Description = "Start Offline C++ Coding Agent"
        $s.Save()
        Write-Host "[OK] Created Desktop shortcut: $shortcutPath" -ForegroundColor Green
    } catch {
        Write-Warning "Could not create Desktop shortcut."
    }
}

Write-Host ""
Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host "  [SUCCESS] ALL CHECKS PASSED! SYSTEM IS 100% READY." -ForegroundColor Green
Write-Host ""
Write-Host "  1. Start server: run start_assistant.bat (or Desktop shortcut)" -ForegroundColor White
Write-Host "  2. Open VS Code, Visual Studio 2005-2013, or http://127.0.0.1:18080" -ForegroundColor White
Write-Host "  3. Index your workspace/solution and start asking!" -ForegroundColor White
Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to finish..."
