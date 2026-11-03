# -*- coding: utf-8 -*-
"""
Template preview media (Tok Aba, 2026-10-22, phase 2): what the developer
hub's "Preview media" editor stores, what "Publish templates" sends, and what
a box accepts from the signed list.

  1. uploads: the kind is read from the bytes (a PNG named .svg is refused by
     its name, SVG bytes named .png by their bytes), 5 MB a file, names made
     from the content hash, a caption cleaned to one plain line
  2. THE RULE: 5 slides with 1 video is fine; a second video is refused, a
     sixth slide is refused, both in plain words
  3. reorder, delete, and a path that is not a listed file is never served
  4. publish: files, then the list, then its signature; the list names every
     file with sha256, size, kind, order and caption, and verifies
  5. a box takes only what the signed list names: a good list lands; a
     tampered file, an over-size file, a list breaking the rule, a traversal
     name or a bad signature changes nothing and never raises
  6. routes: developer-only writes, non-developer refused, read with the
     validated type and nosniff (skipped where fastapi/httpx are missing)

    python tests/smoke_templatemedia.py
"""

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                          # noqa: E402
from core.services import ghnet                                  # noqa: E402
from core.services import provision                              # noqa: E402
from core.services import source_publish                         # noqa: E402
from core.services import template_media as tm                   # noqa: E402
from core.services import template_media_sync as sync            # noqa: E402

from cryptography.hazmat.primitives import serialization          # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey)

bad = 0


def check(msg, passed, detail=""):
    global bad
    if passed:
        print("ok   " + msg)
    else:
        print("FAIL " + msg + ("\n      " + str(detail) if detail else ""))
        bad += 1


def refused(fn, *a, want=""):
    try:
        fn(*a)
    except tm.MediaError as exc:
        return want in str(exc)
    return False


def png(n=0):
    return b"\x89PNG\r\n\x1a\n" + bytes([n]) * 64


def jpg(n=0):
    return b"\xff\xd8\xff\xe0" + bytes([n]) * 64


def gif(n=0):
    return b"GIF89a" + bytes([n]) * 64


def webp(n=0):
    return b"RIFF\x00\x00\x00\x00WEBPVP8 " + bytes([n]) * 64


def mp4(n=0):
    return b"\x00\x00\x00\x18ftypisom" + bytes([n]) * 64


def webm(n=0):
    return b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm" + bytes([n]) * 64


SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

_priv = Ed25519PrivateKey.generate()
PUB = base64.b64encode(_priv.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
CFG = tempfile.mkdtemp()
with io.open(os.path.join(CFG, "store.toml"), "w", encoding="utf-8") as f:
    f.write('[signing]\npubkey = "%s"\n' % PUB)
with open(os.path.join(CFG, "store_signing.pem"), "wb") as f:
    f.write(_priv.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()))
config.load.cache_clear()
provision.save_format("alpha", {"label": "Alpha", "widgets": [], "draft": False}, CFG)

# ---- 1. uploads -------------------------------------------------------------------
lst = tm.add("alpha", png(1), "  Line one\n\tand\x00 two  ", "front.png")
check("a PNG is added, named from its hash", len(lst) == 1
      and lst[0]["file"] == hashlib.sha256(png(1)).hexdigest()[:16] + ".png"
      and lst[0]["kind"] == "image" and lst[0]["type"] == "image/png", lst)
check("...its caption is one plain line", lst[0]["caption"] == "Line one and two", lst[0])
check("SVG bytes are refused, whatever the name", refused(tm.add, "alpha", SVG, "", "x.png",
                                                         want="Only JPEG, PNG"))
check("a .svg name is refused, whatever the bytes", refused(tm.add, "alpha", png(2), "", "x.svg",
                                                           want="Only JPEG, PNG"))
