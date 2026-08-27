@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

title seeed-arm-control 启动
echo ========================================
echo   seeed-arm-control 本地验收启动
echo   页面: http://localhost:5173
echo ========================================
echo.

where uv >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 uv。请先安装: https://docs.astral.sh/uv/
  pause
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 npm。请先安装 Node.js。
  pause
  exit /b 1
)

echo [1/4] 同步 Python 依赖...
uv sync
if errorlevel 1 (
  echo [错误] uv sync 失败
  pause
  exit /b 1
)

if not exist "frontend\node_modules\" (
  echo [2/4] 安装前端依赖...
  pushd frontend
  call npm install
  if errorlevel 1 (
    popd
    echo [错误] npm install 失败
    pause
    exit /b 1
  )
  popd
) else (
  echo [2/4] 前端依赖已存在，跳过 npm install
)

rem 启动真机检测。后端会扫描已注册的机械臂类型并持续重试连接。
set "REBOT_MODE_LABEL=真机自动检测（未检测到时会重试）"

echo [3/4] 启动后端 http://127.0.0.1:8000 （%REBOT_MODE_LABEL%，新窗口）...
rem /D supplies the working directory.  Keeping assignments inside the child
rem command without nested quotes ensures they are not expanded to empty.
rem Do not use uvicorn --reload here. Its watcher can outlive the launcher
rem window on Windows and retain exclusive COM handles after a restart.
start "seeed-arm-backend" /D "%~dp0" cmd /c "uv run uvicorn backend.app:app --host 127.0.0.1 --port 8000"

echo [4/4] 启动前端 http://localhost:5173 （新窗口）...
start "seeed-arm-frontend" /D "%~dp0frontend" cmd /c "npm run dev -- --host 127.0.0.1 --port 5173"

echo.
echo 等待服务就绪后打开浏览器...
timeout /t 4 /nobreak >nul
start "" "http://localhost:5173"

echo.
echo 已打开浏览器。关闭两个标题为 seeed-arm-backend / seeed-arm-frontend 的窗口即可停止服务。
echo 本窗口可关闭。
pause
