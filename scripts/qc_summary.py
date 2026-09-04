#!/usr/bin/env python3
"""Merge per-room rule scans into one comparison view (batch QC reports).

Input:  manifest.json from batch_record.py, plus one scan_violations.py --json
        file per recorded room, saved as <scans_dir>/<mp4_basename>.json
Output: qc_summary.md + qc_summary.json next to the manifest — per-room
        violation counts and risk score sorted worst-first, immediate-fix
        shortlist, and cross-room identical violations (uniform script-template
        suspects). The LLM turns this into the final report; this script only
        does the mechanical merge so 35 rooms don't get hand-sorted.

Usage:
    python3 qc_summary.py --manifest qc_runs/run1/rec/manifest.json --scans qc_runs/run1/scans
"""
import argparse
import json
import os
import sys
import time

WEIGHTS = {"high": 10, "mid": 3, "low": 1}
# ponytail: fixed threshold; tune per brand only if the flag stops matching judgement
NEED_FIX_SCORE = 15
STATUS_RANK = {"ok": 0, "no_scan": 1, "fail": 2}  # usable rooms first, broken last


def load_scan(scans_dir, mp4):
    base = os.path.splitext(os.path.basename(mp4))[0]
    path = os.path.join(scans_dir, base + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def summarize(entries, scans_dir):
    rows, common = [], {}
    for e in entries:
        row = {"room": e["room"], "name": e["name"],
               "category": e.get("category", "general"), "status": e["status"],
               "error": e.get("error"), "mp4": e.get("mp4"),
               "high": 0, "mid": 0, "low": 0, "missing_sop": 0, "score": 0}
        if e["status"] == "ok":
            scan = load_scan(scans_dir, e["mp4"])
            if scan is None:
                row["status"] = "no_scan"
            else:
                for v in scan.get("violations", []):
                    if v["level"] in WEIGHTS:
                        row[v["level"]] += 1
                        key = (v["rule_id"], v["label"], v["matched"])
                        common.setdefault(key, []).append(e["name"])
                    # ponytail: unknown level still counts, as a low
                    else:
                        row["low"] += 1
                row["missing_sop"] = sum(1 for s in scan.get("sop", []) if not s["covered"])
                row["score"] = sum(WEIGHTS[k] * row[k] for k in WEIGHTS)
        rows.append(row)
    rows.sort(key=lambda r: (-r["score"], STATUS_RANK.get(r["status"], 3), r["name"]))
    shared = [{"rule_id": k[0], "label": k[1], "matched": k[2], "rooms": v}
              for k, v in common.items() if len(v) >= 2]
    shared.sort(key=lambda s: -len(s["rooms"]))
    return rows, shared


def to_markdown(rows, shared):
    lines = [f"# 批量质检汇总（{len(rows)} 间）", "",
             "| # | 账号 | 类目 | 高 | 中 | 低 | SOP未命中 | 风险分 | 处置 |",
             "|---|------|------|---|---|---|-----------|--------|------|"]
    for i, r in enumerate(rows, 1):
        if r["status"] == "ok":
            action = "⚠️ 立即整改" if r["score"] >= NEED_FIX_SCORE else ""
        elif r["status"] == "no_scan":
            action = "无扫描结果"
        else:
            action = f"录制失败：{r['error']}"
        lines.append(f"| {i} | {r['name']} | {r['category']} | {r['high']} | "
                     f"{r['mid']} | {r['low']} | {r['missing_sop']} | "
                     f"{r['score']} | {action} |")
    if shared:
        lines += ["", "## 跨账号共性问题（疑似统一话术模板，建议模板层清理）"]
        for s in shared:
            lines.append(f"- [{s['rule_id']}] {s['label']}：「{s['matched']}」"
                         f" × {len(s['rooms'])} 店（{'、'.join(s['rooms'])}）")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="batch_record.py manifest.json")
    parser.add_argument("--scans", required=True, help="dir of <mp4_basename>.json scan files")
    args = parser.parse_args()

    # background/piped runs on Windows default to GBK; Thai handles would crash it
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with open(args.manifest, encoding="utf-8") as fh:
        entries = json.load(fh)
    rows, shared = summarize(entries, args.scans)

    out_md = os.path.join(os.path.dirname(os.path.abspath(args.manifest)), "qc_summary.md")
    out_json = out_md.replace(".md", ".json")
    payload = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "need_fix_score": NEED_FIX_SCORE, "rooms": rows, "common": shared}
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(to_markdown(rows, shared))
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    fix = sum(1 for r in rows if r["status"] == "ok" and r["score"] >= NEED_FIX_SCORE)
    print(f"[summary] {len(rows)} 间 | 需立即整改 {fix} | 跨账号共性问题 {len(shared)} 个"
          f" -> {out_md}")


if __name__ == "__main__":
    main()
