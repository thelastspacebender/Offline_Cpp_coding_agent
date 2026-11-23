#requires -Version 5.1
<#
.SYNOPSIS
  Inno-Style Multi-Step WinForms Setup Wizard for Offline C++ Coding Agent.
  Run with -STA apartment state via install.bat.
#>

$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$RootDir = $PSScriptRoot
$BinDir = Join-Path $RootDir "bin"
$ModelsDir = Join-Path $RootDir "models"
$CatalogPath = Join-Path $RootDir "model_catalog.json"
$ConfigPath = Join-Path $RootDir "config.json"

if (-not (Test-Path $CatalogPath)) {
    [System.Windows.Forms.MessageBox]::Show("model_catalog.json not found in $RootDir", "Setup Error", "OK", "Error") | Out-Null
    exit 1
}

$Catalog = Get-Content -Path $CatalogPath -Raw -Encoding UTF8 | ConvertFrom-Json

# ---------------------------------------------------------------------------
# Hardware & IDE Detection (Pure PowerShell / Win32 / Registry)
# ---------------------------------------------------------------------------
function Get-HardwareSummary {
    $ramBytes = 16GB
    try {
        $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
        if ($cs.TotalPhysicalMemory) { $ramBytes = [double]$cs.TotalPhysicalMemory }
    } catch {
        try {
            $osMem = (Get-ItemProperty "HKLM:\HARDWARE\RESOURCEMAP\System Resources\Physical Memory" -ErrorAction SilentlyContinue)
        } catch {}
    }
    $ramGB = [math]::Round($ramBytes / 1GB, 1)

    $driveLetter = ([System.IO.Path]::GetPathRoot($RootDir)).TrimEnd('\')
    $freeGB = 50.0
    try {
        $psDrive = Get-PSDrive -Name ($driveLetter.TrimEnd(':')) -ErrorAction Stop
        $freeGB = [math]::Round($psDrive.Free / 1GB, 1)
    } catch {}

    $cpuName = $env:PROCESSOR_IDENTIFIER
    try {
        $cpuName = (Get-ItemProperty "HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0" -ErrorAction Stop).ProcessorNameString.Trim()
    } catch {}
    $cores = [Environment]::ProcessorCount

    $gpus = @()
    # Try nvidia-smi first
    $nvsmi = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
    if ($nvsmi) {
        try {
            $nvOut = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null
            foreach ($ln in $nvOut) {
                $parts = $ln -split ','
                if ($parts.Count -ge 2) {
                    $vram = [math]::Round(([double]$parts[1].Trim()) / 1024.0, 1)
                    $gpus += [PSCustomObject]@{ Name = $parts[0].Trim(); Vendor = "NVIDIA"; VramGB = $vram }
                }
            }
        } catch {}
    }

    if ($gpus.Count -eq 0) {
        try {
            $regPath = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            Get-ChildItem $regPath -ErrorAction SilentlyContinue | ForEach-Object {
                if ($_.PSChildName -match '^\d+$') {
                    $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
                    if ($props.DriverDesc -and $props.DriverDesc -notmatch 'Microsoft Basic|Remote|Virtual') {
                        $desc = [string]$props.DriverDesc
                        $vendor = if ($desc -match 'NVIDIA') { "NVIDIA" } elseif ($desc -match 'AMD|Radeon') { "AMD" } elseif ($desc -match 'Intel') { "Intel" } else { "GPU" }
                        $vb = $props.'HardwareInformation.qwMemorySize'
                        if (-not $vb) { $vb = $props.'HardwareInformation.MemorySize' }
                        $vgb = if ($vb -is [byte[]]) { [math]::Round(([BitConverter]::ToUInt32($vb, 0)) / 1GB, 1) } elseif ($vb) { [math]::Round([double]$vb / 1GB, 1) } else { 0.0 }
                        $gpus += [PSCustomObject]@{ Name = $desc; Vendor = $vendor; VramGB = $vgb }
                    }
                }
            }
        } catch {}
    }

    $bestVram = 0.0
    $hasNvidia4 = $false
    $hasAnyGpu = ($gpus.Count -gt 0)
    $hasIgpu = $false
    foreach ($g in $gpus) {
        if ($g.VramGB -gt $bestVram) { $bestVram = $g.VramGB }
        if ($g.Vendor -eq "NVIDIA" -and $g.VramGB -ge 4.0) { $hasNvidia4 = $true }
        if ($g.Vendor -in @("AMD", "Intel")) { $hasIgpu = $true }
    }

    $recBackend = if ($hasNvidia4) { "cuda" } elseif ($hasAnyGpu) { "vulkan" } else { "cpu" }
    $recCtx = if ($ramGB -ge 32) { 32768 } elseif ($ramGB -ge 16) { 16384 } else { 8192 }

    $hasVSCode = (Get-Command "code.cmd" -ErrorAction SilentlyContinue) -or (Test-Path (Join-Path $env:USERPROFILE ".vscode"))
    $prog86 = ${env:ProgramFiles(x86)}
    if (-not $prog86) { $prog86 = "C:\Program Files (x86)" }
    $classicVS = @()
    foreach ($pair in @(
        @("8.0", "VS 2005", "Microsoft Visual Studio 8"),
        @("9.0", "VS 2008", "Microsoft Visual Studio 9.0"),
        @("10.0", "VS 2010", "Microsoft Visual Studio 10.0"),
        @("11.0", "VS 2012", "Microsoft Visual Studio 11.0"),
        @("12.0", "VS 2013", "Microsoft Visual Studio 12.0")
    )) {
        $exe = Join-Path $prog86 "$($pair[2])\Common7\IDE\devenv.exe"
        if (Test-Path $exe) { $classicVS += $pair[1] }
    }

    return [PSCustomObject]@{
        CpuName = $cpuName
        Cores = $cores
        RamGB = $ramGB
        FreeDiskGB = $freeGB
        Gpus = $gpus
        BestVramGB = $bestVram
        HasIgpu = $hasIgpu
        RecBackend = $recBackend
        RecCtx = $recCtx
        HasVSCode = [bool]$hasVSCode
        ClassicVS = $classicVS
    }
}

$HW = Get-HardwareSummary

function Get-CompatInfo($model) {
    $minRam = [double]$model.min_ram_gb
    $recVram = [double]$model.rec_vram_gb
    $effMem = [math]::Max($HW.BestVramGB, $(if ($HW.HasIgpu) { $HW.RamGB * 0.55 } else { 0.0 }))
    if ($HW.RamGB -ge $minRam -and ($effMem -ge $recVram -or $HW.RamGB -ge ($minRam + 8))) {
        return @{ Tag = "[OK] Optimal"; Color = [System.Drawing.Color]::FromArgb(22, 163, 74) }
    } elseif ($HW.RamGB -ge [math]::Max(6.0, $minRam * 0.75)) {
        return @{ Tag = "[TIGHT] Fits with RAM"; Color = [System.Drawing.Color]::FromArgb(202, 138, 4) }
    } else {
        return @{ Tag = "[HEAVY] Needs ${minRam}GB+ RAM"; Color = [System.Drawing.Color]::FromArgb(220, 38, 38) }
    }
}

# ---------------------------------------------------------------------------
# Inno-Style Wizard Form Layout
# ---------------------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = "Setup - Offline C++ Coding Agent"
$form.Size = New-Object System.Drawing.Size(760, 560)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.BackColor = [System.Drawing.Color]::White
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)

