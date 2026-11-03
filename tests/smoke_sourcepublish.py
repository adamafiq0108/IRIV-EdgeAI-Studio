# The built-in templates are published to the SOURCE repository.
#
# config/formats.toml is the whole template list, and it was a file on one
# SD card: the developer box authored the four built-ins and no other box --
# and no release -- ever saw them. "Publish templates" is the missing last
# step: refresh every snapshot from the system it came from (so editing the
# template system and pressing the button IS the procedure), then commit the
# file to the release branch. The next release carries the templates.
#
# GitHub is faked here: the contract under test is what is sent, when nothing
# is sent (unchanged bytes), and how a refusal is worded.
import base64
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

from core import config                                       # noqa: E402
from core.services import provision                           # noqa: E402
from core.services import source_publish as sp                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()

# -- where it publishes, and the token ------------------------------------------
src = sp.source(CFG)
check("publishes formats.toml to the source repo's release branch",
      src["repo"] == "irivai-systor" and src["path"] == "config/formats.toml"
      and src["branch"] == "cytron-dashboard", repr(src))
check("no token = not ready, and the status never carries the token",
      sp.status(CFG)["token"] is False and "value" not in json.dumps(sp.status(CFG)))
sp.set_token("github_pat_secret", CFG)
check("...a token is stored in its own local file, 0600-style, never echoed",
      sp.status(CFG)["token"] is True
      and "secret" not in json.dumps(sp.status(CFG))
      and os.path.exists(os.path.join(CFG, "source_publish.local.toml")))

# -- a fake GitHub ---------------------------------------------------------------
calls = []
remote = {"content": None}


def fake_gh(src_, path, tok, method="GET", body=None):
    calls.append((method, path, body))
    if method == "GET":
        if remote["content"] is None:
            raise urllib.error.HTTPError(path, 404, "nf", {}, io.BytesIO(b""))
        return {"sha": "abc123", "encoding": "base64",
                "content": base64.b64encode(remote["content"]).decode()}
    if method == "PUT":
        remote["content"] = base64.b64decode(body["content"])
        return {"commit": {"sha": "deadbeefcafe0000", "html_url": "https://x/c"}}
    raise AssertionError(method)


sp._gh = fake_gh

# Two published templates, no systems filed (the refresh is exercised by the
# hub route tests; here the file is the input).
provision.save_format("blank_canvas", {"label": "Blank Canvas", "hint": "",
                                       "widgets": [], "draft": False}, CFG)
provision.save_format("counting", {"label": "People Counting", "hint": "",
                                   "widgets": [], "draft": False}, CFG)

out = sp.publish("systems", CFG)
put = [c for c in calls if c[0] == "PUT"]
check("the first publish creates the file on the branch",
      out["ok"] and not out["unchanged"] and len(put) == 1
      and put[0][1] == "contents/config/formats.toml"
      and put[0][2]["branch"] == "cytron-dashboard" and "sha" not in put[0][2],
      repr(out) + repr(put))
sent = base64.b64decode(put[0][2]["content"])
check("...and what is sent is the box's formats.toml, LF",
      b"[formats.counting]" in sent and b"\r\n" not in sent, sent[:80])
check("...naming the templates in the commit message",
      "- blank_canvas" in put[0][2]["message"] and "- counting" in put[0][2]["message"],
      put[0][2]["message"])
check("...and the commit is remembered for the panel",
      out["commit"] == "deadbeefcafe" and sp.status(CFG)["last"]["commit"] == "deadbeefcafe",
      repr(sp.status(CFG)))

calls.clear()
out = sp.publish("systems", CFG)
check("publishing again with nothing changed sends nothing",
      out["unchanged"] is True and not [c for c in calls if c[0] == "PUT"], repr(calls))

provision.set_format_label("counting", "People Counter", CFG)
calls.clear()
out = sp.publish("systems", CFG)
put = [c for c in calls if c[0] == "PUT"]
check("a changed file is an update, carrying the blob's sha",
      len(put) == 1 and put[0][2].get("sha") == "abc123"
      and b"People Counter" in base64.b64decode(put[0][2]["content"]), repr(put))

# -- refusals in the words of the person fixing them --------------------------
def refuse(code):
    def gh(*a, **k):
        raise urllib.error.HTTPError("x", code, "no", {}, io.BytesIO(b""))
    return gh


for code, word in ((401, "expired"), (404, "Contents"), (422, "branch")):
    sp._gh = refuse(code)
    try:
        sp.publish("systems", CFG)
        check(f"a {code} is refused", False, "no error")
    except ValueError as exc:
        check(f"a {code} says what to fix", word in str(exc), str(exc))
sp._gh = fake_gh

sp.set_token("", CFG)
try:
    sp.publish("systems", CFG)
    check("no token refuses", False)
except ValueError as exc:
    check("no token refuses before touching GitHub", "token" in str(exc), str(exc))

# -- the routes exist, developer-gated, and the panel has the button ------------
routes = io.open(os.path.join(ROOT, "core", "dev", "formats.py"), encoding="utf-8").read()
for path in ("/api/hub/formats/source", "/api/hub/formats/source/token",
             "/api/hub/formats/source/publish"):
    check(f"route {path} exists", f'"{path}"' in routes)
tail = routes[routes.index('"/api/hub/formats/source"'):]
check("...every one of them behind the developer tier",
      tail.count("_require_developer") >= 3)
panel = io.open(os.path.join(ROOT, "frontend", "src", "components", "TemplatesPanel.jsx"),
                encoding="utf-8").read()
check("the panel has the button and the dialog",
      "Publish templates" in panel and "SourcePublishDialog" in panel
      and "/api/hub/formats/source/publish" in panel)
cfg = io.open(os.path.join(ROOT, "config", "update.toml"), encoding="utf-8").read()
check("update.toml declares [source]", "[source]" in cfg and 'repo = "irivai-systor"' in cfg)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