check("unknown bytes are refused", refused(tm.add, "alpha", b"hello world" * 10, "", "x.jpg"))
check("over 5 MB is refused", refused(tm.add, "alpha", png(3) + b"\0" * tm.MAX_BYTES, "", "",
                                      want="larger than 5 MB"))
check("the same file twice is refused", refused(tm.add, "alpha", png(1), "", "",
                                                want="already one of"))
check("a bad template key is refused", refused(tm.add, "../alpha", png(4), "", ""))
check("the kinds: jpg gif webp mp4 webm sniffed from bytes",
      [tm.sniff(b(9)) for b in (jpg, gif, webp, mp4, webm)] == ["jpg", "gif", "webp", "mp4", "webm"])
check("...a QuickTime box is not an MP4",
      tm.sniff(b"\x00\x00\x00\x14ftypqt  " + b"\0" * 32) is None)

# ---- 2. the rule ------------------------------------------------------------------
tm.add("alpha", mp4(1), "Clip")
tm.add("alpha", jpg(1))
tm.add("alpha", gif(1))
lst = tm.add("alpha", webp(1))
check("5 slides with 1 video is fine", len(lst) == 5
      and sum(1 for i in lst if i["kind"] == "video") == 1, lst)
check("a sixth slide is refused", refused(tm.add, "alpha", png(5), "", "",
                                          want="already has 5 slides"))
tm.remove("alpha", lst[-1]["file"])
check("a second video is refused", refused(tm.add, "alpha", webm(1), "", "",
                                           want="already has a video"))
check("the rule holds on a list too: 2 videos",
      refused(tm.check_rule, [{"kind": "video"}, {"kind": "video"}], want="video"))
check("...and 6 slides", refused(tm.check_rule, [{"kind": "image"}] * 6, want="5 slides"))

# ---- 3. arrange, delete, serve -----------------------------------------------------
lst = tm.items("alpha")
order = [i["file"] for i in lst][::-1]
new = tm.arrange("alpha", order, {order[0]: "First now"})
check("reorder and caption", [i["file"] for i in new] == order and new[0]["caption"] == "First now")
check("a reorder naming other files is refused",
      refused(tm.arrange, "alpha", order[:-1] + ["0000000000000000.png"], {}))
gone = order[0]
tm.remove("alpha", gone)
check("delete removes the entry and the file",
      gone not in [i["file"] for i in tm.items("alpha")]
      and not (tm.root() / "alpha" / gone).exists())
listed = tm.items("alpha")[0]["file"]
check("a listed file is served with its validated type",
      tm.path_of("alpha", listed) is not None and tm.path_of("alpha", listed)[1].startswith("image/"))
check("traversal and unlisted names are not served",
      tm.path_of("alpha", "../../etc/passwd") is None
      and tm.path_of("alpha", "media.json") is None
      and tm.path_of("../alpha", listed) is None
      and tm.path_of("alpha", "0123456789abcdef.png") is None)

# ---- 4. publish --------------------------------------------------------------------
commits = []


def fake_gh(src, path, tok, method="GET", body=None):
    raise urllib.error.HTTPError("u", 404, "nf", {}, None)


def fake_commit(target, blob, message, tok):
    commits.append((target["path"], blob))
    return {"unchanged": False, "commit": "abc", "url": ""}


source_publish._gh, source_publish._commit = fake_gh, fake_commit
MIR = {"owner": "o", "repo": "r", "branch": "main", "path": "templates/formats.toml"}
res = sync.publish(MIR, "tok", CFG, "msg")
paths = [p for p, _ in commits]
local = tm.items("alpha")
check("publish sends every file, then the list, then its signature",
      res["error"] == "" and res["files"] == len(local) == res["uploaded"]
      and paths[:-2] == [f"templates/media/alpha/{i['file']}" for i in local]
      and paths[-2:] == ["templates/media.json", "templates/media.json.sig"], (res, paths))
