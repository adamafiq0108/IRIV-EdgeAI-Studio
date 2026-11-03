# The Text card and the Image card (Tok Aba, 2026-10-14): two static
# Builder cards that read no data. services/builder.py validates and renders
# them; services/builder_assets.py stores, sniffs, serves and cleans up the
# uploaded pictures; routes/builder.py exposes upload and serve.
#
#   1. validate: the new kinds are accepted with no key; every field cleaned
#      (text cut and stripped of control characters, choices whitelisted, a
#      picture by its 32-hex asset id only); an older widget gains no field
#   2. compute: drawn from their own fields, no data read, never AI-off
#   3. upload matrix: PNG / JPEG / WebP / GIF accepted by their BYTES; SVG,
#      HTML renamed .png, BMP, PDF, empty, oversize, over 4096 px refused; a
#      polyglot (PNG bytes, HTML after) is stored and served as image/png only
#   4. paths: the slug and the asset id are checked, so nothing outside the
#      system's own folder is reachable; the file name is generated
#   5. cleanup: a picture the saved layout stops naming is deleted; orphans
#      are swept once older than the grace, fresh uploads are kept; bounded
#   6. a deleted system takes its pictures with it
#   7. the routes: gated like the layout save, serve with safe headers
#      (exercised through FastAPI when it is installed, else read off source)
#
#   py -3 tests/smoke_builderstatic.py
import os
import struct
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
STORE = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = STORE

from core.services import builder as builder_service             # noqa: E402
from core.services import builder_assets as assets               # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- sample pictures, built byte by byte ------------------------------------------
def png(w=10, h=8, tail=b""):
    ihdr = struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\0\0\0\0"
            + b"\0\0\0\0IEND\xaeB`\x82" + tail)


def jpeg(w=10, h=8):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHH", 11, 8, h, w) + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def webp(w=10, h=8):
    vp8x = b"VP8X" + struct.pack("<I", 10) + b"\0\0\0\0" + (w - 1).to_bytes(3, "little") \
        + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(vp8x)) + b"WEBP" + vp8x


def gif(w=10, h=8):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00\x00\x00;"


# -- 1. validate ---------------------------------------------------------------------
AID = "0123456789abcdef0123456789abcdef"
out = builder_service.validate([
    {"id": "t1", "kind": "text_card", "body": "Line one\r\n<script>alert(1)</script>\x00\x1b"
     + "x" * 3000, "text_size": "huge", "align": "justify", "bold": "yes"},
    {"id": "i1", "kind": "image_card", "asset": AID, "alt": "  the  line  ", "fit": "stretch",
     "caption": "c" * 300},
    {"id": "i2", "kind": "image_card", "asset": "../../etc/passwd"},
    {"id": "i3", "kind": "image_card", "asset": "https://example.com/x.png"},
])
t, i, i2, i3 = out
check("a Text card needs no key and is accepted", t["kind"] == "text_card" and t["key"] == "")
check("...its words keep line breaks, lose control characters, stop at 2000",
      t["body"].startswith("Line one\n<script>alert(1)</script>") and "\x00" not in t["body"]
      and "\x1b" not in t["body"] and "\r" not in t["body"] and len(t["body"]) == 2000)
check("...size, alignment and bold from their whitelists",
      t["text_size"] == "medium" and t["align"] == "left" and t["bold"] is True)
check("...and no valign unless one is set (an old Text card stays at the top)", "valign" not in t)
tv = builder_service.validate([{"id": "v1", "kind": "text_card", "body": "x", "valign": "bottom"},
                               {"id": "v2", "kind": "text_card", "body": "x", "valign": "lower"}])
check("a Text card keeps Above / Middle / Below, refuses anything else",
      tv[0]["valign"] == "bottom" and "valign" not in tv[1])
check("an Image card keeps a 32-hex asset id, its description squeezed, fit whitelisted",
      i["asset"] == AID and i["alt"] == "the line" and i["fit"] == "contain"
      and len(i["caption"]) == 120)
check("...a path or a web address is never kept as the picture",
      i2["asset"] == "" and i3["asset"] == "")
named = builder_service.validate([{"id": "n1", "kind": "text_card", "title": "Shift notes",
                                   "body": "x"}])[0]
check("the card keeps its title as the name the Builder lists it by (never drawn on it)",
      named["title"] == "Shift notes" and "heading" not in named)
