@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

title seeed-arm-control 启动
echo ========================================
echo   seeed-arm-control 本地验收启动
echo   Mock 模式（无真机串口）
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

echo [3/4] 启动后端 http://127.0.0.1:8000 （新窗口）...
start "seeed-arm-backend" cmd /k "cd /d "%~dp0" && set REBOT_MOCK=1 && uv run uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000"

echo [4/4] 启动前端 http://localhost:5173 （新窗口）...
start "seeed-arm-frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev -- --host 127.0.0.1 --port 5173"

echo.
echo 等待服务就绪后打开浏览器...
timeout /t 4 /nobreak >nul
start "" "http://localhost:5173"

echo.
echo 已打开浏览器。关闭两个标题为 seeed-arm-backend / seeed-arm-frontend 的窗口即可停止服务。
echo 本窗口可关闭。
pause
