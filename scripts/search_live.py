#!/usr/bin/env python3
"""Search Douyin / TikTok for live rooms by keyword via Chrome DevTools Protocol.

Given an account name or brand name, opens the platform's live-search page,
scrolls to load results, and extracts all currently-live rooms.

Douyin: room_id, account name, live title, verification badge.
TikTok: @handle, nickname, title/viewers. TikTok search REQUIRES a logged-in
TikTok session in the debug browser (guests hit a hard login wall).

Usage:
    python3 search_live.py "某品牌官方旗舰店"                # douyin (default)
    python3 search_live.py "shoes" --platform tiktok
    python3 search_live.py "steapex" --platform tiktok --mode users   # 品牌矩阵号发现
    python3 search_live.py "某品牌名" --json --scroll 5

Requires: websocket-client, requests, a Chrome running with
--remote-debugging-port=9222; Douyin logged in (douyin) / TikTok logged in
(tiktok).
"""
import argparse
import json
import re
import sys
import time
import urllib.parse

import requests
import websocket

CDP_BASE = "http://127.0.0.1:9222"

# JS to extract live room cards from the Douyin search results page.
# Tries multiple strategies because Douyin's DOM structure changes occasionally.
DOUYIN_EXTRACT_JS = r"""
(function() {
    var results = [];
    var seen = {};

    // Strategy 1: find all anchors linking to live.douyin.com/<digits>
    var anchors = document.querySelectorAll('a[href*="live.douyin.com/"]');
    for (var i = 0; i < anchors.length; i++) {
        var a = anchors[i];
        var href = a.href || a.getAttribute('href') || '';
        var m = href.match(/live\.douyin\.com\/(\d+)/);
        if (!m) continue;
        var roomId = m[1];
        if (seen[roomId]) continue;

        // Walk up to find a card-like container
        var card = a;
        for (var up = 0; up < 6; up++) {
            if (!card.parentElement) break;
            card = card.parentElement;
            var text = card.innerText || '';
            if (text.length > 20 && text.length < 500) break;
        }

        var cardText = (card.innerText || '').trim();
        var lines = cardText.split('\n').map(function(s){return s.trim();}).filter(Boolean);

        // Account name: usually near the top of the card, may contain "· 正在直播"
        var name = '';
        var title = '';
        var isVerified = false;

        // Look for verification badge (blue V) in the card
        if (card.querySelector('[class*="verify"], [class*="Verified"], svg[class*="verify"]')) {
            isVerified = true;
        }

        // Try to find name: usually a short line before "正在直播"
        for (var j = 0; j < lines.length; j++) {
            var line = lines[j];
            if (line.indexOf('正在直播') !== -1 || line.indexOf('直播中') !== -1) {
                // Name is often on the same line before the dot, or the previous line
                var parts = line.split(/[·•]/);
                if (parts.length > 1 && parts[0].trim().length > 0) {
                    name = parts[0].trim();
                } else if (j > 0) {
                    name = lines[j-1];
                }
                // Title is usually a few lines after
                for (var k = j+1; k < lines.length && k < j+5; k++) {
                    if (lines[k].length > 4 && lines[k].indexOf('正在直播') === -1
                        && lines[k].indexOf('直播中') === -1
                        && lines[k].indexOf('人观看') === -1
                        && lines[k].indexOf('点赞') === -1) {
                        title = lines[k];
                        break;
                    }
                }
                break;
            }
        }

        // Fallback: use anchor text or first meaningful line
        if (!name) {
            name = (a.innerText || '').trim().split('\n')[0] || lines[0] || '';
        }
        if (!title && lines.length > 1) {
            for (var t = 0; t < lines.length; t++) {
                if (lines[t] !== name && lines[t].length > 4) {
                    title = lines[t];
                    break;
                }
            }
        }

        seen[roomId] = true;
        results.push({
            room_id: roomId,
            name: name.substring(0, 40),
            title: (title || '').substring(0, 80),
            verified: isVerified,
            href: href.split('?')[0]
        });
    }

    return JSON.stringify(results);
})()
"""

