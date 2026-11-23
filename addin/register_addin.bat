@echo off
setlocal
set "OUT_DLL=%~dp0..\bin\VS2005QwenRAG.dll"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_addin.ps1" "%OUT_DLL%"
