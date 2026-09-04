#!/usr/bin/env python3
"""Record several live rooms in parallel; one run directory holds everything.

Run directory layout (qc_runs/<date>_<brand>/):
    rooms.csv      input: room_or_handle,account_name[,category]
                   (same CSV probe_live.py --file eats; LIVE rooms only)
    rec/           <name>_<room>_<D>s.mp4 files + manifest.json
    manifest.json  per-room {room, name, category, status, mp4, duration_sec,
                   error} — the pipeline spine: transcribe/scan/report steps
                   read it, possibly in later sessions.
                   Resumable: rooms already "ok" are skipped on rerun.

Usage:
    python3 batch_record.py --file rooms.csv --duration 180 --outdir qc_runs/run1/rec

One record_live.py subprocess per room. TikTok rooms (yt-dlp path) never touch
the browser; Douyin rooms each get their own debug-browser tab (record_live.py
creates one per run).

# ponytail: workers=4 because all Douyin runs share one debug browser; raise
# only if a brand has more rooms than patience and the browser stays stable.
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECORD = os.path.join(HERE, "record_live.py")


def load_rooms(path):
    rooms, seen = [], set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if parts[0] in seen:  # duplicate room -> same output file, would collide
                continue
            seen.add(parts[0])
            rooms.append({"room": parts[0],
                          "name": parts[1] if len(parts) > 1 else parts[0],
                          "category": parts[2] if len(parts) > 2 else "general"})
    return rooms


def safe_name(s):
    """Filename-safe (\\w keeps Chinese in py3), readable."""
    return re.sub(r"[^\w.\-]+", "_", s)[:40].strip("_") or "room"


def ffprobe_duration(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return round(float(out), 1)
    except Exception:  # noqa: BLE001
        return None


def record_one(item, duration, outdir):
    entry = dict(item, status="fail", mp4=None, duration_sec=None, error=None)
    out = os.path.join(outdir, f"{safe_name(item['name'])}_{item['room']}_{duration}s.mp4")
    entry["mp4"] = out
    try:
        # +400s covers CDP wait (22s) and worst-case yt-dlp retries (2x150s)
        proc = subprocess.run(
            [sys.executable, RECORD, "--room", item["room"],
             "--duration", str(duration), "--output", out],
            capture_output=True, text=True, timeout=duration + 400)
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()
            entry["error"] = tail[-1][:200] if tail else f"rc={proc.returncode}"
            return entry
    except subprocess.TimeoutExpired:
        entry["error"] = "timeout"
        return entry
    if not os.path.exists(out):
        entry["error"] = "no output file"
        return entry
    entry["status"] = "ok"
    entry["duration_sec"] = ffprobe_duration(out)
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True,
                        help="CSV: room_or_handle,account_name[,category]")
    parser.add_argument("--duration", type=int, default=180, help="seconds per room")
    parser.add_argument("--outdir", required=True, help="run rec/ directory")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    rooms = load_rooms(args.file)
    if not rooms:
        parser.error("no rooms in --file")
    os.makedirs(args.outdir, exist_ok=True)
    # background/piped runs on Windows default to GBK; Thai handles would crash it
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    manifest_path = os.path.join(args.outdir, "manifest.json")
    done = {}
    if os.path.exists(manifest_path):  # resume: keep ok entries, retry the rest
        with open(manifest_path, encoding="utf-8") as fh:
            done = {e["room"]: e for e in json.load(fh) if e.get("status") == "ok"}
    pending = [r for r in rooms if r["room"] not in done]
    print(f"[info] {len(rooms)} rooms, {len(done)} already ok, "
          f"{len(pending)} to record", file=sys.stderr)

    entries = list(done.values())
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(record_one, r, args.duration, args.outdir)
                   for r in pending]
        for fut in concurrent.futures.as_completed(futures):
            entry = fut.result()
            entries.append(entry)
            print(f"[{entry['status']}] {entry['name']} ({entry['room']})"
                  + ("" if entry["status"] == "ok" else f" {entry['error']}"),
                  file=sys.stderr)
            with open(manifest_path, "w", encoding="utf-8") as fh:  # crash-safe progress
                json.dump(entries, fh, ensure_ascii=False, indent=2)

    ok = sum(1 for e in entries if e["status"] == "ok")
    print(f"[summary] {ok}/{len(rooms)} recorded -> {manifest_path}", file=sys.stderr)
    sys.exit(0 if ok == len(rooms) else 1)


if __name__ == "__main__":
    main()
