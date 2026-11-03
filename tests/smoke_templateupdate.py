# "Check for template updates": a box installs the templates the developer
# published, and nothing else moves.
#
# Asked for on 2026-09-24. The developer hub's Publish button commits
# config/formats.toml to GitHub; until now the only way that file reached a
# box was the next release. The create wizard gets a button that fetches the
# published file and installs what is new or changed. Three rules came with
# the request, and each is pinned here:
#
#   * "current system will not be affected" -- only formats.toml changes.
#   * "never commit it automatically" -- this reads GitHub, never writes it,
#     and nothing runs on a timer.
#   * the templates a box AUTHORS are kept: on the developer's own hub the
#     published copy is at best what that hub sent, and at worst older than
#     the edits in progress.
#
# And the failure the whole thing is designed around: a second check right
# after the first must find NOTHING to do. The published entry and the local
# one are compared in the stored shape, because comparing raw files would
# call every re-serialised JSON blob a change and the button would claim an
# update it had not made, every time, for ever.
#
# Also here: the panels themselves. On the board this was reported from, a
# dev frontend build sat on a box in customer mode, so System Templates and
# Licenses drew every button and every one of them answered 404 or 405. The
# shell now needs the box to say it carries the tooling.
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
from core.services import auth                                   # noqa: E402
from core.services import ghnet                                  # noqa: E402
from core.services import provision                              # noqa: E402
from core.services import source_publish                         # noqa: E402
from core.services import template_updates as tu                 # noqa: E402

bad = 0


def check(msg, passed, detail=""):
    global bad
    if passed:
        print("ok   " + msg)
    else:
        print("FAIL " + msg + ("\n      " + str(detail) if detail else ""))
        bad += 1


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def http_error(code):
    return urllib.error.HTTPError("https://api.github.com/x", code, "nope",
                                  {}, io.BytesIO(b""))


def published(formats):
    """The bytes the developer hub commits: the same writer formats.toml
    uses, so the test exercises the real round trip."""
    return (config._toml_dump({"formats": formats}) + "\n").encode("utf-8")


def overlay(cfg):
    return provision._formats_overlay(cfg)


CFG = tempfile.mkdtemp()
SYSTEMS = tempfile.mkdtemp()

# A throwaway store signing keypair: the publish side signs with the private
# half, the box side verifies with the public half it finds in store.toml --
# exactly the arrangement in the field, minus the real key.
from cryptography.hazmat.primitives import serialization           # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (     # noqa: E402
    Ed25519PrivateKey)
