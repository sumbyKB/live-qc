#!/usr/bin/env python3
"""Extract Douyin / TikTok live stream URL and record it.

Douyin: pulls the FLV URL via Chrome DevTools Protocol.
  - auto-discovers a usable browser tab (tab ids change between sessions)
  - unescapes \\u0026 back to & in extracted URLs
  - prefers quality _or4 (FULL_HD1) > _hd > _sd > _ld
  - stream ids rotate every session, so URLs must always be fetched live
  - signed URLs expire (~1h), so record immediately after extraction

TikTok: web rooms need login in the browser, so the primary path uses yt-dlp
(its tiktok:live extractor signs the webcast API internally, no browser or
login needed). ffmpeg cannot use the system proxy on its own, so pass
-http_proxy explicitly. Falls back to the CDP path when yt-dlp fails (e.g.
after a TikTok signature change) — that fallback requires a logged-in TikTok
session in the debug browser.

Usage:
    # extract only
    python3 record_live.py --room 641012837749 --extract-only
    python3 record_live.py --room @steapex.th --extract-only

    # extract + record 300s
    python3 record_live.py --room 641012837749 --duration 300 --output out.mp4
    python3 record_live.py --room "https://vt.tiktok.com/ZS9Bxxxx/" --duration 180

    # force the browser path for TikTok
    python3 record_live.py --room @onke_th --use-cdp

Requires: websocket-client, requests, ffmpeg; yt-dlp for TikTok; a Chrome on
--remote-debugging-port=9222 with a logged-in Douyin session (Douyin, and
TikTok only for the CDP fallback).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

import requests
import websocket

CDP_BASE = "http://127.0.0.1:9222"
QUALITY_ORDER = ["_or4", "_hd", "_sd", "_ld"]
# TikTok rooms expose one adaptive FLV (hd) plus an HLS rendition of it;
# yt-dlp format ids observed in practice, best first.
TIKTOK_FORMATS = "flv-hd/rtmp-pull/flv-hd1/flv-hd/hls-hd/best"

TIKTOK_HANDLE_RE = re.compile(r"tiktok\.com/@([\w.\-]+)", re.I)
TIKTOK_SHORT_RE = re.compile(r"(?:vt\.tiktok\.com|tiktok\.com/t/)", re.I)

DOUYIN_EXTRACT_JS = r"""
(function() {
    var urls = [];
    try {
        var entries = performance.getEntriesByType('resource');
        for (var i = 0; i < entries.length; i++) {
            if (entries[i].name && entries[i].name.indexOf('.flv') !== -1) {
                urls.push(entries[i].name);
            }
        }
    } catch (e) {}
    try {
        var scripts = document.querySelectorAll('script');
        for (var j = 0; j < scripts.length; j++) {
            var text = scripts[j].textContent;
            if (text && text.indexOf('.flv') !== -1) {
                var m = text.match(/https?:\/\/[^"']+\.flv[^"']*/g);
                if (m) urls = urls.concat(m);
            }
        }
    } catch (e) {}
    return JSON.stringify(urls);
})()
"""

# TikTok web rooms embed the room state in __UNIVERSAL_DATA_FOR_REHYDRATION__
# (streamData.hls_pull_url_map: {ld,sd,hd,...}) and the player fetches
# .m3u8/.flv URLs that show up in performance resource entries.
TIKTOK_EXTRACT_JS = r"""
(function() {
    var urls = [];
    try {
        var entries = performance.getEntriesByType('resource');
        for (var i = 0; i < entries.length; i++) {
            var n = entries[i].name || '';
            if (n.indexOf('.m3u8') !== -1 || n.indexOf('.flv') !== -1) urls.push(n);
        }
    } catch (e) {}
    try {
        var scripts = document.querySelectorAll('script');
        for (var j = 0; j < scripts.length; j++) {
            var text = scripts[j].textContent || '';
            if (text.indexOf('.m3u8') !== -1 || text.indexOf('.flv') !== -1) {
                urls = urls.concat(text.match(/https?:\/\/[^"']+\.m3u8[^"']*/g) || []);
                urls = urls.concat(text.match(/https?:\/\/[^"']+\.flv[^"']*/g) || []);
            }
        }
    } catch (e) {}
    var logged_in = !document.body.innerText.match(/Log in|登录|登入/);
    return JSON.stringify({urls: urls, logged_in: logged_in});
})()
"""


def pick_tab():
    """Return webSocketDebuggerUrl of a usable page tab, creating one if needed."""
    try:
        tabs = requests.get(f"{CDP_BASE}/json/list", timeout=10).json()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"[fatal] cannot reach browser debug port: {exc}")
    pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        try:
            requests.put(f"{CDP_BASE}/json/new?about:blank", timeout=10)
            time.sleep(2)
            tabs = requests.get(f"{CDP_BASE}/json/list", timeout=10).json()
            pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        except Exception:  # noqa: BLE001
            pass
    if not pages:
        sys.exit("[fatal] no usable browser tab found")
    return pages[0]["webSocketDebuggerUrl"]


def normalize(url):
    """Unescape \\u0026 -> & and strip trailing backslashes."""
    url = url.replace("\\u0026", "&").replace("\\/", "/")
    return url.rstrip("\\")


def detect(raw):
    """Classify --room input -> ('douyin', id) or ('tiktok', token)."""
    s = str(raw).strip()
    if "tiktok.com" in s.lower():
        return "tiktok", s
    if s.startswith("@"):
        return "tiktok", s[1:]
    if re.fullmatch(r"\d+", s):
        return "douyin", s
    return "tiktok", s  # bare word like "steapex.th" can only be a TikTok handle


def tiktok_proxy():
    """Proxy for TikTok traffic. Env TIKTOK_PROXY wins (direct/none/off = no
    proxy), else the OS-level proxy (Windows registry / env vars)."""
    env = os.environ.get("TIKTOK_PROXY", "").strip()
    if env:
        if env.lower() in ("direct", "none", "off"):
            return None
        return env
    try:
        from urllib.request import getproxies

        sys_proxy = getproxies().get("https") or getproxies().get("http")
        if sys_proxy:
            return sys_proxy
    except Exception:  # noqa: BLE001
        pass
    return None


def resolve_tiktok_handle(token, proxy):
    """Turn any TikTok input (short link, /live URL, @handle) into a handle."""
    match = TIKTOK_HANDLE_RE.search(token)
    if match:
        return match.group(1)
    if TIKTOK_SHORT_RE.search(token):
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            resp = requests.get(token, headers={"User-Agent": "Mozilla/5.0"},
                                proxies=proxies, timeout=25, allow_redirects=False)
            target = resp.headers.get("Location", "") or (resp.url if resp.ok else "")
            match = TIKTOK_HANDLE_RE.search(target)
            if match:
                return match.group(1)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] short link resolve failed: {exc}", file=sys.stderr)
    return None if "tiktok.com" in token.lower() else token


def extract_tiktok_url_ytdlp(handle, proxy):
    """Get a direct stream URL via yt-dlp's tiktok:live extractor (2 tries)."""
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        sys.exit("[fatal] yt-dlp not found on PATH (required for TikTok recording)")
    url = f"https://www.tiktok.com/@{handle}/live"
    env = dict(os.environ)
    if proxy:
        env["HTTP_PROXY"] = env["http_proxy"] = proxy
        env["HTTPS_PROXY"] = env["https_proxy"] = proxy
    cmd = [yt_dlp, "-f", TIKTOK_FORMATS, "-g", "--no-wait",
           "--socket-timeout", "25", url]
    for attempt in range(2):
        print(f"[info] yt-dlp extracting stream (attempt {attempt + 1}/2)", file=sys.stderr)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=150)
        lines = [line.strip() for line in (proc.stdout or "").splitlines() if "://" in line]
        if lines:
            return normalize(lines[-1])
        err = (proc.stderr or "").strip().splitlines()
        print(f"[warn] yt-dlp: {err[-1][:160] if err else 'no output'}", file=sys.stderr)
    return None


def extract_stream_cdp(platform, room, wait):
    """Navigate the debug browser to the room and scrape stream URLs (Douyin;
    TikTok only as fallback, requires a logged-in TikTok session)."""
    ws_url = pick_tab()
    ws = websocket.create_connection(ws_url, timeout=40)
    counter = {"id": 1}

    def send(method, params=None):
        msg_id = counter["id"]
        counter["id"] += 1
        payload = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        ws.send(json.dumps(payload))
        deadline = time.time() + 40
        while time.time() < deadline:
            resp = json.loads(ws.recv())
            if resp.get("id") == msg_id:
                return resp
        return {}

    send("Network.enable")
    send("Page.enable")
    if platform == "tiktok":
        live_url = f"https://www.tiktok.com/@{room}/live"
        extract_js = TIKTOK_EXTRACT_JS
    else:
        live_url = f"https://live.douyin.com/{room}"
        extract_js = DOUYIN_EXTRACT_JS
    print(f"[info] navigating to {live_url}", file=sys.stderr)
    send("Page.navigate", {"url": live_url})
    print(f"[info] waiting {wait}s for stream to load", file=sys.stderr)
    time.sleep(wait)

    result = send("Runtime.evaluate", {"expression": extract_js, "returnByValue": True})
    ws.close()

    raw = result.get("result", {}).get("result", {}).get("value", "[]")
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        parsed = re.findall(r"https?://[^\"']+\.(?:flv|m3u8)[^\"']*", raw)
    if platform == "tiktok" and isinstance(parsed, dict):
        if not parsed.get("logged_in", True):
            print("[warn] TikTok page looks logged out — guest users get "
                  "redirected to the live feed; log in TikTok in the debug browser",
                  file=sys.stderr)
        parsed = parsed.get("urls", [])

    seen, unique = set(), []
    for url in parsed:
        norm = normalize(url)
        if norm not in seen:
            seen.add(norm)
            unique.append(norm)
    return unique


def pick_quality(urls):
    """Return highest-quality URL. Skips audio-only streams (Douyin ids)."""
    candidates = [u for u in urls if "only_audio=1" not in u]
    for suffix in QUALITY_ORDER:
        for url in candidates:
            if suffix + ".flv" in url:
                return url, suffix
    for url in candidates:  # TikTok: prefer FLV over HLS for ffmpeg robustness
        if ".flv" in url:
            return url, "tiktok-flv"
    return (candidates[0], "unknown") if candidates else (None, None)


def record(stream_url, duration, output, proxy=None):
    cmd = ["ffmpeg", "-y"]
    if proxy:
        cmd += ["-http_proxy", proxy]
    cmd += ["-i", stream_url,
            "-t", str(duration), "-c", "copy", "-bsf:a", "aac_adtstoasc", output]
    print(f"[info] recording {duration}s -> {output}", file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-1500:], file=sys.stderr)
        sys.exit(f"[fatal] ffmpeg failed rc={proc.returncode}")
    if not os.path.exists(output) or os.path.getsize(output) < 100000:
        sys.exit("[fatal] output missing or too small; stream may have ended")
    size_mb = os.path.getsize(output) / 1024 / 1024
    print(f"[ok] recorded {output} ({size_mb:.1f} MB)", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", required=True,
                        help="Douyin web_rid / TikTok @handle / live URL / short link")
    parser.add_argument("--duration", type=int, default=300, help="seconds, default 300")
    parser.add_argument("--output", help="output mp4 path")
    parser.add_argument("--wait", type=int, default=22, help="seconds to wait for stream (CDP path)")
    parser.add_argument("--extract-only", action="store_true")
    parser.add_argument("--use-cdp", action="store_true",
                        help="TikTok: skip yt-dlp, use the browser (needs TikTok login)")
    args = parser.parse_args()

    platform, room = detect(args.room)
    proxy = tiktok_proxy() if platform == "tiktok" else None

    if platform == "tiktok":
        handle = resolve_tiktok_handle(room, proxy)
        if not handle:
            sys.exit("[fatal] could not resolve a TikTok @handle from the input")
        print(f"[info] tiktok handle: @{handle} (proxy: {proxy or 'direct'})", file=sys.stderr)
        if not args.use_cdp:
            best = extract_tiktok_url_ytdlp(handle, proxy)
            if best:
                print("[ok] yt-dlp stream url obtained", file=sys.stderr)
                if args.extract_only:
                    print(best)
                    return
                output = args.output or f"live_{handle}_{args.duration}s.mp4"
                record(best, args.duration, output, proxy=proxy)
                print(output)
                return
            print("[warn] yt-dlp path failed, falling back to browser CDP "
                  "(requires TikTok logged in)", file=sys.stderr)
        urls = extract_stream_cdp("tiktok", handle, args.wait)
        if not urls:
            sys.exit("[fatal] no FLV/M3U8 stream found — room offline, or TikTok "
                     "not logged in in the debug browser (guests are redirected to "
                     "the live feed)")
        best, quality = pick_quality(urls)
        if not best:
            sys.exit("[fatal] only audio-only streams found")
        print(f"[ok] found {len(urls)} stream(s), selected quality={quality}", file=sys.stderr)
        if args.extract_only:
            print(best)
            return
        output = args.output or f"live_{handle}_{args.duration}s.mp4"
        record(best, args.duration, output, proxy=proxy)
        print(output)
        return

    # Douyin (unchanged behavior)
    urls = extract_stream_cdp("douyin", room, args.wait)
    if not urls:
        sys.exit("[fatal] no FLV stream found — room may be offline or login expired")

    best, quality = pick_quality(urls)
    if not best:
        sys.exit("[fatal] only audio-only streams found")
    print(f"[ok] found {len(urls)} stream(s), selected quality={quality}", file=sys.stderr)

    if args.extract_only:
        print(best)
        return

    output = args.output or f"live_{room}_{args.duration}s.mp4"
    record(best, args.duration, output)
    print(output)


if __name__ == "__main__":
    main()
