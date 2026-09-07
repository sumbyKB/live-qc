#!/usr/bin/env python3
"""One-shot environment self-check for the live-qc skill.

Verifies everything SKILL.md's 前置条件 section requires, so an agent (or a
human) can validate a fresh machine in one command. Browser login states
cannot be verified programmatically — those print as manual reminders.

Exit code 0 when all required items pass, 1 otherwise. Warn-level items
(platform- or channel-specific) never fail the run.

Usage:
    python3 scripts/check_env.py        # human-readable report
    python3 scripts/check_env.py --json # machine-readable (for agents)
"""
import argparse
import importlib.util
import json
import os
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(os.path.dirname(HERE), "local", "aliyun_asr.key")

CDP_URL = "http://127.0.0.1:9222/json/version"


def check_module(name, pip_name):
    found = importlib.util.find_spec(name) is not None
    hint = None if found else f"pip3 install {pip_name}"
    return found, hint


def check_cdp():
    try:
        with urllib.request.urlopen(CDP_URL, timeout=3) as resp:
            return resp.status == 200, None
    except Exception as exc:  # noqa: BLE001
        return False, "启动调试浏览器（start-debug-chrome.bat 或 .sh）并完成抖音登录"


def check_aliyun_key():
    if os.environ.get("DASHSCOPE_API_KEY", "").strip():
        return True, None
    if os.path.isfile(KEY_FILE) and os.path.getsize(KEY_FILE) > 0:
        return True, None
    return False, ("小语种（泰语等）将无法转写：创建 sk- Key 存入 local/aliyun_asr.key "
                   "（一行，gitignored），或设 DASHSCOPE_API_KEY 环境变量")


def check_tiktok_proxy():
    env = os.environ.get("TIKTOK_PROXY", "").strip()
    if env:
        return True, None
    try:
        from urllib.request import getproxies
        sys_proxy = getproxies().get("https") or getproxies().get("http")
        if sys_proxy:
            return True, None
    except Exception:  # noqa: BLE001
        pass
    return False, "国内网络直连不通 TikTok：设 TIKTOK_PROXY=http://127.0.0.1:<端口> 或开启系统代理"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def bin_result(name, hint):
        found = shutil.which(name) is not None
        return found, (None if found else hint)

    mod, mod_hint = check_module("requests", "requests")
    wsmod, ws_hint = check_module("websocket", "websocket-client")
    checks = [
        # (名称, 必需, 通过?, 提示)
        ("python 包 requests", True, mod, mod_hint),
        ("python 包 websocket-client", True, wsmod, ws_hint),
        ("ffmpeg", True, *bin_result("ffmpeg", "安装 ffmpeg 并加入 PATH（录制/抽帧必需）")),
        ("Chrome 调试端口 9222", False, *check_cdp()),
        ("yt-dlp", False, *bin_result("yt-dlp", "pip3 install yt-dlp（仅 TikTok 录制需要）")),
        ("lark-cli", False, *bin_result("lark-cli", "影响交付渠道：无则转写走阿里云 ASR、报告落本地 md")),
        ("阿里云 ASR Key", False, *check_aliyun_key()),
        ("TikTok 代理", False, *check_tiktok_proxy()),
    ]

    if args.json:
        print(json.dumps({
            "checks": [{"item": n, "required": req, "ok": ok, "hint": hint}
                       for n, req, ok, hint in checks],
            "manual": ["抖音登录态：调试浏览器中打开 douyin.com 确认已登录",
                       "TikTok 登录态：仅搜索/CDP 兜底需要，在调试浏览器确认"],
            "all_required_ok": all(ok for _, req, ok, _ in checks if req),
        }, ensure_ascii=False, indent=2))
        return 0 if all(ok for _, req, ok, _ in checks if req) else 1

    all_ok = True
    for name, required, ok, hint in checks:
        mark = "[OK]  " if ok else ("[MISS]" if required else "[WARN]")
        if required and not ok:
            all_ok = False
        print(f"{mark} {name}" + (f" — {hint}" if hint and not ok else ""))
    print()
    print("人工确认（脚本无法自动验证）：")
    print("  - 抖音登录态：调试浏览器中打开 https://www.douyin.com 确认已登录")
    print("  - TikTok 登录态：仅搜索/CDP 兜底录制需要，在调试浏览器确认")
    print(f"\n[summary] 必需项{'全部就绪' if all_ok else '有缺失，先解决再继续'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
