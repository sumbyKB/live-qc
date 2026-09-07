@echo off
REM ============================================================
REM  live-qc 调试浏览器一键启动（Windows）
REM  以远程调试模式启动 Chrome（端口 9222）。首次使用请在打开的
REM  浏览器里登录抖音（如需 TikTok 搜索/CDP 兜底也登录 TikTok），
REM  登录态保存在 profile 目录，下次启动无需重复登录。
REM
REM  可用环境变量覆盖默认值：
REM    CHROME_PATH      Chrome 可执行文件完整路径
REM    LIVE_QC_PROFILE  登录态 profile 目录
REM ============================================================

setlocal
REM profile 默认值：已有旧 profile（.workbuddy）则沿用以保留登录态，
REM 否则用本 skill 的可移植默认位置；均可用 LIVE_QC_PROFILE 覆盖。
if defined LIVE_QC_PROFILE (
    set "PROFILE=%LIVE_QC_PROFILE%"
    goto :profile_done
)
if exist "%USERPROFILE%\.workbuddy\chrome-debug-profile" (
    set "PROFILE=%USERPROFILE%\.workbuddy\chrome-debug-profile"
) else (
    set "PROFILE=%USERPROFILE%\.live-qc\chrome-debug-profile"
)
:profile_done

if defined CHROME_PATH (
    set "CHROME=%CHROME_PATH%"
    goto :found
)

REM 在常见安装位置自动探测 Chrome
for %%P in (
    "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do (
    if exist %%P (
        set "CHROME=%%~P"
        goto :found
    )
)

echo [ERROR] 未找到 Chrome，请设置环境变量 CHROME_PATH 指向 chrome.exe 后重试
pause
exit /b 1

:found
echo 正在启动调试浏览器（端口 9222）...
echo   Chrome : %CHROME%
echo   Profile: %PROFILE%
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
