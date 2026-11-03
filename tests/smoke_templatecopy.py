# -*- coding: utf-8 -*-
"""
A built-in template's own preview words (Tok Aba, 2026-10-22, third round):
the "What you get" lines (`features`), the sentence under the title
(`blurb`) and the list's heading (`heading`, 2026-10-23), stored in the
template's formats.toml entry.

  1. limits: at most 8 lines, 140 characters a line, 200 for the sentence,
     no empty line; control characters removed; HTML stays literal text
  2. set and reset ("Use automatic text"), everything else in the entry
     untouched; a save that does not name them (Details, a Publish refresh)
     keeps them
  3. the template update: a box installs a changed list and clears a dropped
     one; the developer hub reports the difference and installs nothing
  4. the route: developer only, refusals in words (skipped where fastapi,
     httpx are missing)

    python tests/smoke_templatecopy.py
"""

import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                          # noqa: E402
from core.services import provision                              # noqa: E402
from core.services import template_preview as tp                 # noqa: E402
from core.services import template_updates as tu                 # noqa: E402

bad = 0


def check(msg, passed, detail=""):
    global bad
    if passed:
        print("ok   " + msg)
    else:
        print("FAIL " + msg + ("\n      " + str(detail) if detail else ""))
        bad += 1


def refused(features, blurb="", want=""):
    try:
        tp.clean_copy({"features": features, "blurb": blurb})
    except ValueError as exc:
        return want in str(exc)
    return False


def stored(cfg, key="alpha"):
    config.load.cache_clear()
    return provision._formats_overlay(cfg).get(key) or {}


CFG = tempfile.mkdtemp()
provision.save_format("alpha", {"label": "Alpha", "hint": "Its line", "widgets": [],
                                "draft": False}, CFG)

# ---- 1. limits --------------------------------------------------------------------
check("8 lines are fine", tp.clean_copy({"features": ["x"] * 8})["features"] == ["x"] * 8)
check("a 9th line is refused", refused(["x"] * 9, want="at most 8 lines"))
check("141 characters is refused", refused(["y" * 141], want="140 characters"))
check("140 is fine", tp.clean_copy({"features": ["y" * 140]})["features"] == ["y" * 140])
check("an empty line is refused", refused(["ok", "   "], want="empty"))
check("a 201-character sentence is refused", refused([], "z" * 201, want="200 characters"))
got = tp.clean_copy({"features": ["  Counts\x00 people\tin\n a zone "], "blurb": "A\x07 line"})
check("control characters go, spaces collapse, ends trimmed",
      got == {"features": ["Counts people in a zone"], "blurb": "A line"}, got)
html = tp.clean_copy({"features": ["<b>bold</b> & <script>x</script>"]})["features"][0]
check("HTML is kept as the literal text it is", html == "<b>bold</b> & <script>x</script>", html)
check("nothing set = nothing stored", tp.clean_copy({"features": [], "blurb": ""}) == {})
check("not a list is refused", refused("a line", want="list"))
try:
    tp.clean_copy({"heading": "h" * 41})
    check("a 41-character heading is refused", False)
except ValueError as exc:
    check("a 41-character heading is refused", "40 characters" in str(exc), exc)
check("a 40-character heading is fine, cleaned like the rest",
      tp.clean_copy({"heading": " What\x00 you  get <i>" + "x" * 23})["heading"]
      == "What you get <i>" + "x" * 23)

# ---- 2. set, keep, reset -------------------------------------------------------------
before = dict(stored(CFG))
out = tp.set_copy("alpha", ["Counts people", "<i>Shows</i> a chart"], "Count who comes in.", CFG,
                  heading="Inside")
check("set: all three stored", out == {"features": ["Counts people", "<i>Shows</i> a chart"],
                                       "blurb": "Count who comes in.", "heading": "Inside"}, out)
after = stored(CFG)
check("...everything else in the entry untouched",
      {k: v for k, v in after.items() if k not in ("features", "blurb", "heading")} == before, after)
check("...and the file reads back", "Counts people" in io.open(
    os.path.join(CFG, "formats.toml"), encoding="utf-8").read())
provision.save_format("alpha", {"label": "Alpha renamed", "widgets": []}, CFG)
check("a save that does not name them (Details) keeps them",
      tp.copy_of("alpha", CFG)["features"] == ["Counts people", "<i>Shows</i> a chart"]
      and stored(CFG)["label"] == "Alpha renamed")
provision.promote_template("alpha", {"name": "Alpha", "dashboard": {}}, CFG)
check("...so does a Publish refresh (promote_template)",
      tp.copy_of("alpha", CFG)["blurb"] == "Count who comes in."
      and tp.copy_of("alpha", CFG)["heading"] == "Inside")
tp.set_copy("alpha", [], "", CFG)
check("Use automatic text clears both, no key left behind",
      tp.copy_of("alpha", CFG) == {"features": [], "blurb": "", "heading": ""}
      and not {"features", "blurb", "heading"} & set(stored(CFG)))
try:
    tp.set_copy("nope", ["x"], "", CFG)
    check("an unknown template is refused", False)
