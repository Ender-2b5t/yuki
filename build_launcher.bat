@echo off
cd /d "%~dp0"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" (
  echo [错误] 找不到 csc.exe，需要 .NET Framework 4.x。
  pause
  exit /b 1
)
if exist "yuki.ico" (
  "%CSC%" /target:winexe /win32icon:"yuki.ico" /out:"yuki.exe" launcher.cs
) else (
  "%CSC%" /target:winexe /out:"yuki.exe" launcher.cs
)
if exist "yuki.exe" (
  echo 已生成 yuki.exe，双击即可启动。
) else (
  echo [错误] 编译失败。
)
pause