# Top Header Banner
$pnlHeader = New-Object System.Windows.Forms.Panel
$pnlHeader.Dock = "Top"
$pnlHeader.Height = 68
$pnlHeader.BackColor = [System.Drawing.Color]::FromArgb(18, 24, 38)
$form.Controls.Add($pnlHeader)

$lblBannerTitle = New-Object System.Windows.Forms.Label
$lblBannerTitle.Location = New-Object System.Drawing.Point(20, 10)
$lblBannerTitle.Size = New-Object System.Drawing.Size(680, 24)
$lblBannerTitle.ForeColor = [System.Drawing.Color]::White
$lblBannerTitle.Font = New-Object System.Drawing.Font("Segoe UI", 12, [System.Drawing.FontStyle]::Bold)
$pnlHeader.Controls.Add($lblBannerTitle)

$lblBannerSub = New-Object System.Windows.Forms.Label
$lblBannerSub.Location = New-Object System.Drawing.Point(20, 36)
$lblBannerSub.Size = New-Object System.Drawing.Size(680, 22)
$lblBannerSub.ForeColor = [System.Drawing.Color]::FromArgb(180, 195, 220)
$lblBannerSub.Font = New-Object System.Drawing.Font("Segoe UI", 9.0)
$pnlHeader.Controls.Add($lblBannerSub)

# Bottom Navigation Bar
$pnlFooter = New-Object System.Windows.Forms.Panel
$pnlFooter.Dock = "Bottom"
$pnlFooter.Height = 56
$pnlFooter.BackColor = [System.Drawing.Color]::FromArgb(244, 246, 249)
$form.Controls.Add($pnlFooter)

$sepBottom = New-Object System.Windows.Forms.Panel
$sepBottom.Dock = "Top"
$sepBottom.Height = 1
$sepBottom.BackColor = [System.Drawing.Color]::FromArgb(215, 220, 228)
$pnlFooter.Controls.Add($sepBottom)

$btnBack = New-Object System.Windows.Forms.Button
$btnBack.Text = "< Back"
$btnBack.Location = New-Object System.Drawing.Point(445, 14)
$btnBack.Size = New-Object System.Drawing.Size(92, 29)
$pnlFooter.Controls.Add($btnBack)

$btnNext = New-Object System.Windows.Forms.Button
$btnNext.Text = "Next >"
$btnNext.Location = New-Object System.Drawing.Point(545, 14)
$btnNext.Size = New-Object System.Drawing.Size(96, 29)
$btnNext.BackColor = [System.Drawing.Color]::FromArgb(0, 122, 204)
$btnNext.ForeColor = [System.Drawing.Color]::White
$btnNext.FlatStyle = "Flat"
$pnlFooter.Controls.Add($btnNext)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "Cancel"
$btnCancel.Location = New-Object System.Drawing.Point(650, 14)
$btnCancel.Size = New-Object System.Drawing.Size(85, 29)
$pnlFooter.Controls.Add($btnCancel)

# Main Content Host
$pnlContent = New-Object System.Windows.Forms.Panel
$pnlContent.Dock = "Fill"
$pnlContent.Padding = New-Object System.Windows.Forms.Padding(20)
$form.Controls.Add($pnlContent)
$pnlContent.BringToFront()

# ----------------- PAGE 1: Hardware Inspection & Backend -----------------
$page1 = New-Object System.Windows.Forms.Panel
$page1.Dock = "Fill"