old = builder_service.validate([{"id": "k", "kind": "tile", "source": "stat", "key": "fn_x"}])[0]
check("an older widget gains none of the new fields",
      not any(f in old for f in ("body", "text_size", "align", "bold", "asset", "alt", "fit", "caption")))

# -- 2. compute ----------------------------------------------------------------------
rows = builder_service.compute(out[:2], [], {}, slug="smoke")
check("compute draws both from their own fields, no data read",
      rows[0]["data"] == {"static": True} and rows[0]["body"].startswith("Line one")
      and rows[1]["asset"] == AID and rows[1]["data"] == {"static": True})
check("...and nothing marks them AI-off or paused",
      builder_service.STATIC_KINDS == ("text_card", "image_card")
      and all(not (r["data"] or {}).get("ai_off") for r in rows))

# -- 3. the upload matrix --------------------------------------------------------------
for name, data, kind in (("PNG", png(), "png"), ("JPEG", jpeg(), "jpeg"),
                         ("WebP", webp(), "webp"), ("GIF", gif(), "gif")):
    try:
        got = assets.sniff(data)
        check(f"{name} accepted by its bytes, size read from its header",
              got == (kind, 10, 8), got)
    except ValueError as e:
        check(f"{name} accepted by its bytes", False, e)
REFUSE = {
    "SVG": b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>',
    "HTML renamed .png": b"<!doctype html><html><script>alert(1)</script></html>",
    "BMP": b"BM" + b"\0" * 60,
    "PDF": b"%PDF-1.7\n" + b"\0" * 40,
    "a PNG claiming 5000 px": png(5000, 10),
    "a truncated PNG": b"\x89PNG\r\n\x1a\n\0\0",
}
for name, data in REFUSE.items():
    try:
        assets.sniff(data)
        check(f"{name} refused", False, "accepted")
    except ValueError:
        check(f"{name} refused", True)
for name, data, why in (("empty", b"", "empty"), ("oversize", png() + b"\0" * assets.MAX_BYTES, "2 MB")):
    try:
        assets.save("smoke", data)
        check(f"{name} refused by save", False, "accepted")
    except ValueError as e:
        check(f"{name} refused by save", why in str(e), e)
poly = png(tail=b"<html><script>alert(1)</script></html>")
saved = assets.save("smoke", poly)
served = assets.read("smoke", saved["id"])
check("a polyglot (PNG, then HTML) is stored and served as image/png only",
      served is not None and served[1] == "image/png" and saved["type"] == "image/png")

# -- 4. paths ---------------------------------------------------------------------------
folder = os.path.join(STORE, "builder_assets", "smoke")
names = os.listdir(folder)
check("stored under <store>/builder_assets/<slug>/<32 hex>.<ext>, the name generated",
      names == [f"{saved['id']}.png"] and assets.is_asset_id(saved["id"]), names)
for slug in ("../x", "a/b", "", "a" * 80):
    try:
        assets.asset_dir(slug)
        check(f"slug {slug!r} refused", False)
    except ValueError:
        check(f"slug {slug!r} refused", True)
check("an id that is not 32 hex reads nothing (no path walking)",
      assets.read("smoke", "../../" + saved["id"]) is None
      and assets.read("smoke", saved["id"] + ".png") is None
      and assets.read("smoke", saved["id"].upper()) is None)

# -- 5. cleanup -------------------------------------------------------------------------
a = assets.save("smoke", png())["id"]
b = assets.save("smoke", gif())["id"]
lay = lambda *ids: {"pages": [{"id": "main", "widgets": [
    {"id": f"i{n}", "kind": "image_card", "asset": x} for n, x in enumerate(ids)]}]}
gone = assets.after_save("smoke", lay(a, b), lay(b))
check("a picture the saved layout stops naming is deleted at once",
      a in gone and assets.read("smoke", a) is None and assets.read("smoke", b) is not None)
fresh = assets.save("smoke", webp())["id"]
gone = assets.after_save("smoke", lay(b), lay(b))
check("a fresh upload nothing names yet is kept (the grace)",
      fresh not in gone and assets.read("smoke", fresh) is not None)
past = time.time() - assets.ORPHAN_GRACE_S - 10
for aid in (fresh, saved["id"]):
    for ext in ("png", "webp"):
        pth = os.path.join(folder, f"{aid}.{ext}")
        if os.path.exists(pth):
            os.utime(pth, (past, past))
