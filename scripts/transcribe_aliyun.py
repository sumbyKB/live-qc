#!/usr/bin/env python3
"""Aliyun DashScope ASR (qwen3-asr-flash) — fast transcription path for the
live-qc pipeline. Handles every language Lark Minutes can't (Thai, Taglish,
etc.) in seconds instead of minutes.

Key resolution order:
    1. --key-file PATH          (explicit override)
    2. DASHSCOPE_API_KEY env    (CI / one-off use)
    3. <repo>/local/aliyun_asr.key   (default location, gitignored — NEVER commit)

Exit codes:
    0  transcript written
    1  API/network error (key present but call failed)
    2  NOT_CONFIGURED — no key found; caller (agent) should tell the user how
       to set one up, or fall back to Lark Minutes.

Usage:
    python3 transcribe_aliyun.py rec/stream.mp4               # -> rec/stream.aliyun.txt
    python3 transcribe_aliyun.py audio.mp3 -o transcript.txt
    python3 transcribe_aliyun.py rec/stream.mp4 --check       # only verify key setup

Note: Aliyun endpoints are domestic (China); they must NOT go through the
TikTok proxy. This script forces NO_PROXY="*" so Windows registry/system
proxy (Clash etc.) can't break the SSL handshake.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

# Aliyun is domestic — never route via the TikTok proxy. Must be set before
# any HTTP client touches the network (requests reads no_proxy lazily, but
# scrubbing proxies here covers every code path).
for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(var, None)
os.environ["NO_PROXY"] = "*"

DEFAULT_KEY_FILE = Path(__file__).resolve().parent.parent / "local" / "aliyun_asr.key"
MODEL = "qwen3-asr-flash"
MSG_NOT_CONFIGURED = (
    "[not-configured] 未找到阿里云 DashScope Key（用于泰语等小语种秒级转写）。\n"
    "  首次配置（1分钟）：打开 https://bailian.console.aliyun.com/?tab=model#/api-key\n"
    "  → 免费开通百炼 → 创建 API-KEY（sk- 开头）→ 在仓库根目录新建 local 文件夹，\n"
    f"  把 key 存为一行写入 {DEFAULT_KEY_FILE}\n"
    "  （或命令行：mkdir -p local && echo \"sk-你的key\" > local/aliyun_asr.key）\n"
    "  该目录已被 .gitignore 忽略，key 绝不会被提交或上传。\n"
    "  未配置时流程可继续：回退飞书妙记转写（小语种可能转不出）。"
)


def find_key(explicit: str | None) -> str | None:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            sys.exit(f"[fatal] key file not found: {p}")
        return p.read_text(encoding="utf-8").strip()
    env = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if env:
        return env
    if DEFAULT_KEY_FILE.exists():
        return DEFAULT_KEY_FILE.read_text(encoding="utf-8").strip()
    return None


def extract_audio(src: Path, tmp_mp3: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(tmp_mp3)],
        check=True,
    )


def transcribe(key: str, media: Path) -> str:
    try:
        from dashscope import MultiModalConversation
    except ImportError:
        print("[info] first run: pip-installing dashscope SDK...", file=sys.stderr)
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "dashscope"], check=True)
        from dashscope import MultiModalConversation

    resp = MultiModalConversation.call(
        model=MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"audio": f"file://{media.resolve()}"},
                {"text": ""},  # empty = plain transcription; hotwords can go here
            ],
        }],
        api_key=key,
        stream=False,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"dashscope API error status={resp.status_code} "
            f"code={getattr(resp, 'code', '?')} message={getattr(resp, 'message', resp)}"
        )
    return resp.output.choices[0].message.content[0]["text"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("media", nargs="?", help="mp4/mp3/m4a path")
    parser.add_argument("-o", "--output", help="transcript path (default: <media>.aliyun.txt)")
    parser.add_argument("--key-file", help="override key file path")
    parser.add_argument("--check", action="store_true", help="only verify key setup and exit")
    args = parser.parse_args()

    key = find_key(args.key_file)
    if args.check:
        if key:
            print(f"[ok] key configured ({'env' if os.environ.get('DASHSCOPE_API_KEY') else DEFAULT_KEY_FILE})")
            return
        print(MSG_NOT_CONFIGURED)
        sys.exit(2)
    if not key:
        print(MSG_NOT_CONFIGURED, file=sys.stderr)
        sys.exit(2)
    if not args.media:
        parser.error("media path required unless --check")

    src = Path(args.media)
    if not src.exists():
        sys.exit(f"[fatal] no such file: {src}")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    is_audio = src.suffix.lower() in {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
    tmp_mp3 = None
    if is_audio:
        media = src
    else:
        tmp_mp3 = src.with_suffix(".aliyun_tmp.mp3")
        print(f"[info] extracting 16k mono audio -> {tmp_mp3.name}", file=sys.stderr)
        extract_audio(src, tmp_mp3)
        media = tmp_mp3

    import time
    t0 = time.time()
    try:
        text = transcribe(key, media)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"[fatal] {exc}")
    finally:
        if tmp_mp3 and tmp_mp3.exists():
            tmp_mp3.unlink()

    out = Path(args.output) if args.output else src.with_suffix(".aliyun.txt")
    out.write_text(text, encoding="utf-8")
    print(f"[ok] {len(text)} chars in {time.time() - t0:.1f}s -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