$lblWelcome = New-Object System.Windows.Forms.Label
$lblWelcome.Location = New-Object System.Drawing.Point(4, 4)
$lblWelcome.Size = New-Object System.Drawing.Size(690, 44)
$lblWelcome.Text = "Welcome to Offline C++ Coding Agent Setup Wizard.`nThis assistant installs 100% locally: Python runtime, llama.cpp inference engine, local GGUF models, and IDE plugins."
$page1.Controls.Add($lblWelcome)

$grpHw = New-Object System.Windows.Forms.GroupBox
$grpHw.Text = "Detected PC Hardware & Specifications"
$grpHw.Location = New-Object System.Drawing.Point(4, 56)
$grpHw.Size = New-Object System.Drawing.Size(695, 195)
$grpHw.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
$page1.Controls.Add($grpHw)

$gpuLines = if ($HW.Gpus.Count -gt 0) {
    ($HW.Gpus | ForEach-Object { "$($_.Name) ($($_.Vendor)$(if ($_.VramGB -gt 0) { ", $($_.VramGB) GB VRAM" }))" }) -join "; "
} else { "No dedicated GPU detected (CPU mode)" }
$vsFound = if ($HW.ClassicVS.Count -gt 0) { $HW.ClassicVS -join ", " } else { "Not detected (Web UI / VS Code still available)" }

$txtHwInfo = New-Object System.Windows.Forms.TextBox
$txtHwInfo.Multiline = $true
$txtHwInfo.ReadOnly = $true
$txtHwInfo.BorderStyle = "None"
$txtHwInfo.BackColor = [System.Drawing.Color]::FromArgb(248, 250, 252)
$txtHwInfo.Font = New-Object System.Drawing.Font("Consolas", 9.5)
$txtHwInfo.Location = New-Object System.Drawing.Point(14, 24)
$txtHwInfo.Size = New-Object System.Drawing.Size(668, 158)
$txtHwInfo.Text = (
    "CPU:          $($HW.CpuName) ($($HW.Cores) threads)`r`n" +
    "RAM:          $($HW.RamGB) GB`r`n" +
    "Free Disk:    $($HW.FreeDiskGB) GB`r`n" +
    "GPU:          $gpuLines`r`n" +
    "VS Code:      $(if ($HW.HasVSCode) { 'Detected' } else { 'Not detected' })`r`n" +
    "Classic VS:   $vsFound`r`n`r`n" +
    "Recommended:  Backend = $($HW.RecBackend.ToUpper()) | Context Window = $($HW.RecCtx) tokens"
)
$grpHw.Controls.Add($txtHwInfo)

$grpEngine = New-Object System.Windows.Forms.GroupBox
$grpEngine.Text = "Inference Acceleration & Context Size"
$grpEngine.Location = New-Object System.Drawing.Point(4, 260)
$grpEngine.Size = New-Object System.Drawing.Size(695, 110)
$page1.Controls.Add($grpEngine)

$lblBackend = New-Object System.Windows.Forms.Label
$lblBackend.Text = "llama.cpp Acceleration Backend:"
$lblBackend.Location = New-Object System.Drawing.Point(14, 28)
$lblBackend.Size = New-Object System.Drawing.Size(210, 22)
$grpEngine.Controls.Add($lblBackend)

$cmbBackend = New-Object System.Windows.Forms.ComboBox
$cmbBackend.DropDownStyle = "DropDownList"
$cmbBackend.Location = New-Object System.Drawing.Point(230, 25)
$cmbBackend.Size = New-Object System.Drawing.Size(320, 24)
$cmbBackend.Items.AddRange(@(
    "vulkan - Universal GPU (AMD Radeon / Intel Arc / NVIDIA)",
    "cuda - NVIDIA CUDA 12 GPU Acceleration (fastest for RTX)",
    "cpu - CPU Only (no GPU required)"
))
$cmbBackend.SelectedIndex = switch ($HW.RecBackend) { "cuda" { 1 } "cpu" { 2 } default { 0 } }
$grpEngine.Controls.Add($cmbBackend)

$lblCtx = New-Object System.Windows.Forms.Label
$lblCtx.Text = "Context Window (-c):"
$lblCtx.Location = New-Object System.Drawing.Point(14, 66)
$lblCtx.Size = New-Object System.Drawing.Size(210, 22)
$grpEngine.Controls.Add($lblCtx)

$cmbCtx = New-Object System.Windows.Forms.ComboBox
$cmbCtx.DropDownStyle = "DropDownList"
$cmbCtx.Location = New-Object System.Drawing.Point(230, 63)
$cmbCtx.Size = New-Object System.Drawing.Size(320, 24)
$cmbCtx.Items.AddRange(@("8192", "16384", "32768"))
$cmbCtx.SelectedItem = [string]$HW.RecCtx
$grpEngine.Controls.Add($cmbCtx)

# ----------------- PAGE 2: Model Selection & Custom GGUF -----------------
$page2 = New-Object System.Windows.Forms.Panel
$page2.Dock = "Fill"

$lblModelHint = New-Object System.Windows.Forms.Label
$lblModelHint.Location = New-Object System.Drawing.Point(4, 2)
$lblModelHint.Size = New-Object System.Drawing.Size(695, 22)
$lblModelHint.Text = "Select one or more models to download, or connect your own local .gguf file below:"
$page2.Controls.Add($lblModelHint)