except ValueError:
    check("an unknown template is refused", True)

# ---- 3. the template update -------------------------------------------------------------
def published(entry):
    return (config._toml_dump({"formats": {"alpha": entry}}) + "\n").encode()


upstream = dict(stored(CFG), features=["Line from the developer"], blurb="Upstream words.",
                heading="Upstream heading")
upstream.pop("draft", None)
tu._fetch = lambda config_dir=None: published(upstream)
r = tu.check(CFG, tempfile.mkdtemp())
check("a box installs a changed list", [t["key"] for t in r["updated"]] == ["alpha"]
      and tp.copy_of("alpha", CFG) == {"features": ["Line from the developer"],
                                       "blurb": "Upstream words.",
                                       "heading": "Upstream heading"}, r)
r = tu.check(CFG, tempfile.mkdtemp())
check("...and then it is unchanged", r["unchanged"] == ["alpha"] and not r["updated"], r)
upstream["heading"] = "A new heading"
r = tu.check(CFG, tempfile.mkdtemp())
check("a heading change alone is an update on a box",
      [t["key"] for t in r["updated"]] == ["alpha"]
      and tp.copy_of("alpha", CFG)["heading"] == "A new heading", r)
upstream.pop("features")
upstream.pop("blurb")
upstream.pop("heading")
r = tu.check(CFG, tempfile.mkdtemp())
check("an upstream that dropped its words clears them on the box",
      [t["key"] for t in r["updated"]] == ["alpha"]
      and tp.copy_of("alpha", CFG) == {"features": [], "blurb": "", "heading": ""}, r)

DEV = tempfile.mkdtemp()
io.open(os.path.join(DEV, "deploy.toml"), "w", encoding="utf-8").write('mode = "dev"\n')
provision.save_format("alpha", {"label": stored(CFG)["label"], "widgets": [],
                                "draft": False}, DEV)
dev_entry = dict(stored(DEV))
dev_entry.pop("draft", None)
upstream = dict(dev_entry)
tu._fetch = lambda config_dir=None: published(upstream)
r = tu.check(DEV, tempfile.mkdtemp())
check("dev hub: same words, nothing differs", r["authoring"] and not r["differs"], r)
tp.set_copy("alpha", ["New words, not published yet"], "", DEV)
r = tu.check(DEV, tempfile.mkdtemp())
check("dev hub: changed words show as differing, nothing installed",
      [t["key"] for t in r["differs"]] == ["alpha"] and not r["updated"]
      and tp.copy_of("alpha", DEV)["features"] == ["New words, not published yet"], r)
tp.set_copy("alpha", [], "", DEV, heading="Only the heading")
r = tu.check(DEV, tempfile.mkdtemp())
check("dev hub: a heading change alone shows as differing",
      [t["key"] for t in r["differs"]] == ["alpha"], r)

# ---- 4. the route ---------------------------------------------------------------------
try:
    import fastapi   # noqa: F401
    import httpx     # noqa: F401
    have = True
except ImportError:
    have = False
if not have:
    print("skip: fastapi or httpx is not installed here -- the route was not run")
else:
    from fastapi import FastAPI                                     # noqa: E402
    from fastapi.testclient import TestClient                       # noqa: E402
    from core.dev import template_copy as routes                    # noqa: E402
    from core.services import auth as auth_service                  # noqa: E402

    auth_service.is_developer = lambda user, cfg=None: user == "dev"
    app = FastAPI()
    app.state.config_dir = CFG

    @app.middleware("http")
    async def who(request, call_next):
        request.state.user = request.headers.get("x-user") or None
        return await call_next(request)
    app.include_router(routes.router)
    c = TestClient(app)
    url = "/api/hub/formats/alpha/copy"
    r = c.put(url, json={"features": ["a"], "blurb": ""}, headers={"x-user": "admin"})
    check("route: a non-developer is refused", r.status_code == 403, r.text)
    r = c.put(url, json={"features": ["Counts <b>people</b>"], "blurb": "Hi"},
              headers={"x-user": "dev"})
    check("route: a developer saves", r.status_code == 200
          and r.json()["features"] == ["Counts <b>people</b>"], r.text)
    r = c.put(url, json={"features": ["x"] * 9}, headers={"x-user": "dev"})
    check("route: a broken limit is refused in words", r.status_code == 400
          and "at most 8 lines" in r.json()["detail"], r.text)
    r = c.put(url, json={"features": [], "blurb": "", "heading": "My heading"},
              headers={"x-user": "dev"})
    check("route: the heading is saved", r.status_code == 200
          and r.json()["heading"] == "My heading", r.text)
    r = c.put(url, json={"heading": "h" * 41}, headers={"x-user": "dev"})
    check("route: a long heading is refused", r.status_code == 400, r.text)
    r = c.put(url, json={"features": [], "blurb": "", "heading": ""}, headers={"x-user": "dev"})
    check("route: reset", r.status_code == 200 and r.json()["features"] == []
          and r.json()["heading"] == "", r.text)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