open(os.path.join(folder, "deadbeef.part"), "wb").write(b"x")
os.utime(os.path.join(folder, "deadbeef.part"), (past, past))
gone = assets.after_save("smoke", lay(b), lay(b))
check("...and swept once older than the grace, a stale .part too; the named one stays",
      fresh in gone and saved["id"] in gone and "deadbeef" in gone
      and assets.read("smoke", b) is not None, gone)
check("the sweep is bounded", assets.SWEEP_LIMIT > 0)
# Through the real layout save.
c = assets.save("smoke", jpeg())["id"]
builder_service.save(None, "smoke", {"pages": [{"id": "main", "widgets": [
    {"id": "pic", "kind": "image_card", "asset": c}]}]})
check("builder.save keeps the picture the layout names", assets.read("smoke", c) is not None)
builder_service.save(None, "smoke", {"pages": [{"id": "main", "widgets": [
    {"id": "words", "kind": "text_card", "body": "hello"}]}]})
check("...and deletes it when the card is removed", assets.read("smoke", c) is None)

# -- 6. a deleted system ------------------------------------------------------------------
assets.save("gone", png())
from core.services import provision                                 # noqa: E402
left = provision._remove_documents("gone")
check("a deleted system takes its pictures with it",
      "builder_assets" in left and not os.path.isdir(os.path.join(STORE, "builder_assets", "gone")))

# -- 7. the routes ------------------------------------------------------------------------
src = open(os.path.join(ROOT, "core", "routes", "builder.py"), encoding="utf-8").read()
up = src.split('def upload_asset(')[1].split("@router")[0]
serve = src.split('def get_asset(')[1].split("@router")[0]
check("upload: same gate as saving the layout (main.edit)", 'perm.require(request, "main.edit")' in up)
check("upload: counted while it streams, refused past the limit",
      "request.stream()" in up and "status_code=413" in up)
check("upload: no file name is read from the request",
      "filename" not in up and "form(" not in up)
check("serve: only by asset id, under the sniffed type, nothing may run",
      "is_asset_id(asset_id)" in serve and '"X-Content-Type-Options": "nosniff"' in serve
      and "default-src 'none'" in serve and "sandbox" in serve)
try:
    import fastapi  # noqa: F401
    # TestClient needs httpx, which is not a product requirement: the release
    # CI installs requirements.txt only, so without it this part is a skip.
    from fastapi.testclient import TestClient as _TC  # noqa: F401
    have_fastapi = True
except (ImportError, RuntimeError):
    have_fastapi = False
if not have_fastapi:
    print("skip: fastapi or its test client (httpx) is not installed here -- the session wall, "
          "the main.edit grant, the wrong-system refusal and the live headers were checked off "
          "the source only")
else:
    from fastapi import FastAPI                                     # noqa: E402
    from fastapi.testclient import TestClient                       # noqa: E402
    from core.routes import builder as builder_routes               # noqa: E402
    from core.routes import perm                                    # noqa: E402

    app = FastAPI()
    app.include_router(builder_routes.router)
    app.state.system = type("S", (), {"slug": "smoke"})()
    app.state.config_dir = None
    role = {"ok": True}

    def fake_require(request, cap):
        if not role["ok"]:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="no")
    perm.require = fake_require
    client = TestClient(app)
    r = client.post("/api/builder/assets", content=png())
    check("route: an editor uploads a PNG", r.status_code == 200 and r.json()["type"] == "image/png", r.text)
    got = client.get(f"/api/builder/assets/{r.json()['id']}")
    check("route: served with nosniff and a CSP that runs nothing",
          got.headers.get("x-content-type-options") == "nosniff"
          and "default-src 'none'" in got.headers.get("content-security-policy", ""))
    check("route: SVG refused", client.post("/api/builder/assets", content=REFUSE["SVG"]).status_code == 400)
    check("route: oversize refused while streaming",
          client.post("/api/builder/assets", content=png() + b"\0" * assets.MAX_BYTES).status_code == 413)
    role["ok"] = False
    check("route: no main.edit grant, no upload",
          client.post("/api/builder/assets", content=png()).status_code == 403)
    check("route: a path-like id is a 404",
          client.get("/api/builder/assets/..%2F..%2Fetc").status_code == 404)

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
