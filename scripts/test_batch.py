#!/usr/bin/env python3
"""Self-check for batch_record.py / qc_summary.py pure logic. Run: python3 test_batch.py"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import batch_record  # noqa: E402
import qc_summary  # noqa: E402

tmp = tempfile.mkdtemp()

# --- batch_record: CSV parsing (comments, category col, duplicate rooms) ---
csv_path = os.path.join(tmp, "rooms.csv")
with open(csv_path, "w", encoding="utf-8") as fh:
    fh.write("# comment\n123,某品牌官方旗舰店,apparel\n123,dupe\n@handle.x,TH店\n")
rooms = batch_record.load_rooms(csv_path)
assert [r["room"] for r in rooms] == ["123", "@handle.x"], rooms
assert rooms[0]["category"] == "apparel" and rooms[1]["category"] == "general"
assert batch_record.safe_name('店 A/"旗舰"!') == "店_A_旗舰", batch_record.safe_name('店 A/"旗舰"!')

# --- qc_summary: end-to-end on fixtures ---
rec_dir = os.path.join(tmp, "rec"); scans_dir = os.path.join(tmp, "scans")
os.makedirs(rec_dir); os.makedirs(scans_dir)
manifest = [
    {"room": "1", "name": "店A", "category": "apparel", "status": "ok", "mp4": "店A_1_180s.mp4",
     "duration_sec": 180.0, "error": None},
    {"room": "2", "name": "店B", "category": "general", "status": "ok", "mp4": "店B_2_180s.mp4",
     "duration_sec": 180.0, "error": None},
    {"room": "3", "name": "店C", "category": "food", "status": "fail", "mp4": None,
     "duration_sec": None, "error": "no FLV stream found"},
    {"room": "4", "name": "店D", "category": "general", "status": "ok", "mp4": "店D_4_180s.mp4",
     "duration_sec": 180.0, "error": None},
]
mp = os.path.join(rec_dir, "manifest.json")
json.dump(manifest, open(mp, "w", encoding="utf-8"), ensure_ascii=False)
# A: 2 high + 1 mid = 23 -> need fix; B: same high phrase as A (common) + 1 low = 11
json.dump({"violations": [
    {"rule_id": "A01", "level": "high", "label": "全网最低价断言", "matched": "全网最低价"},
    {"rule_id": "A02", "level": "high", "label": "顶级绝对化用语", "matched": "顶级"},
    {"rule_id": "A05", "level": "mid", "label": "百字绝对化", "matched": "百搭百配"}],
    "sop": [{"item": "开场", "covered": True}, {"item": "收尾", "covered": False}]},
    open(os.path.join(scans_dir, "店A_1_180s.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"violations": [
    {"rule_id": "A01", "level": "high", "label": "全网最低价断言", "matched": "全网最低价"},
    {"rule_id": "B01", "level": "low", "label": "紧迫感话术", "matched": "最后三分钟"}],
    "sop": []},
    open(os.path.join(scans_dir, "店B_2_180s.json"), "w", encoding="utf-8"), ensure_ascii=False)
# D has no scan file -> no_scan

out = subprocess.run([sys.executable, os.path.join(HERE, "qc_summary.py"),
                      "--manifest", mp, "--scans", scans_dir],
                     capture_output=True, text=True, encoding="utf-8")
assert out.returncode == 0, out.stderr
md = open(os.path.join(rec_dir, "qc_summary.md"), encoding="utf-8").read()
data = json.load(open(os.path.join(rec_dir, "qc_summary.json"), encoding="utf-8"))

names = [r["name"] for r in data["rooms"]]
assert names == ["店A", "店B", "店D", "店C"], names          # sorted by score desc, failures last
assert data["rooms"][0]["score"] == 23 and data["rooms"][1]["score"] == 11
assert "立即整改" in md.splitlines()[4]                      # only 店A flagged
assert "无扫描结果" in md and "录制失败" in md
assert len(data["common"]) == 1 and data["common"][0]["rooms"] == ["店A", "店B"]
assert "疑似统一话术模板" in md and "店A、店B" in md

print("ok: csv parse, dedupe, safe_name, ranking, need-fix flag, no_scan, common violations")
