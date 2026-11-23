@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ======================================================================
echo [BUILD] Building Offline C++ Coding Agent Add-in DLL (x86 CLR)
echo ======================================================================

set "FRAMEWORK_DIR=C:\Windows\Microsoft.NET\Framework\v2.0.50727"
if not exist "%FRAMEWORK_DIR%\csc.exe" set "FRAMEWORK_DIR=C:\Windows\Microsoft.NET\Framework\v3.5"
if not exist "%FRAMEWORK_DIR%\csc.exe" set "FRAMEWORK_DIR=C:\Windows\Microsoft.NET\Framework\v4.0.30319"

if not exist "%FRAMEWORK_DIR%\csc.exe" (
    echo [ERROR] Cannot find 32-bit csc.exe in C:\Windows\Microsoft.NET\Framework!
    exit /b 1
)

set "CSC=%FRAMEWORK_DIR%\csc.exe"
set "REGASM=%FRAMEWORK_DIR%\RegAsm.exe"

set "EXT_DLL=C:\Program Files (x86)\Common Files\Microsoft Shared\MSEnv\PublicAssemblies\extensibility.dll"
if not exist "%EXT_DLL%" set "EXT_DLL=C:\Program Files\Common Files\Microsoft Shared\MSEnv\PublicAssemblies\extensibility.dll"

set "VS_PUB="
for %%V in ("Microsoft Visual Studio 8" "Microsoft Visual Studio 9.0" "Microsoft Visual Studio 10.0" "Microsoft Visual Studio 11.0" "Microsoft Visual Studio 12.0") do (
    if not defined VS_PUB (
        if exist "C:\Program Files (x86)\%%~V\Common7\IDE\PublicAssemblies\EnvDTE.dll" (
            set "VS_PUB=C:\Program Files (x86)\%%~V\Common7\IDE\PublicAssemblies"
        )
    )
)

if not exist "%EXT_DLL%" (
    echo [ERROR] extensibility.dll not found. Classic Visual Studio 2005-2013 may not be installed.
    exit /b 1
)
if not defined VS_PUB (
    echo [ERROR] EnvDTE.dll not found in Visual Studio 2005-2013 PublicAssemblies.
    exit /b 1
)

set "OUT_DIR=%~dp0..\bin"
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
set "OUT_DLL=%OUT_DIR%\VS2005QwenRAG.dll"

echo [1/3] Compiling C# assembly with csc.exe (%VS_PUB%)...
"%CSC%" /target:library /platform:x86 /optimize+ "/out:%OUT_DLL%" ^
    "/r:%EXT_DLL%" ^
    "/r:%VS_PUB%\EnvDTE.dll" ^
    "/r:%VS_PUB%\EnvDTE80.dll" ^
    "/r:%VS_PUB%\Microsoft.VisualStudio.CommandBars.dll" ^
    /r:System.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Data.dll ^
    Properties\AssemblyInfo.cs Core\Models.cs Core\DteNavigator.cs Core\Connect.cs ^
    Network\MiniJson.cs Network\BackendClient.cs UI\AssistantControl.Designer.cs UI\AssistantControl.cs

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Compilation failed!
    exit /b 1
)

echo [2/3] Registering COM types with 32-bit RegAsm.exe /codebase...
if exist "%REGASM%" (
    "%REGASM%" "%OUT_DLL%" /codebase >nul 2>&1
    echo [OK] Registered COM / ActiveX types.
) else (
    echo [WARNING] RegAsm.exe not found, skipping.
)

echo [3/3] Registering .AddIn manifests for Visual Studio 2005-2013...
call "%~dp0register_addin.bat"

echo [SUCCESS] Visual Studio Add-in successfully built and registered!
exit /b 0
