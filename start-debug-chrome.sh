#!/usr/bin/env bash
# ============================================================
#  live-qc 调试浏览器一键启动（macOS / Linux）
#  以远程调试模式启动 Chrome/Chromium（端口 9222）。首次使用请在
#  打开的浏览器里登录抖音（如需 TikTok 搜索/CDP 兜底也登录），
#  登录态保存在 profile 目录，下次启动无需重复登录。
#
#  可用环境变量覆盖默认值：
#    CHROME_PATH      浏览器可执行文件完整路径
#    LIVE_QC_PROFILE  登录态 profile 目录（默认 ~/.live-qc/chrome-debug-profile）
# ============================================================
set -u

PROFILE="${LIVE_QC_PROFILE:-$HOME/.live-qc/chrome-debug-profile}"

# 定位浏览器：环境变量 > 常见安装位置
if [ -n "${CHROME_PATH:-}" ]; then
    CHROME="$CHROME_PATH"
else
    for candidate in \
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        "/Applications/Chromium.app/Contents/MacOS/Chromium" \
        "$(command -v google-chrome 2>/dev/null)" \
        "$(command -v google-chrome-stable 2>/dev/null)" \
        "$(command -v chromium 2>/dev/null)" \
        "$(command -v chromium-browser 2>/dev/null)"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            CHROME="$candidate"
            break
        fi
    done
fi

if [ -z "${CHROME:-}" ]; then
    echo "[ERROR] 未找到 Chrome/Chromium，请设置环境变量 CHROME_PATH 后重试"
    exit 1
fi

echo "正在启动调试浏览器（端口 9222）..."
echo "  Chrome : $CHROME"
echo "  Profile: $PROFILE"
"$CHROME" --remote-debugging-port=9222 --remote-allow-origins=* \
    --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
    --disable-gpu about:blank &
CHROME_PID=$!

sleep 3
echo "验证调试端口..."
if curl -s http://127.0.0.1:9222/json/version | grep -qi "browser"; then
    echo "[OK] 调试端口就绪，请打开 https://www.douyin.com 并登录抖音"
    echo "     （Ctrl-C 退出本脚本不会关闭浏览器；彻底关闭用：kill $CHROME_PID）"
else
    echo "[WARN] 端口未就绪，请稍后手动执行：curl http://127.0.0.1:9222/json/list"
    wait $CHROME_PID
fi