published = dict(commits)
doc = json.loads(published["templates/media.json"])
check("...the list names file, sha256, size, kind, type, caption in order",
      [i["file"] for i in doc["templates"]["alpha"]] == [i["file"] for i in local]
      and all(set(i) == {"file", "sha256", "size", "kind", "type", "caption"}
              for i in doc["templates"]["alpha"]), doc)
check("...LF and ASCII", b"\r\n" not in published["templates/media.json"])
from core.services import store as store_service                 # noqa: E402
try:
    store_service.verify_bytes(published["templates/media.json"],
                               published["templates/media.json.sig"].decode(), CFG)
    check("...and the signature verifies", True)
except store_service.StoreError as exc:
    check("...and the signature verifies", False, exc)

# ---- 5. a box ----------------------------------------------------------------------
BOX_STORE = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = BOX_STORE
config.save_local("update", {"token": "t"}, CFG)
src_files = {f"templates/media/alpha/{p.split('/')[-1]}": b for p, b in commits[:-2]}


def serve(listing, sig=None, overrides=None):
    blob = listing if isinstance(listing, bytes) else sync.manifest_bytes(listing)
    good = base64.b64encode(_priv.sign(blob))
    table = dict(src_files, **(overrides or {}))
    table["templates/media.json"] = blob
    table["templates/media.json.sig"] = sig if sig is not None else good

    def _open(url, token=None, **kw):
        path = url.split("/contents/", 1)[1].split("?", 1)[0]
        if path not in table:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return io.BytesIO(table[path])
    ghnet.open_url = _open


def box_files():
    return [i["file"] for i in tm.items("alpha")]


serve({"alpha": local})
out = sync.after_check(CFG, False)["media"]
check("a box takes the signed list and its files",
      out["installed"] == ["alpha"] and box_files() == [i["file"] for i in local]
      and not out["errors"], out)
before = box_files()

def entry(b, ext="png", kind="image", ctype="image/png"):
    sha = hashlib.sha256(b).hexdigest()
    return {"file": f"{sha[:16]}.{ext}", "kind": kind, "type": ctype, "sha256": sha,
            "size": len(b), "caption": ""}


new = entry(png(55))
serve({"alpha": local + [new]}, overrides={f"templates/media/alpha/{new['file']}": png(77)})
out = sync.after_check(CFG, False)["media"]
check("a tampered file is refused and the box keeps what it had",
      out["errors"] and box_files() == before
      and not (tm.root() / "alpha" / new["file"]).exists(), (out, box_files()))

serve({"alpha": local + [new]},
      overrides={f"templates/media/alpha/{new['file']}": png(55) + b"\0" * tm.MAX_BYTES})
out = sync.after_check(CFG, False)["media"]
check("a file served over 5 MB is refused", out["errors"] and box_files() == before, out)

big = dict(local[0], size=tm.MAX_BYTES + 1)
serve({"alpha": [big]})
out = sync.after_check(CFG, False)["media"]
check("an over-size entry in a signed list is refused", out["errors"] and box_files() == before, out)

vid = {"file": hashlib.sha256(webm(2)).hexdigest()[:16] + ".webm", "kind": "video",
       "type": "video/webm", "sha256": hashlib.sha256(webm(2)).hexdigest(),
       "size": len(webm(2)), "caption": ""}
two_videos = [i for i in local if i["kind"] == "video"] + [vid]
serve({"alpha": two_videos}, overrides={f"templates/media/alpha/{vid['file']}": webm(2)})
out = sync.after_check(CFG, False)["media"]
check("a signed list with a second video is refused", out["errors"] and box_files() == before, out)

pics = []
for n in range(6):
    b = png(100 + n)
    pics.append(({"file": hashlib.sha256(b).hexdigest()[:16] + ".png", "kind": "image",
                  "type": "image/png", "sha256": hashlib.sha256(b).hexdigest(),
                  "size": len(b), "caption": ""}, b))
serve({"alpha": [p for p, _ in pics]},
      overrides={f"templates/media/alpha/{p['file']}": b for p, b in pics})