# TikTok live-search card text is 4 lines: [LIVE badge, viewer count, title,
# nickname] (badge/wording varies by UI locale, e.g. 直播/LIVE/ดูสด).
TIKTOK_EXTRACT_JS = r"""
(function() {
    function norm(s) { return s.toLowerCase().replace(/[^a-z0-9一-鿿]/g, ''); }
    var BADGES = {live:1, '直播':1, '直播中':1, 'ดูสด':1, 'ไลฟ์สด':1, 'สด':1, 'trực tiếp':1};
    var results = [];
    var seen = {};
    var anchors = document.querySelectorAll('a[href*="/live"]');
    for (var i = 0; i < anchors.length; i++) {
        var href = anchors[i].href || '';
        var m = href.match(/tiktok\.com\/@([\w.\-]+)\/live/);
        if (!m || seen[m[1]]) continue;

        var card = anchors[i];
        for (var up = 0; up < 6 && card.parentElement; up++) {
            card = card.parentElement;
            if ((card.innerText || '').length > 30) break;
        }
        var lines = (card.innerText || '').split('\n')
            .map(function(s){ return s.trim(); }).filter(Boolean);

        var viewers = '';
        var name = '';
        var rest = [];
        for (var j = 0; j < lines.length; j++) {
            var line = lines[j];
            if (BADGES[line.toLowerCase()]) continue;
            if (!viewers && /^[\d.,]+\s*[KMB]?$/.test(line) && line.length <= 8) {
                viewers = line;
                continue;
            }
            rest.push(line);
        }
        var handle = m[1];
        for (var k = 0; k < rest.length; k++) {
            var n = norm(rest[k]);
            if (n && (handle.indexOf(n) === 0 || n.indexOf(handle) === 0)) {
                name = rest[k];
                rest.splice(k, 1);
                break;
            }
        }
        if (!name && rest.length) { name = rest.shift(); }
        var title = '';
        for (var t = 0; t < rest.length; t++) {
            if (rest[t].length > title.length && rest[t].length <= 80) title = rest[t];
        }

        seen[handle] = true;
        results.push({
            room_id: '@' + handle,
            name: name.replace(/^@/, '').substring(0, 40),
            title: ((title || '') + (viewers ? ' · ' + viewers : '')).substring(0, 80),
            verified: false,
            href: 'https://www.tiktok.com/@' + handle + '/live'
        });
    }
    return JSON.stringify(results);
})()
"""

# TikTok user-search page: cards are NOT ancestors of the profile <a> (flat DOM),
# so anchor walk-up fails. Parse document.innerText instead — card text follows a
# stable line pattern: [LIVE] / nickname / @handle / count / 粉丝 / · / count / 赞.
# Used by --mode users to discover a brand's matrix accounts (not just live ones).
TIKTOK_USER_EXTRACT_JS = r"""
(function() {
    var FOLLOWER_MARK = /^(?:[\d.,]+\s*[KMB]?\s*)?(粉丝|Followers|ผู้ติดตาม|フォロワー|팔로워)$/i;
    var lines = (document.body.innerText || '')
        .split('\n').map(function(s){ return s.trim(); }).filter(Boolean);
    var seen = {};
    var results = [];
    for (var i = 0; i < lines.length; i++) {
        if (!FOLLOWER_MARK.test(lines[i]) || i < 2) continue;
        var count = /^[\d.,]/.test(lines[i]) ? lines[i].replace(/(粉丝|Followers|ผู้ติดตาม|フォロワー|팔로워)/gi, '').trim()
                                             : lines[i - 1];
        var handle = lines[i - 2].replace(/^@/, '');
        if (!/^[\w.\-]{2,30}$/.test(handle) || seen[handle.toLowerCase()]) continue;
        // nickname sits right above the handle, skipping a LIVE badge if present
        var n = i - 3, name = '';
        while (n >= 0 && i - n <= 4) {
            if (lines[n] !== 'LIVE') { name = lines[n]; break; }
            n--;
        }
        var isLive = false;
        for (var k = Math.max(0, n - 1); k <= n; k++) {
            if (lines[k] === 'LIVE') { isLive = true; break; }
        }
        var likes = '';
        if (lines[i + 1] === '·' && /^[\d.,]+\s*[KMB]?$/.test(lines[i + 2] || '')) {
            likes = lines[i + 2];
        }
        seen[handle.toLowerCase()] = true;
        results.push({
            room_id: '@' + handle,
            name: (name || handle).substring(0, 40),
            title: ('粉丝 ' + count + (likes ? ' · 获赞 ' + likes : '')
                    + (isLive ? ' · LIVE' : '')).substring(0, 80),
            verified: false,
            href: 'https://www.tiktok.com/@' + handle
        });
    }
    return JSON.stringify(results);
})()
"""

# Body text that means TikTok served the login wall instead of search results.
TIKTOK_LOGIN_MARKERS = re.compile(r"登录以搜索|登錄以搜索|Log in to search|เข้าสู่ระบบเพื่อค้นหา", re.I)


def pick_tab():
    """Return webSocketDebuggerUrl of a usable page tab."""
    try:
        tabs = requests.get(f"{CDP_BASE}/json/list", timeout=10).json()
    except Exception as exc:
        sys.exit(f"[fatal] cannot reach browser debug port: {exc}")
    pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        sys.exit("[fatal] no usable browser tab found — is Chrome running with --remote-debugging-port=9222?")
    return pages[0]["webSocketDebuggerUrl"]


def cdp_send(ws, counter, method, params=None):
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