$pnlModelList = New-Object System.Windows.Forms.Panel
$pnlModelList.Location = New-Object System.Drawing.Point(4, 28)
$pnlModelList.Size = New-Object System.Drawing.Size(695, 275)
$pnlModelList.AutoScroll = $true
$pnlModelList.BorderStyle = "FixedSingle"
$pnlModelList.BackColor = [System.Drawing.Color]::FromArgb(250, 251, 253)
$page2.Controls.Add($pnlModelList)

$script:ModelCheckboxes = @()
$yOff = 8
foreach ($m in $Catalog.models) {
    $compat = Get-CompatInfo $m
    $localFile = Join-Path $ModelsDir $m.filename
    $alreadyThere = (Test-Path $localFile) -and ((Get-Item $localFile).Length -gt 100MB)

    $card = New-Object System.Windows.Forms.Panel
    $card.Location = New-Object System.Drawing.Point(8, $yOff)
    $card.Size = New-Object System.Drawing.Size(656, 60)
    $card.BackColor = [System.Drawing.Color]::White
    $card.BorderStyle = "FixedSingle"

    $chk = New-Object System.Windows.Forms.CheckBox
    $chk.Location = New-Object System.Drawing.Point(10, 6)
    $chk.Size = New-Object System.Drawing.Size(410, 22)
    $chk.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
    $chk.Text = "$($m.name)  ($($m.size_gb) GB)"
    $chk.Checked = ($m.default_selected -or $alreadyThere)
    $chk.Tag = $m
    $card.Controls.Add($chk)
    $script:ModelCheckboxes += $chk

    $lblBadge = New-Object System.Windows.Forms.Label
    $lblBadge.Location = New-Object System.Drawing.Point(430, 7)
    $lblBadge.Size = New-Object System.Drawing.Size(218, 20)
    $lblBadge.TextAlign = "TopRight"
    $lblBadge.Font = New-Object System.Drawing.Font("Segoe UI", 8.5, [System.Drawing.FontStyle]::Bold)
    $lblBadge.ForeColor = $compat.Color
    $lblBadge.Text = if ($alreadyThere) { "[INSTALLED] $($compat.Tag)" } else { $compat.Tag }
    $card.Controls.Add($lblBadge)

    $lblDesc = New-Object System.Windows.Forms.Label
    $lblDesc.Location = New-Object System.Drawing.Point(28, 30)
    $lblDesc.Size = New-Object System.Drawing.Size(618, 24)
    $lblDesc.Font = New-Object System.Drawing.Font("Segoe UI", 8.5)
    $lblDesc.ForeColor = [System.Drawing.Color]::FromArgb(75, 85, 99)
    $lblDesc.Text = "Req: $($m.min_ram_gb)GB+ RAM / $($m.rec_vram_gb)GB VRAM | $($m.use_case_en)"
    $card.Controls.Add($lblDesc)

    $pnlModelList.Controls.Add($card)
    $yOff += 66
}

$grpCustom = New-Object System.Windows.Forms.GroupBox
$grpCustom.Text = "Optional: Use an Existing Local .gguf File from Disk"
$grpCustom.Location = New-Object System.Drawing.Point(4, 310)
$grpCustom.Size = New-Object System.Drawing.Size(695, 62)
$page2.Controls.Add($grpCustom)

$txtCustomGguf = New-Object System.Windows.Forms.TextBox
$txtCustomGguf.Location = New-Object System.Drawing.Point(12, 24)
$txtCustomGguf.Size = New-Object System.Drawing.Size(555, 24)
$txtCustomGguf.Font = New-Object System.Drawing.Font("Consolas", 9.0)
$grpCustom.Controls.Add($txtCustomGguf)

$btnBrowseGguf = New-Object System.Windows.Forms.Button
$btnBrowseGguf.Text = "Browse .gguf..."
$btnBrowseGguf.Location = New-Object System.Drawing.Point(575, 22)
$btnBrowseGguf.Size = New-Object System.Drawing.Size(108, 26)
$btnBrowseGguf.Add_Click({
    $ofd = New-Object System.Windows.Forms.OpenFileDialog
    $ofd.Title = "Select Local GGUF Model File"
    $ofd.Filter = "GGUF Models (*.gguf)|*.gguf|All Files (*.*)|*.*"
    if ($ofd.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $txtCustomGguf.Text = $ofd.FileName
    }
})
$grpCustom.Controls.Add($btnBrowseGguf)

# ----------------- PAGE 3: IDE Integrations -----------------
$page3 = New-Object System.Windows.Forms.Panel
$page3.Dock = "Fill"

$grpIdes = New-Object System.Windows.Forms.GroupBox
$grpIdes.Text = "Select IDE Integrations & Shortcuts"
$grpIdes.Location = New-Object System.Drawing.Point(4, 8)
$grpIdes.Size = New-Object System.Drawing.Size(695, 290)
$page3.Controls.Add($grpIdes)

$chkVSCode = New-Object System.Windows.Forms.CheckBox
$chkVSCode.Text = "Install Visual Studio Code / Cursor / VSCodium Extension (Recommended)"
$chkVSCode.Location = New-Object System.Drawing.Point(18, 34)
$chkVSCode.Size = New-Object System.Drawing.Size(650, 24)
$chkVSCode.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
$chkVSCode.Checked = $true
$grpIdes.Controls.Add($chkVSCode)