out = sync.after_check(CFG, False)["media"]
check("a signed list with six slides is refused", out["errors"] and box_files() == before, out)

trav = dict(local[0], file="../../../x.png")
serve({"alpha": [trav]})
out = sync.after_check(CFG, False)["media"]
check("a traversal name in a signed list is refused", out["errors"] and box_files() == before, out)

serve({"alpha": []}, sig=base64.b64encode(b"x" * 64))
out = sync.after_check(CFG, False)["media"]
check("a list that does not verify changes nothing, and never raises",
      out["errors"] and box_files() == before, out)

keep = dict(local[0])
serve({"alpha": [keep]},
      overrides={f"templates/media/alpha/{keep['file']}": b"\0" * (tm.MAX_BYTES + 2)})
# Already here and correct, so it is not read again; the list shrinks to it.
out = sync.after_check(CFG, False)["media"]
check("a file already here with the right hash is not downloaded again",
      not out["errors"] and box_files() == [keep["file"]], out)

check("the source hub compares instead of installing",
      "differs" in sync.after_check(CFG, True)["media"])

# ---- 6. routes ---------------------------------------------------------------------
try:
    import fastapi   # noqa: F401
    import httpx     # noqa: F401
    import multipart  # noqa: F401
    have = True
except ImportError:
    have = False
if not have:
    print("skip: fastapi, httpx or python-multipart is not installed here -- routes not run")
else:
    from fastapi import FastAPI                                     # noqa: E402
    from fastapi.testclient import TestClient                       # noqa: E402
    from core.dev import template_media as dev_routes               # noqa: E402
    from core.routes import template_media as read_routes           # noqa: E402
    from core.services import auth as auth_service                  # noqa: E402

    os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
    auth_service.is_developer = lambda user, cfg=None: user == "dev"
    app = FastAPI()
    app.state.config_dir = CFG

    @app.middleware("http")
    async def who(request, call_next):
        request.state.user = request.headers.get("x-user") or None
        return await call_next(request)
    app.include_router(dev_routes.router)
    app.include_router(read_routes.router)
    c = TestClient(app)
    DEV, OP = {"x-user": "dev"}, {"x-user": "admin"}
    up = lambda body, name="a.png", h=DEV: c.post(  # noqa: E731
        "/api/hub/formats/alpha/media", headers=h, files={"file": (name, body)},
        data={"caption": "Hi"})
    r = up(png(1), h=OP)
    check("route: a non-developer cannot add", r.status_code == 403, r.text)
    r = up(png(1))
    check("route: a developer adds a PNG", r.status_code == 200 and r.json()["slides"] == 1, r.text)
    name = r.json()["items"][0]["file"]
    check("route: SVG refused in words", up(SVG, "a.svg").status_code == 400
          and "Only JPEG" in up(SVG, "a.png").json()["detail"])
    check("route: over 5 MB refused", up(png(2) + b"\0" * tm.MAX_BYTES).status_code == 400)
    check("route: unknown template 404", c.post("/api/hub/formats/nope/media", headers=DEV,
                                                files={"file": ("a.png", png(3))}).status_code == 404)
    check("route: a non-developer cannot delete",
          c.delete(f"/api/hub/formats/alpha/media/{name}", headers=OP).status_code == 403)
    g = c.get(f"/api/hub/template-media/alpha/{name}", headers=OP)
    check("route: any signed-in account reads it, typed and nosniff",
          g.status_code == 200 and g.headers["content-type"] == "image/png"
          and g.headers.get("x-content-type-options") == "nosniff", dict(g.headers))
    check("route: signed out is refused",
          c.get(f"/api/hub/template-media/alpha/{name}").status_code == 401)
    check("route: traversal is not found",
          c.get("/api/hub/template-media/alpha/..%2F..%2Fstore.toml", headers=OP).status_code == 404)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