import base64                                                       # noqa: E402
_priv = Ed25519PrivateKey.generate()
TEST_PEM = _priv.private_bytes(serialization.Encoding.PEM,
                               serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
TEST_PUB_B64 = base64.b64encode(_priv.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def trust_test_key(cfg):
    """Make `cfg` a box that trusts the throwaway key."""
    io.open(os.path.join(cfg, "store.toml"), "w", encoding="utf-8").write(
        '[signing]\npubkey = "%s"\n' % TEST_PUB_B64)
    config.load.cache_clear()


def hold_test_key(cfg):
    """Make `cfg` a hub that can sign with it."""
    io.open(os.path.join(cfg, "store_signing.pem"), "wb").write(TEST_PEM)


trust_test_key(CFG)

# ===========================================================================
# 0. where a box reads from
# ===========================================================================
# The source repo is private to the product and a customer box has no token
# for it -- by design. The release repo is the one every box already reads
# with its update token, so the published copy lives there.
tgt = tu.target(CFG)
check("a box reads the templates from the release repo, not the source",
      tgt["repo"] == "irivai-release" and tgt["owner"] == "adamafiq0108"
      and tgt["branch"] == "main" and tgt["path"] == "templates/formats.toml",
      tgt)
check("...which is exactly where Publish puts the copy",
      source_publish.mirror(CFG) == tgt)
check("...and the source commit still goes to the branch releases are built from",
      source_publish.source(CFG)["repo"] == "irivai-systor", source_publish.source(CFG))

# ===========================================================================
# 1. the box before the check
# ===========================================================================
provision.save_format("alpha", {"label": "Alpha", "hint": "the same",
                                "classes": ["person"], "widgets": [],
                                "draft": False}, CFG)
provision.save_format("beta", {"label": "Beta, old", "widgets": [],
                               "draft": False}, CFG)
provision.save_format("gamma", {"label": "Gamma, only here", "widgets": [],
                                "draft": False}, CFG)
provision.save_format("veiled", {"label": "Veiled", "widgets": [],
                                 "draft": False}, CFG)
# The box owner withdrew one. TEMPLATES ships empty now, so the tombstone is
# written the way delete_format would write it.
formats = dict((config.load(provision.FORMATS_FILE, CFG) or {}).get("formats") or {})
formats["veiled"] = dict(formats["veiled"], hidden=True)
provision._write_formats(formats, CFG)
check("the fixture box holds four templates, one of them hidden",
      set(overlay(CFG)) == {"alpha", "beta", "gamma", "veiled"}
      and "veiled" not in provision.builtin_formats(CFG))

# ===========================================================================
# 2. what the developer published
# ===========================================================================
graph = {"version": 3, "workflows": [{"id": "main", "name": "Main", "graph": {
    "version": 3,
    "nodes": [{"id": "m1", "type": "source.detection",
               "position": {"x": 0, "y": 0}, "data": {}}],
    "edges": []}}]}
delta_entry = provision.format_entry("delta", {
    "label": "Delta", "hint": "brand new", "category": "retail",
    "classes": ["person", "chair"], "widgets": [{"id": "w1", "kind": "tile"}],
    "zone": {"mode": "line", "hint": "Draw a line."}, "model": "yolov8s",
    "workflows": graph,
    "dashboard": {"pages": [{"id": "main", "widgets": []}]},
    "tables": [{"key": "t", "name": "T", "columns": []}],
    "zones": [{"id": "z1", "kind": "line", "points": [[0, 0], [1, 1]]}],
    "settings": {"capture": {"snap": True, "record": False}}})[1]
remote = {
    # Byte-for-byte what the box has: must read as unchanged.
    "alpha": dict(overlay(CFG)["alpha"]),
    # A newer edition of one the box has.
    "beta": provision.format_entry("beta", {"label": "Beta, new",
                                            "widgets": [], "workflows": graph})[1],
    "delta": delta_entry,
    # The developer's half-built work is in the file too. Not a template.
    "epsilon": dict(provision.format_entry("epsilon", {"label": "Eps", "widgets": []})[1],
                    draft=True),
    # A published edition of the one this box withdrew.
    "veiled": provision.format_entry("veiled", {"label": "Veiled, new",
                                                "widgets": []})[1],
    # Not a key this product would ever write.
    "Bad-Key": {"label": "x"},
}
real_fetch = tu._fetch
tu._fetch = lambda cfg=None: published(remote)           # no network in a test

# ===========================================================================
# 3. the check installs the new and the changed, and only those
# ===========================================================================
r = tu.check(CFG, SYSTEMS)
keys = lambda items: sorted(t["key"] for t in items)     # noqa: E731
check("the new template is added", keys(r["added"]) == ["delta"], r["added"])
check("the changed template is updated", keys(r["updated"]) == ["beta"], r["updated"])
check("the identical one is reported unchanged, not rewritten",
      r["unchanged"] == ["alpha"], r["unchanged"])
check("a draft upstream, a bad key, and the one this box hid are all skipped",
      set(r["skipped"]) == {"epsilon", "Bad-Key", "veiled"}, r["skipped"])
check("nothing was kept -- no template here is authored here yet",
      r["kept"] == [] and r["errors"] == [], (r["kept"], r["errors"]))
after = overlay(CFG)
check("the box now holds the new template, published, not as a draft",
      "delta" in provision.builtin_formats(CFG)
      and not provision.builtin_formats(CFG)["delta"]["draft"])
fmt = provision.builtin_formats(CFG)["delta"]
check("...with its whole build: workflow, dashboard, tables, zones, settings, model",
      fmt.get("has_workflow") and isinstance(fmt.get("dashboard"), dict)
      and fmt.get("tables") and fmt.get("zones") and fmt.get("settings")
      and fmt["model"] == "yolov8s" and fmt["category"] == "retail",
      {k: fmt.get(k) for k in ("has_workflow", "model", "category")})
check("the changed one carries its new label and its new workflow",
      after["beta"]["label"] == "Beta, new" and after["beta"].get("workflows_json"),
      after["beta"].get("label"))
check("a template only this box has is KEPT -- this installs, it never prunes",
      "gamma" in after and after["gamma"]["label"] == "Gamma, only here")
check("the one this box withdrew stays withdrawn, old label and all",
      after["veiled"].get("hidden") is True and after["veiled"]["label"] == "Veiled",
      after["veiled"])
check("the draft upstream did not land", "epsilon" not in after)
check("the answer names templates by label for the toast",
      r["added"][0]["label"] == "Delta" and r["updated"][0]["label"] == "Beta, new")
st = tu.status(CFG)
check("the check is remembered for the wizard to show",
      st["checked_at"] > 0 and st["last"]["ok"] is True
      and st["last"]["added"] == ["delta"] and st["last"]["updated"] == ["beta"],
      st)

# ===========================================================================
# 4. THE SECOND CHECK FINDS NOTHING TO DO
# ===========================================================================
# The published entry is validated into the stored shape and compared with
# the local record in that shape. Compare the raw entries and every
# re-serialised blob is "a change": the button reports an update it did not
# make, every time.
r2 = tu.check(CFG, SYSTEMS)
check("a check straight after a check installs nothing",
      r2["added"] == [] and r2["updated"] == [], (r2["added"], r2["updated"]))
check("...and reports every published template as unchanged",
      sorted(r2["unchanged"]) == ["alpha", "beta", "delta"], r2["unchanged"])

# ===========================================================================
# 5. a template AUTHORED on this box is kept
# ===========================================================================
os.makedirs(os.path.join(SYSTEMS, "beta_editor"))
open(os.path.join(SYSTEMS, "beta_editor", "system.py"), "w").close()
provision.set_format_workbench("beta", "beta_editor", CFG)
# ...and a link to a system that no longer exists is no claim of authorship.
provision.set_format_workbench("delta", "gone_editor", CFG)
remote["beta"] = provision.format_entry("beta", {"label": "Beta, newer still",
                                                 "widgets": []})[1]
remote["delta"] = provision.format_entry("delta", {"label": "Delta 2",
                                                   "widgets": []})[1]
r3 = tu.check(CFG, SYSTEMS)
check("the template whose workbench lives here is kept, not overwritten",
      keys(r3["kept"]) == ["beta"] and overlay(CFG)["beta"]["label"] == "Beta, new",
      (r3["kept"], overlay(CFG)["beta"]["label"]))
check("...while a workbench link to a system that is gone protects nothing",
      keys(r3["updated"]) == ["delta"] and overlay(CFG)["delta"]["label"] == "Delta 2",
      r3["updated"])
check("...and the kept one is named as THIS box knows it, not by the upstream label",
      r3["kept"][0]["label"] == "Beta, new", r3["kept"])

# ===========================================================================
# 6. a template that will not validate costs nobody the others
# ===========================================================================
remote["broken"] = {"label": "", "widgets": []}          # no label: refused
remote["zeta"] = provision.format_entry("zeta", {"label": "Zeta", "widgets": []})[1]
remote["zeta"]["workflows_json"] = "{not json"
# A zone that is a STRING used to raise AttributeError out of the whole check,
# after some templates had already been installed and before anything was
# recorded -- the box changed and the wizard said the check had failed.
remote["eta"] = dict(provision.format_entry("eta", {"label": "Eta", "widgets": []})[1],
                     zone="optional")
r4 = tu.check(CFG, SYSTEMS)
check("an entry that fails validation is reported, not installed",
      {e["key"] for e in r4["errors"]} == {"broken", "zeta", "eta"}
      and not ({"broken", "zeta", "eta"} & set(overlay(CFG))), r4["errors"])
check("...and the errors carry the reason, in words", all(e["error"] for e in r4["errors"]))
check("...whatever the validator raised -- the check finished and was recorded",
      tu.status(CFG)["last"]["ok"] is True and tu.status(CFG)["last"]["errors"] == 3)
del remote["broken"], remote["zeta"], remote["eta"]

# ===========================================================================
# 7. when GitHub says no, the box says why, in the operator's words
# ===========================================================================
tu._fetch = real_fetch                                   # back to the real one
real_open = ghnet.open_url
seen = {}


def answering(codes):
    """open_url that answers each path from `codes`: an int is an HTTPError,
    bytes are a body."""
    def _open(url, token=None, **kw):
        seen.setdefault("urls", []).append(url)
        seen["token"] = token
        seen["method"] = kw.get("method")
        path = url.split("/contents/", 1)[1].split("?", 1)[0]
        answer = codes.get(path, 404)
        if isinstance(answer, int):
            raise http_error(answer)
        return io.BytesIO(answer)
    return _open


ghnet.open_url = answering({})
try:
    tu.check(CFG, SYSTEMS)
    check("a 404 with no token is refused", False)
except ValueError as exc:
    check("a 404 with no token says the box needs an update token, and where",
          "update token" in str(exc) and "notification bell" in str(exc), exc)
check("...and the fetch is a plain GET of the release repo's file",
      seen["method"] is None
      and seen["urls"][0].startswith("https://api.github.com/repos/adamafiq0108/"
                                     "irivai-release/contents/templates/formats.toml"),
      seen)
check("...remembered as a failed check, with the reason",
      tu.status(CFG)["last"]["ok"] is False and "update token" in tu.status(CFG)["last"]["error"])
config.save_local("update", {"token": "ghp_test_only"}, CFG)
try:
    tu.check(CFG, SYSTEMS)
except ValueError as exc:
    check("a 404 WITH a token says the owner has not published yet - nothing an admin must do",
          "has not published" in str(exc) and "developer hub" not in str(exc), exc)
check("...and the box's own update token rode along",
      seen["token"] == "ghp_test_only")
ghnet.open_url = answering({"templates/formats.toml": 401})
try:
    tu.check(CFG, SYSTEMS)
except ValueError as exc:
    check("a 401 says the token was refused", "refused" in str(exc), exc)
ghnet.open_url = answering({"templates/formats.toml": 403})
try:
    tu.check(CFG, SYSTEMS)
except ValueError as exc:
    check("a 403 is not 'nothing published' -- it names access or rate limiting",
          "403" in str(exc) and "rate limit" in str(exc), exc)


def unreachable(url, token=None, **kw):
    raise urllib.error.URLError("no route to host")


ghnet.open_url = unreachable
try:
    tu.check(CFG, SYSTEMS)
except ValueError as exc:
    check("offline says GitHub could not be reached", "could not reach" in str(exc), exc)

# -- the signature ------------------------------------------------------------
# A repository write is not a signature. The file is sealed with the store
# key on the developer hub, and a box installs nothing that does not verify.
body = published(remote)
good_sig = base64.b64encode(_priv.sign(body))
ghnet.open_url = answering({"templates/formats.toml": body})
try:
    tu.check(CFG, SYSTEMS)
    check("a published file with no signature is refused", False)
except ValueError as exc:
    check("a published file with no signature is refused, saying so",
          "no signature" in str(exc) and "store_signing.pem" in str(exc), exc)
ghnet.open_url = answering({"templates/formats.toml": body,
                            "templates/formats.toml.sig": base64.b64encode(b"x" * 64)})
try:
    tu.check(CFG, SYSTEMS)
    check("a bad signature is refused", False)
except ValueError as exc:
    check("a bad signature is refused", "does not verify" in str(exc), exc)
tampered = body.replace(b"Beta, new", b"Beta, evil")
ghnet.open_url = answering({"templates/formats.toml": tampered,
                            "templates/formats.toml.sig": good_sig})
try:
    tu.check(CFG, SYSTEMS)
    check("a file changed after signing is refused", False)
except ValueError as exc:
    check("a file changed after signing is refused", "does not verify" in str(exc), exc)
check("...and none of those touched the box",
      "Beta, evil" not in json.dumps(overlay(CFG)) and tu.status(CFG)["last"]["ok"] is False)
ghnet.open_url = answering({"templates/formats.toml": body,
                            "templates/formats.toml.sig": good_sig})
r5 = tu.check(CFG, SYSTEMS)
check("the genuine file, with its signature, is taken", r5["ok"] and r5["errors"] == [], r5)
ghnet.open_url = real_open
config.save_local("update", {}, CFG)

# -- a stray control character cannot brick the file --------------------------
# One U+0001 in a label used to be written raw, which is not TOML: the file
# stopped parsing, the create wizard offered nothing, and no page could write
# it again. Now it is escaped, reads back as the same character, and the file
# is re-parsed before it replaces the old one.
provision.save_format("ctl", {"label": "Evil\x01 label\x7f", "hint": "a\x02b",
                              "widgets": [], "draft": False}, CFG)
check("a control character in a label is written as an escape and reads back",
      overlay(CFG)["ctl"]["label"] == "Evil\x01 label\x7f"
      and overlay(CFG)["ctl"]["hint"] == "a\x02b"
      and "delta" in overlay(CFG), overlay(CFG).get("ctl"))
check("...and the file was swapped in whole, not rewritten in place",
      not os.path.exists(os.path.join(CFG, "formats.toml.tmp")))
try:
    provision.format_entry("x", {"label": "X", "zone": "optional"})
    check("a zone that is not an object is refused by the validator", False)
except ValueError as exc:
    check("a zone that is not an object is refused by the validator", "zone" in str(exc))

# -- THE AUTHORING HUB installs nothing -----------------------------------------
# A box declared dev is where the templates come from. Installing there would
# overwrite edits in progress and resurrect a template deleted before it was
# re-published. It compares instead, so the developer knows to press Publish.
DEV = tempfile.mkdtemp()
trust_test_key(DEV)
io.open(os.path.join(DEV, "deploy.toml"), "w", encoding="utf-8").write('mode = "dev"\n')
config.load.cache_clear()
provision.save_format("alpha", dict(tu.entry_data(remote["alpha"]), draft=False), DEV)
provision.save_format("beta", {"label": "Beta, being edited", "widgets": [],
                               "draft": False}, DEV)                 # differs upstream
provision.save_format("theta", {"label": "Theta, new here", "widgets": [],
                                "draft": False}, DEV)                 # not upstream
provision.save_format("iota", {"label": "Iota, a draft", "widgets": []}, DEV)   # a draft
before = json.dumps(overlay(DEV), sort_keys=True)
tu._fetch = lambda cfg=None: published(remote)
rd = tu.check(DEV, SYSTEMS)
check("on the authoring hub the check installs NOTHING",
      rd["authoring"] and rd["added"] == [] and rd["updated"] == []
      and json.dumps(overlay(DEV), sort_keys=True) == before, rd)
check("...it names the templates that differ from the published copy, by the local label",
      keys(rd["differs"]) == ["beta"] and rd["differs"][0]["label"] == "Beta, being edited",
      rd["differs"])
check("...the ones not published yet, drafts excluded",
      keys(rd["unpublished"]) == ["theta"], rd["unpublished"])
check("...and the ones published but removed here -- which do NOT come back",
      keys(rd["removed_here"]) == ["delta", "veiled"] and "delta" not in overlay(DEV),
      rd["removed_here"])
check("...while the identical one is unchanged", rd["unchanged"] == ["alpha"], rd["unchanged"])
check("the status tells the wizard which kind of hub this is",
      tu.status(DEV)["authoring"] is True and tu.status(CFG)["authoring"] is False)
tu._fetch = real_fetch

# ===========================================================================
# 8. never a write, never a timer
# ===========================================================================
src = read("core", "services", "template_updates.py")
check("the updater has no way to write to GitHub",
      "PUT" not in src and "_gh(" not in src and "method=" not in src.split("def _fetch")[1].split("def _explain")[0])
# Since 2026-10-14 the wizard's auto refresh runs its one check on a worker
# thread so the caller can stop waiting (smoke_templateautorefresh.py). That
# thread is started by a request and does one check; still no timer, no loop.
check("...and no timer: it runs when asked",
      "Timer" not in src and "while True" not in src and "start_checker" not in src
      and "interval" not in src and src.count("Thread(") == 1
      and src.index("Thread(") > src.index("def _auto_refresh"))
check("...and it never opens a system's config",
      "set_zones" not in src and "save_store" not in src
      and "apply_template" not in src and "recompile" not in src)

# ===========================================================================
# 9. Publish writes the same bytes to BOTH repos, and signs the copy for boxes
# ===========================================================================
PCFG = tempfile.mkdtemp()
provision.save_format("blank_canvas", {"label": "Blank Canvas", "widgets": [],
                                       "draft": False}, PCFG)
source_publish.set_token("ghp_publish_test", PCFG)
calls = []


def fake_gh(target, path, tok, method="GET", body=None):
    calls.append((target["repo"], path, method, body))
    if method == "GET":
        raise http_error(404)                    # no file on the branch yet
    return {"commit": {"sha": "abc123def4567890", "html_url": "https://x/c"}}


source_publish._gh = fake_gh
# Without the signing key: the source commit happens, the copy for boxes does
# not, and the answer says exactly that -- an unsigned copy would only be
# refused later by every box.
out0 = source_publish.publish(tempfile.mkdtemp(), PCFG)
puts0 = [(c[0], c[1]) for c in calls if c[2] == "PUT"]
check("without a signing key the publish still commits to the source repo",
      out0["ok"] and out0["commit"] == "abc123def456"
      and puts0 == [("irivai-systor", "contents/config/formats.toml")], (out0, puts0))
check("...but writes no copy for boxes, and says why",
      out0["mirror_commit"] == "" and "store_signing.pem" in out0["mirror_error"]
      and "signed" in out0["mirror_error"], out0["mirror_error"])
check("...and the publish settings tell the panel the key is missing",
      source_publish.status(PCFG)["signing"] is False)

hold_test_key(PCFG)
calls.clear()
out = source_publish.publish(tempfile.mkdtemp(), PCFG)
puts = [c for c in calls if c[2] == "PUT"]
# ...then the preview media list and its signature (template_media_sync.py;
# no media here, so the list is empty and no file goes before it).
check("with the key, one press commits to the source repo AND the release repo, file then signature",
      [(c[0], c[1]) for c in puts]
      == [("irivai-systor", "contents/config/formats.toml"),
          ("irivai-release", "contents/templates/formats.toml"),
          ("irivai-release", "contents/templates/formats.toml.sig"),
          ("irivai-release", "contents/templates/media.json"),
          ("irivai-release", "contents/templates/media.json.sig")], puts)
check("...the very same bytes, so a box fetches what the next release carries",
      len(puts) == 5 and puts[0][3]["content"] == puts[1][3]["content"])
check("...and the answer says what the media did",
      out["media"]["error"] == "" and out["media"]["files"] == 0, out.get("media"))
blob_sent = base64.b64decode(puts[1][3]["content"])
sig_sent = base64.b64decode(puts[2][3]["content"])
try:
    _priv.public_key().verify(base64.b64decode(sig_sent), blob_sent)
    check("...and the signature is a real ed25519 seal over exactly those bytes", True)
except Exception as exc:                                    # noqa: BLE001
    check("...and the signature is a real ed25519 seal over exactly those bytes", False, exc)
check("...on the branches update.toml names",
      puts[0][3]["branch"] == "cytron-dashboard" and puts[1][3]["branch"] == "main")
check("...and the answer reports both commits, source and copy apart",
      out["ok"] and out["commit"] == "abc123def456" and out["mirror_commit"] == "abc123def456"
      and out["mirror_error"] == "" and not out["unchanged"] and not out["mirror_unchanged"], out)
check("the token was sent, and only to GitHub's API, never echoed",
      all(c[2] in ("GET", "PUT") for c in calls) and "ghp_publish_test" not in json.dumps(out))


def source_held(target, path, tok, method="GET", body=None):
    """The source branch already has these bytes; the release repo has nothing."""
    calls.append((target["repo"], path, method, body))
    if method == "GET":
        if target["repo"] == "irivai-systor":
            blob = open(os.path.join(PCFG, "formats.toml"), "rb").read().replace(b"\r\n", b"\n")
            return {"sha": "s", "encoding": "base64",
                    "content": base64.b64encode(blob).decode()}
        raise http_error(404)
    return {"commit": {"sha": "0123456789abcdef", "html_url": ""}}


source_publish._gh = source_held
calls.clear()
out1 = source_publish.publish(tempfile.mkdtemp(), PCFG)
check("a source that already holds the file is 'unchanged' while the copy for boxes still commits",
      out1["unchanged"] is True and out1["commit"] == ""
      and out1["mirror_commit"] == "0123456789ab" and out1["mirror_unchanged"] is False, out1)
check("...so the panel can say which of the two happened, from what was remembered",
      source_publish.status(PCFG)["last"]["unchanged"] is True
      and source_publish.status(PCFG)["last"]["mirror_commit"] == "0123456789ab")


def mirror_refused(target, path, tok, method="GET", body=None):
    if method == "GET":
        raise http_error(404)
    if target["repo"] == "irivai-release":
        raise http_error(404)                    # the token does not cover it
    return {"commit": {"sha": "feedfacefeedface", "html_url": ""}}


source_publish._gh = mirror_refused
out2 = source_publish.publish(tempfile.mkdtemp(), PCFG)
check("a release repo the token cannot write to does not fail the publish",
      out2["ok"] and out2["commit"] == "feedfacefeed", out2)
check("...but is reported, in words, so the panel can warn that boxes cannot fetch yet",
      "cannot write to adamafiq0108/irivai-release@main" in out2["mirror_error"],
      out2["mirror_error"])
check("...and remembered beside the last publish",
      source_publish.status(PCFG)["last"].get("mirror_error") == out2["mirror_error"])
check("the publish settings tell the panel both targets",
      source_publish.status(PCFG)["mirror"]["repo"] == "irivai-release"
      and source_publish.status(PCFG)["source"]["repo"] == "irivai-systor")


def source_refused(target, path, tok, method="GET", body=None):
    raise http_error(403)


source_publish._gh = source_refused
try:
    source_publish.publish(tempfile.mkdtemp(), PCFG)
    check("a source repo the token cannot write to fails the publish", False)
except ValueError as exc:
    check("a source repo the token cannot write to fails the publish, saying so",
          "cannot write to adamafiq0108/irivai-systor" in str(exc), exc)

# ===========================================================================
# 10. the developer panels need the BOX to carry the tooling
# ===========================================================================
real_tier = auth.tier
auth.tier = lambda username, config_dir=None: auth.TIER_DEVELOPER
try:
    with_tools = auth.settings_tabs("dee", CFG, dev_tools=True)
    without = auth.settings_tabs("dee", CFG, dev_tools=False)
    unsaid = auth.settings_tabs("dee", CFG)
finally:
    auth.tier = real_tier
check("a developer on a box WITH the tooling gets System Templates and Licenses",
      "templates" in with_tools and "licenses" in with_tools, with_tools)
check("...and on a box WITHOUT it, neither -- a tab whose buttons all 404 is not a tab",
      "templates" not in without and "licenses" not in without
      and set(with_tools) - set(without) == {"templates", "licenses"}, without)
check("...unsaid reads deploy.toml, which fails closed",
      unsaid == without, unsaid)

hub_routes = read("core", "routes", "hub.py")
route = hub_routes[hub_routes.index('name="hub.update_templates"'):]
route = route[:route.index("def _template_listing")]
check("the check is an ADMIN action -- a deployment taking a release, not authoring",
      "_require_admin(" in route and "_require_developer" not in route)
check("...that lives in core/routes, so a customer install has it",
      "template_updates.check(" in route)
check("the shell is told whether the box carries the tooling",
      '"dev_tools"' in hub_routes and "app.state.dev_tools" in read("hub_server.py")
      and 'dev_tools=' in hub_routes)
shell = read("frontend", "src", "components", "HubShell.jsx")
check("the shell draws the developer panels only where the routes exist",
      "user?.dev_tools" in shell and "devTools" in shell
      and "devOnly || devTools" in shell and "devTools={devTools}" in shell)
wizard = read("frontend", "src", "components", "CreateSystemWizard.jsx")
check("the create wizard has the button in its header, top right, on every step",
      "/api/hub/templates/update" in wizard
      and "Check for template updates" in wizard
      and wizard.rfind("Check for template updates") < wizard.index("<ol className")
      and wizard.count("Check for template updates") == 2)
check("...with the check's status as the line under the title, in place of the demo note",
      "The Demo System stays untouched" not in wizard
      and wizard.index("isDev ? checkNote") < wizard.index("<ol className")
      and "not changed" in wizard)
check("...tells the developer hub apart, shows a failed check as a failure, and needs a token",
      "d.authoring" in wizard and "last.ok === false" in wizard
      and "updates.token === false" in wizard and "removed_here" in wizard)
panel = read("frontend", "src", "components", "TemplatesPanel.jsx")
check("both buttons are developer tier only, a customer sees neither",
      "isDev && (" in wizard and "isDev && (" in panel
      and "const isDev = !!user?.is_developer" in wizard)
check("...and a customer's wizard line says templates come with each software update",
      "Templates are updated with each software update." in wizard)
check("the System Templates page has the same button",
      "/api/hub/templates/update" in panel and "Check for updates" in panel)
check("...and its Publish dialog names both repositories and says nothing commits on its own",
      "for boxes to fetch" in panel and "Nothing is" in panel and "mirror_error" in panel)
check("...reports the source commit and the copy for boxes as two facts, and a missing signing key",
      "mirror_unchanged" in panel and "mirror_commit" in panel and "!ready.signing" in panel
      and "d.authoring" in panel)
# A stored token is never shown back, so once one was set the field vanished
# and a token pasted wrong could only be replaced from a shell on the box --
# which is exactly what happened on 2026-09-24.
check("...and a stored token can be REPLACED from the dialog, both dialogs",
      panel.count("setReplacing(true)") == 2 and "Use a different token" in panel)
check("update.toml declares where boxes read from",
      "[templates]" in read("config", "update.toml")
      and 'path = "templates/formats.toml"' in read("config", "update.toml"))

print()
if bad:
    print("%d check(s) failed" % bad)
    raise SystemExit(1)
print("ALL OK")
