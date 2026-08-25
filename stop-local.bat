@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在结束本机 8000 / 5173 上的 uvicorn 与 vite（若存在）...

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo 结束 PID %%p ^(8000^)
  taskkill /PID %%p /F >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING"') do (
  echo 结束 PID %%p ^(5173^)
  taskkill /PID %%p /F >nul 2>&1
)

echo 完成。也可直接关掉 seeed-arm-backend / seeed-arm-frontend 窗口。
pause
