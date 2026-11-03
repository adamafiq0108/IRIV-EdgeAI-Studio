#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_models.py -- download Hailo Model Zoo HEFs for THIS device's accelerator.

HEFs are not committed: they are large, and a HEF compiled for hailo8 will not
load on hailo10h. So the catalogue (models.toml) records what exists and this
script fetches the right build for the accelerator actually plugged in.

    python systems/demo/scripts/fetch_models.py --list
    python systems/demo/scripts/fetch_models.py yolov8n
    python systems/demo/scripts/fetch_models.py --all --arch hailo8l

Files land in `systems/demo/models/<arch>/<file>.hef`, which is exactly where
app/hailo.py resolves them, plus `models/coco.txt` for the class names.

Nothing here imports HailoRT: you can pre-stage models on a laptop and copy the
folder to the Pi. `--arch` exists for that case; without it the architecture is
auto-detected (app/hailo.py detect_arch).
"""

import argparse
import os
import sys
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)                       # systems/demo
sys.path.insert(0, _PKG)

try:
    import tomllib                                   # py3.11+
except ModuleNotFoundError:                          # pragma: no cover
    print("python 3.11+ required (tomllib)", file=sys.stderr)
    raise SystemExit(2)

#: COCO class names for the zoo models; without them every detection is an
#: integer and the zone rules cannot filter by "person". WRITTEN, not
#: downloaded: the Model Zoo file they used to come from answers 404, and the
#: detector refuses to start when its labels file is missing. A copy of
#: core/services/models.py COCO_CLASSES (this script runs on a Pi with no core
#: on its path) -- tests/smoke_modelbootstrap.py keeps the two identical, and
#: the order is the class index, so it matters.
COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table",
    "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)
#: A real HEF is megabytes; anything under this is an error page or a cut-off
#: transfer and is thrown away (mirrors core/services/model_bootstrap.py).
MIN_HEF_BYTES = 1024 * 1024


def load_catalogue():
    path = os.path.join(_PKG, "models.toml")
    with open(path, "rb") as f:
        return tomllib.load(f)


def detect_arch():
    """Reuse the app's detection so this script and the runtime never disagree."""
    try:
        from app.hailo import detect_arch as _detect   # noqa: PLC0415
        return _detect()
    except Exception:                                  # noqa: BLE001
        return None


def download(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r  {os.path.basename(dest)} {pct:3d}%  "
                          f"{done >> 20}/{total >> 20} MiB", end="", flush=True)
        print()
        if dest.endswith(".hef") and os.path.getsize(tmp) < MIN_HEF_BYTES:
            print(f"  {os.path.basename(dest)} came back as "
                  f"{os.path.getsize(tmp)} bytes - not a model file",
                  file=sys.stderr)
            return False
        os.replace(tmp, dest)
        return True
    except urllib.error.HTTPError as e:
        print(f"\n  HTTP {e.code} for {url}", file=sys.stderr)
    except (urllib.error.URLError, OSError) as e:
        print(f"\n  {e}", file=sys.stderr)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return False


def main():
    cat = load_catalogue()
    models = cat.get("models") or {}
    zoo = cat.get("zoo") or {}

    p = argparse.ArgumentParser(description="Fetch Hailo HEFs for this system.")
    p.add_argument("name", nargs="*", help="model name(s) from models.toml")
    p.add_argument("--all", action="store_true", help="every detection model")
    p.add_argument("--list", action="store_true", help="show the catalogue and exit")
    p.add_argument("--arch", choices=("hailo8", "hailo8l", "hailo10h"),
                   help="override auto-detection (staging for another device)")
    p.add_argument("--models-dir", default=os.path.join(_PKG, "models"))
    args = p.parse_args()

    if args.list:
        arch = args.arch or detect_arch()
        print(f"detected arch: {arch or 'none (no accelerator on this host)'}\n")
        for name, m in sorted(models.items()):
            have = os.path.exists(os.path.join(args.models_dir, arch or "", m["file"])) if arch else False
            print(f"  {'[x]' if have else '[ ]'} {name:16s} {m['task']:14s} {m['description']}")
        print("\n[x] = already downloaded for this arch")
        return 0

    arch = args.arch or detect_arch()
    if not arch:
        print("No Hailo accelerator detected and no --arch given.\n"
              "Run on the device, or stage with e.g. --arch hailo8l.", file=sys.stderr)
        return 1

    ver = (zoo.get("vers") or {}).get(arch)
    if not ver:
        print(f"no zoo release recorded for arch {arch!r} in models.toml", file=sys.stderr)
        return 1

    wanted = list(args.name)
    if args.all:
        wanted = [n for n, m in models.items() if m.get("task") == "detection"]
    if not wanted:
        print("nothing to do: name a model, or use --all / --list", file=sys.stderr)
        return 1

    unknown = [n for n in wanted if n not in models]
    if unknown:
        print(f"unknown model(s): {', '.join(unknown)}\n"
              f"have: {', '.join(sorted(models))}", file=sys.stderr)
        return 1

    out_dir = os.path.join(args.models_dir, arch)
    print(f"arch {arch}, zoo {ver} -> {out_dir}")
    failed = []
    for name in wanted:
        m = models[name]
        dest = os.path.join(out_dir, m["file"])
        if os.path.exists(dest):
            print(f"  {m['file']} already present, skipping")
            continue
        url = zoo["base_url"].format(ver=ver, arch=arch, name=name)
        if not download(url, dest):
            failed.append(name)

    labels = os.path.join(args.models_dir, "coco.txt")
    if not os.path.exists(labels):
        print("  coco.txt (class names)")
        os.makedirs(args.models_dir, exist_ok=True)
        with open(labels, "w", encoding="utf-8") as f:
            f.write("\n".join(COCO_CLASSES) + "\n")

    if failed:
        print(f"\nfailed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"\nDone. Set [detector] kind = \"hailo\" and model = \"{wanted[0]}\" "
          f"in systems/demo/config.toml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