$lblVscSub = New-Object System.Windows.Forms.Label
$lblVscSub.Text = "Copies extension to %USERPROFILE%\.vscode\extensions for instant chat panel, Reducing AI, and clickable [File:Line] navigation."
$lblVscSub.Location = New-Object System.Drawing.Point(38, 58)
$lblVscSub.Size = New-Object System.Drawing.Size(635, 32)
$lblVscSub.ForeColor = [System.Drawing.Color]::DimGray
$grpIdes.Controls.Add($lblVscSub)

$chkClassicVS = New-Object System.Windows.Forms.CheckBox
$chkClassicVS.Text = "Compile & Register Classic Visual Studio Add-in (VS 2005 / 2008 / 2010 / 2012 / 2013)"
$chkClassicVS.Location = New-Object System.Drawing.Point(18, 102)
$chkClassicVS.Size = New-Object System.Drawing.Size(650, 24)
$chkClassicVS.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
$chkClassicVS.Checked = ($HW.ClassicVS.Count -gt 0)
$grpIdes.Controls.Add($chkClassicVS)

$lblVsSub = New-Object System.Windows.Forms.Label
$lblVsSub.Text = "Compiles x86 .NET 2.0 Add-in DLL via csc.exe, registers COM types with RegAsm, and writes .AddIn manifests for VS 8.0-12.0."
$lblVsSub.Location = New-Object System.Drawing.Point(38, 126)
$lblVsSub.Size = New-Object System.Drawing.Size(635, 32)
$lblVsSub.ForeColor = [System.Drawing.Color]::DimGray
$grpIdes.Controls.Add($lblVsSub)

$chkShortcut = New-Object System.Windows.Forms.CheckBox
$chkShortcut.Text = "Create Desktop Shortcut ('Offline C++ Coding Agent')"
$chkShortcut.Location = New-Object System.Drawing.Point(18, 174)
$chkShortcut.Size = New-Object System.Drawing.Size(650, 24)
$chkShortcut.Checked = $true
$grpIdes.Controls.Add($chkShortcut)

$lblWebNote = New-Object System.Windows.Forms.Label
$lblWebNote.Text = "Note: For Visual Studio 2015 / 2017 / 2019 / 2022, CLion, or any editor, the built-in Web UI (http://127.0.0.1:18080) is always included and lets you index any folder on disk."
$lblWebNote.Location = New-Object System.Drawing.Point(18, 222)
$lblWebNote.Size = New-Object System.Drawing.Size(655, 48)
$lblWebNote.ForeColor = [System.Drawing.Color]::FromArgb(30, 64, 175)
$grpIdes.Controls.Add($lblWebNote)

# ----------------- PAGE 4: Installation Progress -----------------
$page4 = New-Object System.Windows.Forms.Panel
$page4.Dock = "Fill"

$lblStepStatus = New-Object System.Windows.Forms.Label
$lblStepStatus.Location = New-Object System.Drawing.Point(4, 6)
$lblStepStatus.Size = New-Object System.Drawing.Size(695, 24)
$lblStepStatus.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
$lblStepStatus.Text = "Preparing installation..."
$page4.Controls.Add($lblStepStatus)

$prgInstall = New-Object System.Windows.Forms.ProgressBar
$prgInstall.Location = New-Object System.Drawing.Point(4, 34)
$prgInstall.Size = New-Object System.Drawing.Size(695, 22)
$page4.Controls.Add($prgInstall)

$txtLog = New-Object System.Windows.Forms.TextBox
$txtLog.Multiline = $true
$txtLog.ReadOnly = $true
$txtLog.ScrollBars = "Vertical"
$txtLog.Location = New-Object System.Drawing.Point(4, 68)
$txtLog.Size = New-Object System.Drawing.Size(695, 304)
$txtLog.BackColor = [System.Drawing.Color]::FromArgb(15, 23, 42)
$txtLog.ForeColor = [System.Drawing.Color]::FromArgb(226, 232, 240)
$txtLog.Font = New-Object System.Drawing.Font("Consolas", 9.0)
$page4.Controls.Add($txtLog)

# ----------------- PAGE 5: Finish -----------------
$page5 = New-Object System.Windows.Forms.Panel
$page5.Dock = "Fill"

$lblFinishTitle = New-Object System.Windows.Forms.Label
$lblFinishTitle.Location = New-Object System.Drawing.Point(4, 12)
$lblFinishTitle.Size = New-Object System.Drawing.Size(690, 32)
$lblFinishTitle.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$lblFinishTitle.ForeColor = [System.Drawing.Color]::FromArgb(22, 163, 74)
$lblFinishTitle.Text = "Setup Completed Successfully!"
$page5.Controls.Add($lblFinishTitle)

$lblFinishBody = New-Object System.Windows.Forms.Label
$lblFinishBody.Location = New-Object System.Drawing.Point(4, 56)
$lblFinishBody.Size = New-Object System.Drawing.Size(690, 140)
$lblFinishBody.Text = (
    "Offline C++ Coding Agent is installed and configured.`r`n`r`n" +
    "- Web UI & Model Manager:  http://127.0.0.1:18080`r`n" +
    "- VS Code Extension:       Open VS Code -> Sidebar -> Offline C++ Agent`r`n" +
    "- Classic VS 2005-2013:    Tools -> Offline C++ Coding Agent`r`n" +
    "- Stop servers anytime:    run stop_assistant.bat"
)
$page5.Controls.Add($lblFinishBody)

