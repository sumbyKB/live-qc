@echo off
REM ============================================================
REM  douyin-live-qc 调试浏览器一键启动脚本
REM  启动后浏览器会以远程调试模式运行（端口 9222），
REM  首次使用请在打开的浏览器里登录抖音，登录态会保存在
REM  chrome-debug-profile 目录，下次启动无需重复登录。
REM ============================================================

set CHROME=C:\Users\Administrator\AppData\Local\Google\Chrome\Application\chrome.exe
set PROFILE=C:\Users\Administrator\.workbuddy\chrome-debug-profile

if not exist "%CHROME%" (
    echo [ERROR] 未找到 Chrome，请修改脚本中的 CHROME 路径
    pause
    exit /b 1
)

echo 正在启动调试浏览器（端口 9222）...
start "" "%CHROME%" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check --disable-gpu about:blank

timeout /t 3 >nul
echo 验证调试端口...
curl -s http://127.0.0.1:9222/json/version | findstr /i "Browser"
if %errorlevel%==0 (
    echo [OK] 调试端口就绪，请打开 https://www.douyin.com 并登录抖音
) else (
    echo [WARN] 端口未就绪，请稍后手动执行：curl http://127.0.0.1:9222/json/list
)
pause
