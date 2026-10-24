@echo off
cd /d "%~dp0"
if exist "yuki.exe" (
  start "" "yuki.exe"
  exit /b 0
)
set "ELECTRON_RUN_AS_NODE="
if exist "runtime\electron\electron.exe" (
  start "" "runtime\electron\electron.exe" --no-sandbox "%~dp0."
  exit /b 0
)
if exist "node_modules\electron\dist\electron.exe" (
  start "" "node_modules\electron\dist\electron.exe" --no-sandbox "%~dp0."
  exit /b 0
)
echo [错误] 找不到 yuki.exe / Electron。
echo 请先运行 安装环境.bat，或按 docs\SETUP_ASSETS.md 准备 Electron。
pause