$chkLaunchNow = New-Object System.Windows.Forms.CheckBox
$chkLaunchNow.Text = "Launch Offline C++ Coding Agent now (start_assistant.bat)"
$chkLaunchNow.Location = New-Object System.Drawing.Point(4, 210)
$chkLaunchNow.Size = New-Object System.Drawing.Size(500, 26)
$chkLaunchNow.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$chkLaunchNow.Checked = $true
$page5.Controls.Add($chkLaunchNow)

# ---------------------------------------------------------------------------
# Helpers for Non-Blocking Process Execution & Logging
# ---------------------------------------------------------------------------
$script:CancelRequested = $false

function Append-Log([string]$msg) {
    $txtLog.AppendText("$(Get-Date -Format 'HH:mm:ss') | $msg`r`n")
    $txtLog.SelectionStart = $txtLog.TextLength
    $txtLog.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

function Invoke-ProcessWithEvents([string]$exe, [string]$arguments, [string]$watchFile = "", [double]$expectedMB = 0) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $arguments
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $null = $proc.Start()

    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    while (-not $proc.HasExited) {
        if ($script:CancelRequested) {
            try { $proc.Kill() } catch {}
            return 1
        }
        if ($watchFile -and $expectedMB -gt 0 -and (Test-Path $watchFile)) {
            $mb = [math]::Round((Get-Item $watchFile).Length / 1MB, 1)
            $pct = [math]::Min(99, [int](($mb / $expectedMB) * 100))
            $lblStepStatus.Text = "Downloading $([System.IO.Path]::GetFileName($watchFile)): $mb MB / $([math]::Round($expectedMB, 0)) MB ($pct%)"
        }
        Start-Sleep -Milliseconds 180
        [System.Windows.Forms.Application]::DoEvents()
    }
    $proc.WaitForExit()
    return $proc.ExitCode
}

