@echo off
cd /d "%~dp0"
echo ===============================================
echo   yuki 环境安装（只需一次，约 2-3 分钟）
echo ===============================================
echo.
where py >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 Python 启动器 py.exe，请先安装 Python 3.11。
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/2] 创建虚拟环境 .venv ...
  py -3.11 -m venv .venv
  if errorlevel 1 (
    echo [错误] 创建虚拟环境失败。
    pause
    exit /b 1
  )
) else (
  echo [1/2] 虚拟环境已存在，跳过。
)
echo [2/2] 安装依赖 ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r "server\requirements.txt"
if errorlevel 1 (
  echo.
  echo [错误] 依赖安装失败，请检查网络后重试。
  pause
  exit /b 1
)
if not exist "server\config.json" (
  copy "server\config.example.json" "server\config.json" >nul
  echo 已从模板生成 server\config.json
)
echo.
echo 安装完成！接下来：
echo   1. 编辑 server\config.json 填 api_key（或启动后在设置面板里填）
echo   2. 按 docs\SETUP_ASSETS.md 准备 vendor / models / Electron
echo   3. 双击 启动.bat 或运行 npm start
echo.
pause