def evaluate_json(ws, counter, expression):
    """Run extract JS and parse its JSON payload (empty list on failure)."""
    result = cdp_send(ws, counter, "Runtime.evaluate",
                      {"expression": expression, "returnByValue": True})
    raw = result.get("result", {}).get("result", {}).get("value", "[]")
    try:
        return json.loads(raw)
    except Exception:
        return []


def scroll_and_collect(ws, counter, extract_js, scroll_rounds, wait_between):
    """Extract after each incremental scroll (virtual lists drop off-screen DOM)."""
    all_results = {}
    for i in range(scroll_rounds):
        for item in evaluate_json(ws, counter, extract_js):
            all_results[item["room_id"]] = item
        if i < scroll_rounds - 1:
            cdp_send(ws, counter, "Runtime.evaluate", {
                "expression": "window.scrollBy(0, window.innerHeight * 1.5);"
            })
            time.sleep(wait_between)
    for item in evaluate_json(ws, counter, extract_js):
        all_results[item["room_id"]] = item
    return list(all_results.values())


def search_live(keyword, scroll_rounds=4, wait_between=2):
    """Search Douyin live tab and return list of live room dicts."""
    ws = websocket.create_connection(pick_tab(), timeout=60)
    counter = {"id": 1}

    cdp_send(ws, counter, "Page.enable")
    cdp_send(ws, counter, "Runtime.enable")

    search_url = f"https://www.douyin.com/search/{urllib.parse.quote(keyword)}?type=live"
    print(f"[info] searching: {search_url}", file=sys.stderr)
    cdp_send(ws, counter, "Page.navigate", {"url": search_url})

    # Wait for results to load
    print("[info] waiting for results to load...", file=sys.stderr)
    time.sleep(5)

    results = scroll_and_collect(ws, counter, DOUYIN_EXTRACT_JS, scroll_rounds, wait_between)
    ws.close()
    return results


def search_tiktok(keyword, scroll_rounds=4, wait_between=2, mode="live"):
    """Search TikTok live tab or user list (requires TikTok login in the debug browser)."""
    ws = websocket.create_connection(pick_tab(), timeout=90)
    counter = {"id": 1}

    cdp_send(ws, counter, "Page.enable")
    cdp_send(ws, counter, "Runtime.enable")

    tab = "live" if mode == "live" else "user"
    search_url = f"https://www.tiktok.com/search/{tab}?q={urllib.parse.quote(keyword)}"
    print(f"[info] searching: {search_url}", file=sys.stderr)
    cdp_send(ws, counter, "Page.navigate", {"url": search_url})
    print("[info] waiting for results to load...", file=sys.stderr)
    time.sleep(8)

    body = evaluate_json(
        ws, counter, "JSON.stringify((document.body.innerText || '').substring(0, 3000))")
    if TIKTOK_LOGIN_MARKERS.search(str(body)):
        ws.close()
        sys.exit("[fatal] TikTok 搜索页被登录墙拦截 — 请先在调试浏览器（端口 9222）"
                 "打开 tiktok.com 登录 TikTok，再重试")

    extract_js = TIKTOK_EXTRACT_JS if mode == "live" else TIKTOK_USER_EXTRACT_JS

    # Poll until results actually render (SPA lazy-loads; fixed sleep is unreliable)
    for _ in range(10):
        time.sleep(2)
        if evaluate_json(ws, counter, extract_js):
            break

    results = scroll_and_collect(ws, counter, extract_js, scroll_rounds, wait_between)
    ws.close()
    return results


def main():
    parser = argparse.ArgumentParser(description="Search Douyin/TikTok live rooms by keyword")
    parser.add_argument("keyword", help="account name or brand name to search")
    parser.add_argument("--platform", choices=["douyin", "tiktok"], default="douyin",
                        help="platform to search (default: douyin)")
    parser.add_argument("--mode", choices=["live", "users"], default="live",
                        help="tiktok: search live rooms or user accounts (matrix discovery)")
    parser.add_argument("--json", action="store_true", help="output JSON")
    parser.add_argument("--scroll", type=int, default=4,
                        help="number of scroll rounds to load more results (default 4)")
    args = parser.parse_args()

    if args.platform == "tiktok":
        results = search_tiktok(args.keyword, scroll_rounds=args.scroll, mode=args.mode)
    else:
        results = search_live(args.keyword, scroll_rounds=args.scroll)

    if not results:
        print("[info] no live rooms found for this keyword", file=sys.stderr)
        if args.json:
            print(json.dumps([], ensure_ascii=False))
        return

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    # Pretty print
    print(f"\n找到 {len(results)} 个在播直播间：\n")
    for i, r in enumerate(results, 1):
        badge = " [蓝V]" if args.platform == "douyin" and r.get("verified") else ""
        print(f"  {i}. {r['name']}{badge}")
        print(f"     room_id: {r['room_id']}")
        print(f"     标题: {r['title'] or '(未获取到)'}")
        print(f"     链接: {r['href']}")
        print()


if __name__ == "__main__":
    main()
