param(
    [string]$DllPath
)

$ErrorActionPreference = "Stop"

if (-not $DllPath) {
    $DllPath = Join-Path $PSScriptRoot "..\bin\VS2005QwenRAG.dll"
}

$DllPath = [System.IO.Path]::GetFullPath($DllPath)
$srcManifest = Join-Path $PSScriptRoot "VS2005QwenRAG.AddIn"

if (-not (Test-Path $srcManifest)) {
    Write-Error "Source manifest not found: $srcManifest"
    exit 1
}

$rawXml = [System.IO.File]::ReadAllText($srcManifest, [System.Text.Encoding]::UTF8)
$xml = $rawXml.Replace("%BIN_DIR%\VS2005QwenRAG.dll", $DllPath)

$vsYears = @("Visual Studio 2005", "Visual Studio 2008", "Visual Studio 2010", "Visual Studio 2012", "Visual Studio 2013")
$vsVers = @("8.0", "9.0", "10.0", "11.0", "12.0")

$destDirs = @(
    (Join-Path $env:APPDATA "Microsoft\MSEnvShared\Addins")
)
foreach ($y in $vsYears) {
    $destDirs += (Join-Path $env:USERPROFILE "Documents\$y\Addins")
}
foreach ($v in $vsVers) {
    $destDirs += (Join-Path $env:APPDATA "Microsoft\MSEnv\$v\Addins")
}

$myDocs = [Environment]::GetFolderPath('MyDocuments')
if ($myDocs) {
    foreach ($y in $vsYears) {
        $destDirs += (Join-Path $myDocs "$y\Addins")
    }
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

foreach ($dir in ($destDirs | Select-Object -Unique)) {
    try {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        $targetFile = Join-Path $dir "VS2005QwenRAG.AddIn"
        [System.IO.File]::WriteAllText($targetFile, $xml, $utf8NoBom)
        Write-Host "[OK] Registered Add-in manifest: $targetFile"
    } catch {
        Write-Warning "[SKIP] Could not write to $dir : $($_.Exception.Message)"
    }
}
