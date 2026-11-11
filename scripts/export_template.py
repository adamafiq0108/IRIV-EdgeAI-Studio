#!/usr/bin/env python3
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
export_template.py -- write one template from a board's formats.toml as a file
for the shared templates folder (templates/<key>.json).

Build the template on a board (developer account, template editor), then:

    python3 scripts/export_template.py --list
    python3 scripts/export_template.py ai_smart_parking            # -> templates/ai_smart_parking.json
    python3 scripts/export_template.py ai_smart_parking --out /tmp/

Put the file in the repo's templates/ folder, commit and push: every board
shows it as a built-in template after its next software update
(core/services/provision.py, SHARED_TEMPLATES_DIR).

Reads /opt/irivai-systor/config/formats.toml by default (an installed board),
or --formats PATH. Draft templates are exported as drafts; add --publish to
export one as a normal (visible) template.
"""

import argparse
import json
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_FORMATS = pathlib.Path("/opt/irivai-systor/config/formats.toml")


def main():
    ap = argparse.ArgumentParser(description="Export a template to templates/<key>.json")
    ap.add_argument("key", nargs="?", help="the template key, e.g. ai_smart_parking")
    ap.add_argument("--formats", type=pathlib.Path,
                    default=DEFAULT_FORMATS if DEFAULT_FORMATS.is_file()
                    else ROOT / "config" / "formats.toml",
                    help="the formats.toml to read (default: this board's)")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "templates",
                    help="folder (or .json file) to write (default: templates/)")
    ap.add_argument("--list", action="store_true", help="list the templates and exit")
    ap.add_argument("--publish", action="store_true",
                    help="export a draft as a visible template")
    args = ap.parse_args()

    try:
        formats = tomllib.loads(args.formats.read_text(encoding="utf-8")).get("formats") or {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.exit(f"cannot read {args.formats}: {exc}")

    if args.list or not args.key:
        for key, entry in sorted(formats.items()):
            if isinstance(entry, dict):
                flag = " (draft)" if entry.get("draft") else ""
                print(f"{key:28} {entry.get('label', '')}{flag}")
        return
    entry = formats.get(args.key)
    if not isinstance(entry, dict):
        sys.exit(f"no template '{args.key}' in {args.formats} - try --list")
    entry = dict(entry)
    entry.pop("hidden", None)
    if args.publish:
        entry.pop("draft", None)

    out = args.out
    if out.suffix.lower() != ".json":
        out.mkdir(parents=True, exist_ok=True)
        out = out / f"{args.key}.json"
    out.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}" + ("  (draft: not shown to users; use --publish)" if entry.get("draft") else ""))


if __name__ == "__main__":
    main()
