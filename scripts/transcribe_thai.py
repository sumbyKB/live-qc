#!/usr/bin/env python3
"""Local Whisper ASR for livestream recordings — fallback for languages the
Lark Minutes ASR can't handle (Thai, Tagalog, etc.).

First run self-installs everything (per skill design: users install nothing
by hand):
    1. pip install faster-whisper        (~150MB deps)
    2. model download on first use       (turbo ~1.6GB / large-v3 ~3GB, cached
       in ~/.cache/huggingface, one-time). If HuggingFace is unreachable
       (mainland networks), retries once via the hf-mirror.com endpoint.

Usage:
    python3 transcribe_thai.py video.mp4                    # -> video.txt
    python3 transcribe_thai.py video.mp4 --language th      # force language
    python3 transcribe_thai.py video.mp4 --model large-v3   # max accuracy

# ponytail: default is the turbo distilled model — 4-8x faster, Thai quality
# slightly below large-v3; pass --model large-v3 when a transcript looks lossy.
"""
import argparse
import subprocess
import sys
import os

DEFAULT_MODEL = "deepdml/faster-whisper-large-v3-turbo-ct2"


def ensure_faster_whisper():
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        print("[info] first run: pip-installing faster-whisper (~150MB)...",
              file=sys.stderr)
        subprocess.run([sys.executable, "-m", "pip", "install", "faster-whisper"],
                       check=True)


def load_model(name):
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(name, device="auto", compute_type="auto")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] model download failed ({exc}); retrying via hf-mirror.com",
              file=sys.stderr)
        # huggingface_hub reads HF_ENDPOINT at import time, so the mirror only
        # applies in a fresh interpreter; cache there, then load from cache
        code = ("from faster_whisper import WhisperModel; "
                f"WhisperModel({name!r}, device='auto', compute_type='auto'); "
                "print('model cached')")
        # HF_HUB_DISABLE_XET: hf-mirror can't proxy the Xet CAS CDN (401),
        # force plain HTTP download
        env = dict(os.environ, HF_ENDPOINT="https://hf-mirror.com",
                   HF_HUB_DISABLE_XET="1")
        if subprocess.run([sys.executable, "-c", code], env=env).returncode != 0:
            sys.exit("[fatal] model download failed twice — check network/proxy, "
                     "or set HF_ENDPOINT manually")
        return WhisperModel(name, device="auto", compute_type="auto")


def fmt(sec):
    ms = int(round(sec * 1000))
    h, rest = divmod(ms, 3600_000)
    m, rest = divmod(rest, 60_000)
    s = rest / 1000
    return f"{h:d}:{m:02d}:{s:06.3f}" if h else f"{m:02d}:{s:06.3f}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video", help="mp4/audio path")
    parser.add_argument("--output", help="transcript path (default: <video>.txt)")
    parser.add_argument("--language", help="force language code, e.g. th (default auto)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="HF model id or faster-whisper alias")
    args = parser.parse_args()

    if not os.path.exists(args.video):
        sys.exit(f"[fatal] no such file: {args.video}")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ensure_faster_whisper()
    print(f"[info] loading model {args.model} (first run downloads it, one-time)",
          file=sys.stderr)
    model = load_model(args.model)

    print("[info] transcribing...", file=sys.stderr)
    segments, info = model.transcribe(args.video, language=args.language,
                                      vad_filter=True)
    print(f"[info] detected language: {info.language} "
          f"(probability {info.language_probability:.2f})", file=sys.stderr)

    out = args.output or os.path.splitext(args.video)[0] + ".txt"
    n = 0
    with open(out, "w", encoding="utf-8") as fh:
        for seg in segments:
            text = seg.text.strip()
            if text:
                fh.write(f"Speaker 1 {fmt(seg.start)}\n{text}\n\n")
                n += 1
    print(f"[ok] {n} segments -> {out}", file=sys.stderr)
    if n == 0:
        sys.exit("[fatal] no speech recognized — check the recording has speech")


if __name__ == "__main__":
    main()
