# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""What the Hub home's template cards and their preview show.

Tok Aba, 2026-10-22: the built-in templates sit on the Hub home as picture
cards, and a card opens a preview that says in plain words what the template
gives you (frontend/src/components/TemplateGallery.jsx). This module answers
what the listing (core/routes/hub.py _template_listing) adds per template:
its pictures and media, a summary of the build it carries, and the
developer's own words for the preview when they wrote some (copy_of).

Kept out of routes/hub.py, which is past its CI line ceiling already.
"""
import pathlib
import re

# A template's pictures are FILES, not a formats.toml field: the template
# update path (template_updates.entry_data) carries a fixed set of keys and
# would drop a new one on the next published fetch, and a picture is release
# content anyway. core/static/img/templates/<key>.png (or -1, -2 ... for a
# slider), any of png/jpg/webp; none = the card draws its placeholder.
IMG_DIR = pathlib.Path(__file__).resolve().parents[1] / "static" / "img" / "templates"
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def images(key, img_dir=None):
    """The /static URLs of a template's pictures, in slider order."""
    try:
        files = [p for p in pathlib.Path(img_dir or IMG_DIR).iterdir()
                 if p.is_file() and p.suffix.lower() in IMG_EXT]
    except OSError:
        return []
    out = []
    for p in files:
        stem = p.stem
        if stem == key:
            out.append((0, p.name))
        elif stem.startswith(key + "-") and stem[len(key) + 1:].isdigit():
            out.append((int(stem[len(key) + 1:]), p.name))
    return ["/static/img/templates/" + name for _, name in sorted(out)]


def media(key):
    """The preview's slides: the media uploaded on the developer hub and
    brought by the signed template update (template_media.py), else the
    release's own pictures above. [{url, kind, caption}], kind image|video."""
    from core.services import template_media                # noqa: PLC0415
    got = template_media.listing(key)
    return got or [{"url": u, "kind": "image", "caption": ""} for u in images(key)]


def summary(fmt):
    """What a template brings, in the plain facts the preview lists: the
    dashboard's pages and card titles, how many workflow steps come wired,
    and the tables it records to. Read from the build the template carries;
    a template without one answers empty lists and 0."""
    pages, cards = [], []
    for page in ((fmt.get("dashboard") or {}).get("pages") or []):
        if not isinstance(page, dict):
            continue
        pages.append(str(page.get("name") or ""))
        for w in page.get("widgets") or []:
            title = str((w or {}).get("title") or "").strip()
            if title and title not in cards:
                cards.append(title)
    steps = 0
    for wf in ((fmt.get("workflows") or {}).get("workflows") or []):
        steps += len(((wf or {}).get("graph") or {}).get("nodes") or [])
    tables = [str((t or {}).get("name") or (t or {}).get("key") or "")
              for t in (fmt.get("tables") or []) if isinstance(t, dict)]
    return {"pages": [p for p in pages if p], "cards": cards[:12],
            "steps": steps, "tables": [t for t in tables if t]}


# ---------------------------------------------------------------------------
# The developer's own words (Tok Aba, 2026-10-22, third round)
# ---------------------------------------------------------------------------
# A template may carry its own "What you get" lines (`features`), a short
# sentence under the title (`blurb`) and its own name for the list's heading
# (`heading`, "What you get" when unset). Stored IN the template's formats.toml
# entry, so they travel with it: Publish templates signs them with the rest,
# and a box's template update installs them -- and a change to them is a
# change to the template (template_updates._same compares the stored entry).
# Empty or absent = the automatic text above. Plain text only: control
# characters are removed and nothing is ever rendered as HTML.

COPY_KEYS = ("features", "blurb", "heading")
MAX_FEATURES = 8
FEATURE_MAX = 140
BLURB_MAX = 200
HEADING_MAX = 40


def _plain(text):
    return " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", str(text)).split())


def clean_copy(data):
    """{features?, blurb?, heading?} for a format entry -- only the ones that are set,
    so a template without its own words stores exactly what it did before.
    ValueError in the developer's words when a limit is broken."""
    out = {}
    features = data.get("features")
    if features:
        if not isinstance(features, list):
            raise ValueError("What you get must be a list of lines")
        if len(features) > MAX_FEATURES:
            raise ValueError(f"What you get can have at most {MAX_FEATURES} lines")
        lines = [_plain(f) for f in features]
        if any(not line for line in lines):
            raise ValueError("A line in What you get is empty - type something or remove it")
        if any(len(line) > FEATURE_MAX for line in lines):
            raise ValueError(f"Each line in What you get can be at most {FEATURE_MAX} characters")
        out["features"] = lines
    blurb = _plain(data.get("blurb") or "")
    if len(blurb) > BLURB_MAX:
        raise ValueError(f"The sentence under the title can be at most {BLURB_MAX} characters")
    if blurb:
        out["blurb"] = blurb
    heading = _plain(data.get("heading") or "")
    if len(heading) > HEADING_MAX:
        raise ValueError(f"The heading can be at most {HEADING_MAX} characters")
    if heading:
        out["heading"] = heading
    return out


def copy_of(key, config_dir=None):
    """{features, blurb, heading} as stored for `key` ([] and "" = automatic)."""
    from core.services import provision as provision_service  # noqa: PLC0415
    entry = provision_service._formats_overlay(config_dir).get(key) or {}
    return {"features": [str(f) for f in entry.get("features") or []],
            "blurb": str(entry.get("blurb") or ""),
            "heading": str(entry.get("heading") or "")}


def set_copy(key, features, blurb, config_dir=None, heading=""):
    """Write (or, with both empty, clear) a template's own words, everything
    else in its entry untouched. Returns copy_of(key)."""
    from core.services import provision as provision_service  # noqa: PLC0415
    clean = clean_copy({"features": features, "blurb": blurb, "heading": heading})
    with provision_service._FORMATS_LOCK:
        raw = provision_service.config.load(provision_service.FORMATS_FILE, config_dir) or {}
        formats = {k: dict(v) for k, v in (raw.get("formats") or {}).items()}
        if key not in formats:
            fmt = provision_service.builtin_formats(config_dir).get(key)
            if not fmt:
                raise ValueError("no such template")
            # A shipped template with no entry yet: saved whole, words added.
            provision_service.save_format(key, dict(fmt, **clean), config_dir)
            return copy_of(key, config_dir)
        for k in COPY_KEYS:
            formats[key].pop(k, None)
        formats[key].update(clean)
        provision_service._write_formats(formats, config_dir)
    return copy_of(key, config_dir)