function Run-Installation {
    if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir -Force | Out-Null }
    if (-not (Test-Path $ModelsDir)) { New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null }

    $backendChoice = switch ($cmbBackend.SelectedIndex) { 1 { "cuda" } 2 { "cpu" } default { "vulkan" } }
    $ctxChoice = [int]$cmbCtx.SelectedItem
    $selectedModels = @()
    foreach ($cb in $script:ModelCheckboxes) {
        if ($cb.Checked) { $selectedModels += $cb.Tag }
    }
    $customGguf = $txtCustomGguf.Text.Trim().Trim('"')

    # Step 1: Python & Dependencies
    $prgInstall.Value = 10
    $lblStepStatus.Text = "[1/5] Checking Python environment & dependencies..."
    Append-Log "[1/5] Checking Python..."
    $pyExe = $null
    $parentPy = Join-Path $RootDir "..\python\python.exe"
    $portableDir = Join-Path $RootDir "runtime\python"
    $portableExe = Join-Path $portableDir "python.exe"

    if (Test-Path $parentPy) { $pyExe = (Resolve-Path $parentPy).Path }
    elseif (Test-Path $portableExe) { $pyExe = $portableExe }
    elseif (Get-Command "python.exe" -ErrorAction SilentlyContinue) { $pyExe = "python.exe" }

    if (-not $pyExe) {
        Append-Log "Downloading portable Python 3.11.9 embeddable..."
        New-Item -ItemType Directory -Path $portableDir -Force | Out-Null
        $pyZip = Join-Path $portableDir "python-embed.zip"
        Invoke-ProcessWithEvents "curl.exe" "-L -C - `"$($Catalog.python_embed_url)`" -o `"$pyZip`"" $pyZip 11 | Out-Null
        Expand-Archive -Path $pyZip -DestinationPath $portableDir -Force
        Remove-Item $pyZip -Force -ErrorAction SilentlyContinue
        $pth = Join-Path $portableDir "python311._pth"
        if (Test-Path $pth) { (Get-Content $pth) -replace '#import site', 'import site' | Set-Content $pth }
        $getPip = Join-Path $portableDir "get-pip.py"
        Invoke-ProcessWithEvents "curl.exe" "-sSL `"$($Catalog.get_pip_url)`" -o `"$getPip`"" | Out-Null
        Invoke-ProcessWithEvents $portableExe "`"$getPip`"" | Out-Null
        $pyExe = $portableExe
    }
    Append-Log "Using Python: $pyExe"

    $depCheck = & $pyExe -c "import fastapi, uvicorn, pydantic, tree_sitter, tree_sitter_cpp; print('OK')" 2>$null
    if ($depCheck -ne "OK") {
        Append-Log "Installing Python packages from requirements.txt..."
        $reqFile = Join-Path $RootDir "requirements.txt"
        Invoke-ProcessWithEvents $pyExe "-m pip install -r `"$reqFile`"" | Out-Null
    } else {
        Append-Log "[SKIP] Python packages already installed."
    }

    # Step 2: llama-server (Vulkan / CUDA / CPU)
    $prgInstall.Value = 30
    $lblStepStatus.Text = "[2/5] Installing llama.cpp ($($backendChoice.ToUpper()))..."
    $llamaExe = Join-Path $BinDir "llama-server.exe"
    $needDownloadLlama = (-not (Test-Path $llamaExe)) -or ($backendChoice -eq "cuda" -and -not (Test-Path (Join-Path $BinDir "ggml-cuda.dll")))
    if ($needDownloadLlama) {
        Append-Log "[2/5] Querying GitHub Releases API for llama.cpp ($backendChoice)..."
        $assetUrl = $Catalog.llama_cpp.fallback_urls.$backendChoice
        $cudartUrl = $Catalog.llama_cpp.fallback_urls.cudart
        try {
            $relResp = Invoke-RestMethod -Uri $Catalog.llama_cpp.releases_api -Headers @{ "User-Agent" = "OfflineCppAgentSetup" } -TimeoutSec 12
            foreach ($rel in $relResp) {
                $pattern = switch ($backendChoice) {
                    "cuda" { "bin-win-cuda-12.*-x64\.zip$" }
                    "cpu"  { "bin-win-cpu-x64\.zip$" }
                    default { "bin-win-vulkan-x64\.zip$" }
                }
                $matchAsset = $rel.assets | Where-Object { $_.name -match $pattern -and $_.name -like "llama-*" } | Select-Object -First 1
                if ($matchAsset) {
                    $assetUrl = $matchAsset.browser_download_url
                    if ($backendChoice -eq "cuda") {
                        $cudartAsset = $rel.assets | Where-Object { $_.name -like "cudart-llama-bin-win-cuda-12*-x64.zip" } | Select-Object -First 1
                        if ($cudartAsset) { $cudartUrl = $cudartAsset.browser_download_url }
                    }
                    Append-Log "Resolved llama.cpp release $($rel.tag_name): $($matchAsset.name)"
                    break
                }
            }
        } catch {
            Append-Log "GitHub API unavailable, using pinned fallback release $($Catalog.llama_cpp.fallback_tag)."
        }

        $zipPath = Join-Path $BinDir "llama-release.zip"
        Append-Log "Downloading llama-server package..."
        Invoke-ProcessWithEvents "curl.exe" "-L -C - --retry 5 `"$assetUrl`" -o `"$zipPath`"" $zipPath 40 | Out-Null
        if (Test-Path $zipPath) {
            Expand-Archive -Path $zipPath -DestinationPath $BinDir -Force
            Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        }
        if ($backendChoice -eq "cuda" -and $cudartUrl) {
            $cudartZip = Join-Path $BinDir "cudart.zip"
            Append-Log "Downloading CUDA runtime libraries..."
            Invoke-ProcessWithEvents "curl.exe" "-L -C - --retry 5 `"$cudartUrl`" -o `"$cudartZip`"" $cudartZip 370 | Out-Null
            if (Test-Path $cudartZip) {
                Expand-Archive -Path $cudartZip -DestinationPath $BinDir -Force
                Remove-Item $cudartZip -Force -ErrorAction SilentlyContinue
            }
        }
    } else {
        Append-Log "[SKIP] llama-server.exe already present."
    }

    # Step 3: Download Selected Models
    $prgInstall.Value = 50
    $activeModelPath = ""
    $totalSel = [math]::Max(1, $selectedModels.Count)
    $mIdx = 0
    foreach ($m in $selectedModels) {
        $mIdx++
        $targetFile = Join-Path $ModelsDir $m.filename
        $expectedMB = ([double]$m.size_gb) * 1024.0
        $lblStepStatus.Text = "[3/5] Model ($mIdx/$totalSel): $($m.name)..."
        if ((Test-Path $targetFile) -and ((Get-Item $targetFile).Length -gt 100MB)) {
            Append-Log "[SKIP] Model already downloaded: $($m.filename)"
        } else {
            Append-Log "Downloading $($m.name) (~$($m.size_gb) GB) with resume support..."
            $rc = Invoke-ProcessWithEvents "curl.exe" "-L -C - --retry 5 `"$($m.url)`" -o `"$targetFile`"" $targetFile $expectedMB
            if (($rc -ne 0 -or -not (Test-Path $targetFile) -or (Get-Item $targetFile).Length -lt 100MB) -and $m.fallback_url) {
                Append-Log "Primary mirror incomplete, trying fallback mirror..."
                Invoke-ProcessWithEvents "curl.exe" "-L -C - --retry 5 `"$($m.fallback_url)`" -o `"$targetFile`"" $targetFile $expectedMB | Out-Null
            }
        }
        if (-not $activeModelPath -and (Test-Path $targetFile)) {
            $activeModelPath = "models/$($m.filename)"
        }
        $prgInstall.Value = 50 + [int](($mIdx / $totalSel) * 30)
    }

    $customList = @()
    if ($customGguf -and (Test-Path $customGguf)) {
        $customList += $customGguf
        $activeModelPath = $customGguf
        Append-Log "Configured custom local model: $customGguf"
    }
    if (-not $activeModelPath) {
        $activeModelPath = "models/qwen2.5-coder-7b-instruct-q4_k_m.gguf"
    }

    # Write config.json
    $nglVal = if ($backendChoice -eq "cpu") { 0 } else { 99 }
    $cfgObj = @{
        server = @{ host = "127.0.0.1"; port = 18080 }
        llama = @{
            host = "127.0.0.1"
            port = 8080
            backend = $backendChoice
            model_path = $activeModelPath
            context_size = $ctxChoice
            gpu_layers = $nglVal
            threads = $HW.Cores
            custom_models = $customList
        }
        indexer = @{
            max_file_size_mb = 10
            encodings = @("utf-8", "cp1251", "cp866", "latin1")
            chunk_size_lines = 50
        }
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($ConfigPath, ($cfgObj | ConvertTo-Json -Depth 5), $utf8NoBom)
    Append-Log "Saved config.json (backend=$backendChoice, ctx=$ctxChoice, model=$activeModelPath)"

    # Step 4: IDE Extensions
    $prgInstall.Value = 85
    $lblStepStatus.Text = "[4/5] Installing IDE extensions..."

    if ($chkVSCode.Checked) {
        $vscSrc = Join-Path $RootDir "vscode-extension"
        $vscDst = Join-Path $env:USERPROFILE ".vscode\extensions\offline-cpp-coding-agent-1.0.0"
        if (Test-Path $vscSrc) {
            if (Test-Path $vscDst) { Remove-Item $vscDst -Recurse -Force -ErrorAction SilentlyContinue }
            New-Item -ItemType Directory -Path $vscDst -Force | Out-Null
            Copy-Item -Path (Join-Path $vscSrc "*") -Destination $vscDst -Recurse -Force
            Append-Log "[OK] Installed VS Code extension to: $vscDst"
        }
    }

    if ($chkClassicVS.Checked) {
        $buildBat = Join-Path $RootDir "addin\build_addin.bat"
        if (Test-Path $buildBat) {
            Append-Log "Building and registering Visual Studio Add-in..."
            $rcAddin = Invoke-ProcessWithEvents "cmd.exe" "/c `"$buildBat`""
            if ($rcAddin -eq 0) {
                Append-Log "[OK] Classic Visual Studio Add-in built and registered."
            } else {
                Append-Log "[WARN] Add-in build returned code $rcAddin (close any open Visual Studio window and re-run addin\build_addin.bat)."
            }
        }
    }

    # Step 5: Desktop Shortcut
    $prgInstall.Value = 95
    $lblStepStatus.Text = "[5/5] Finalizing..."
    if ($chkShortcut.Checked) {
        try {
            $desktop = [System.Environment]::GetFolderPath('Desktop')
            $lnk = Join-Path $desktop "Offline C++ Coding Agent.lnk"
            $ws = New-Object -ComObject WScript.Shell
            $s = $ws.CreateShortcut($lnk)
            $s.TargetPath = (Join-Path $RootDir "start_assistant.bat")
            $s.WorkingDirectory = $RootDir
            $s.Description = "Start Offline C++ Coding Agent"
            $s.Save()
            Append-Log "[OK] Created Desktop shortcut: $lnk"
        } catch {
            Append-Log "[WARN] Could not create Desktop shortcut."
        }
    }

    $prgInstall.Value = 100
    $lblStepStatus.Text = "Installation complete!"
    Append-Log "[SUCCESS] Setup completed!"
}

# ---------------------------------------------------------------------------
# Navigation Controller
# ---------------------------------------------------------------------------
$script:CurrentPage = 1
$pages = @($page1, $page2, $page3, $page4, $page5)

function Show-Page([int]$num) {
    $script:CurrentPage = $num
    $pnlContent.Controls.Clear()
    $pnlContent.Controls.Add($pages[$num - 1])

    switch ($num) {
        1 {
            $lblBannerTitle.Text = "Offline C++ Coding Agent Setup — Hardware & Engine"
            $lblBannerSub.Text = "Inspects your CPU, RAM, GPU, and IDEs to recommend the best configuration."
            $btnBack.Enabled = $false
            $btnNext.Text = "Next >"
            $btnNext.Enabled = $true
        }
        2 {
            $lblBannerTitle.Text = "Select Local GGUF Models"
            $lblBannerSub.Text = "Choose one or more models from the verified catalog or point to an existing .gguf file."
            $btnBack.Enabled = $true
            $btnNext.Text = "Next >"
            $btnNext.Enabled = $true
        }
        3 {
            $lblBannerTitle.Text = "IDE Integrations & Shortcuts"
            $lblBannerSub.Text = "Supports Visual Studio Code, Visual Studio 2005-2013 Add-in, and Web UI (VS 2015-2022 / Any)."
            $btnBack.Enabled = $true
            $btnNext.Text = "Install"
            $btnNext.Enabled = $true
        }
        4 {
            $lblBannerTitle.Text = "Installing Components..."
            $lblBannerSub.Text = "Please wait while Offline C++ Coding Agent configures your local environment."
            $btnBack.Enabled = $false
            $btnNext.Enabled = $false
            $btnNext.Text = "Installing..."
            [System.Windows.Forms.Application]::DoEvents()
            Run-Installation
            Show-Page 5
        }
        5 {
            $lblBannerTitle.Text = "Setup Complete"
            $lblBannerSub.Text = "Offline C++ Coding Agent is ready for use."
            $btnBack.Visible = $false
            $btnCancel.Visible = $false
            $btnNext.Enabled = $true
            $btnNext.Text = "Finish"
        }
    }
}

$btnBack.Add_Click({
    if ($script:CurrentPage -gt 1 -and $script:CurrentPage -lt 4) {
        Show-Page ($script:CurrentPage - 1)
    }
})

$btnNext.Add_Click({
    if ($script:CurrentPage -lt 3) {
        Show-Page ($script:CurrentPage + 1)
    } elseif ($script:CurrentPage -eq 3) {
        Show-Page 4
    } elseif ($script:CurrentPage -eq 5) {
        if ($chkLaunchNow.Checked) {
            Start-Process -FilePath (Join-Path $RootDir "start_assistant.bat") -WorkingDirectory $RootDir
        }
        $form.Close()
    }
})

$btnCancel.Add_Click({
    $script:CancelRequested = $true
    $form.Close()
})

Show-Page 1
[void]$form.ShowDialog()
