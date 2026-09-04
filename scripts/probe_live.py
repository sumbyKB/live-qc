#!/usr/bin/env python3
"""Probe Douyin / TikTok live rooms for online status, concurrently.

Usage:
    python3 probe_live.py <room_id|@handle|url>[,<room_id|@handle|url>...]
    python3 probe_live.py --file rooms.csv

Input per room (platform auto-detected):
    Douyin : numeric web_rid, e.g. 641012837749
    TikTok : @handle, handle, https://www.tiktok.com/@user/live, or a
             vt.tiktok.com short link (resolved via redirect)

CSV format (no header needed): room_or_handle,account_name  (platforms may mix)
Output: one line per room -> id | name | LIVE/OFF/ERR | title
"""
import argparse
import concurrent.futures
import os
import re
import sys

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.7680.31 Safari/537.36"
    ),
    "Referer": "https://live.douyin.com/",
}
# 代理通过环境变量 DOUYIN_PROXY 配置（如 http://127.0.0.1:8118），未设置则直连。
# 注意：douyin.com 对机房/云 IP 可能风控，本地家庭/办公网络一般无需代理。
PROXIES = {"http": os.environ["DOUYIN_PROXY"], "https": os.environ["DOUYIN_PROXY"]} if os.environ.get("DOUYIN_PROXY") else None

# TikTok 国内网络直连不通，必须走代理：优先 TIKTOK_PROXY 环境变量（设为 direct
# 强制直连），否则自动读系统代理（Windows 注册表 / http_proxy 环境变量）。
TIKTOK_HANDLE_RE = re.compile(r"tiktok\.com/@([\w.\-]+)", re.I)
TIKTOK_SHORT_RE = re.compile(r"(?:vt\.tiktok\.com|tiktok\.com/t/)", re.I)


def tiktok_proxies():
    env = os.environ.get("TIKTOK_PROXY", "").strip()
    if env:
        if env.lower() in ("direct", "none", "off"):
            return None
        return {"http": env, "https": env}
    try:
        from urllib.request import getproxies

        sys_proxy = getproxies().get("https") or getproxies().get("http")
        if sys_proxy:
            return {"http": sys_proxy, "https": sys_proxy}
    except Exception:  # noqa: BLE001
        pass
    return None


def detect(raw):
    """Classify an input token -> ('douyin', id) or ('tiktok', token)."""
    s = str(raw).strip()
    if "tiktok.com" in s.lower():
        return "tiktok", s
    if s.startswith("@"):
        return "tiktok", s[1:]
    if re.fullmatch(r"\d+", s):
        return "douyin", s
    return "tiktok", s  # bare word like "steapex.th" can only be a TikTok handle


def resolve_short_link(url, proxies):
    """Follow a vt.tiktok.com short link and return the @handle it points to."""
    try:
        resp = requests.get(url, headers=HEADERS, proxies=proxies, timeout=25,
                            allow_redirects=False)
        target = resp.headers.get("Location", "")
        if not target and resp.ok:
            target = resp.url
        match = TIKTOK_HANDLE_RE.search(target)
        if match:
            return match.group(1)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] short link resolve failed: {exc}", file=sys.stderr)
    return None


def probe_tiktok(item):
    token, name = item
    proxies = tiktok_proxies()
    handle = token
    if "tiktok.com" in token.lower():
        match = TIKTOK_HANDLE_RE.search(token)
        if match:
            handle = match.group(1)
        elif TIKTOK_SHORT_RE.search(token):
            handle = resolve_short_link(token, proxies)
            if not handle:
                return (token, name, "ERR", "short link unresolved")
        else:
            return (token, name, "ERR", "not a /live or /@user link")
    try:
        api = (f"https://www.tiktok.com/api-live/user/room/?aid=1988&sourceType=54"
               f"&uniqueId={handle}")
        data = requests.get(api, headers=HEADERS, proxies=proxies, timeout=20).json()
        info = data.get("data") or {}
        user = info.get("user") or {}
        status = user.get("status")
        if status is None:
            status = (info.get("liveRoom") or {}).get("status")
        title = (info.get("liveRoom") or {}).get("title") or ""
        return ("@" + handle, user.get("nickname") or name,
                "LIVE" if status == 2 else "OFF", title[:40])
    except Exception as exc:  # noqa: BLE001
        return ("@" + handle, name, "ERR", str(exc)[:50])


def probe_douyin(item):
    room_id, name = item
    url = f"https://live.douyin.com/{room_id}"
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=20)
            html = resp.text
            is_live = ('"status":2' in html) or (".flv" in html)
            title = ""
            match = re.search(r'"title":"([^"]{2,60})"', html)
            if match:
                title = match.group(1)
            return (room_id, name, "LIVE" if is_live else "OFF", title[:40])
        except Exception as exc:  # noqa: BLE001
            if attempt == 1:
                return (room_id, name, "ERR", str(exc)[:50])
    return (room_id, name, "ERR", "unknown")


def probe(item):
    platform, value = detect(item[0])
    if platform == "tiktok":
        return probe_tiktok((value, item[1]))
    return probe_douyin((value, item[1]))


def load_rooms(args):
    if args.file:
        rooms = []
        with open(args.file, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                room_id = parts[0]
                name = parts[1] if len(parts) > 1 else room_id
                rooms.append((room_id, name))
        return rooms
    return [(rid.strip(), rid.strip()) for rid in args.rooms.split(",") if rid.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rooms", nargs="?", default="",
                        help="comma separated: douyin room ids / tiktok handles / urls")
    parser.add_argument("--file", help="CSV file: room_or_handle,account_name")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    rooms = load_rooms(args)
    if not rooms:
        parser.error("provide room ids or --file")

    live_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(probe, rooms):
            print(" | ".join(str(x) for x in result))
            if result[2] == "LIVE":
                live_count += 1
    print(f"\n[summary] {live_count}/{len(rooms)} rooms LIVE", file=sys.stderr)


if __name__ == "__main__":
    main()
