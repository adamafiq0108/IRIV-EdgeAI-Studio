# After an update, the admins are told what changed -- in the Inbox.
#
# The Update card shows the release notes BEFORE the operator presses Update.
# Once the box is back on the new version the card reads "up to date" and the
# notes are gone with it: nothing on the box said the update happened or what
# it brought. So the first hub start on a new version leaves one notice for
# master and the developer, "Updated to vX", carrying the same grouped notes
# the card showed, and points at the System tab.
#
# Pinned: once per version, not per restart; nothing on a source checkout or
# a box not laid out for OTA; nothing on a fresh install (no earlier release
# beside this one); the notes come from the check cache when it names the
# running version and a plain pointer otherwise; the body is allowed the room
# the notes need; the bell draws line breaks.
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import update as up                       # noqa: E402
from core.services import inbox                              # noqa: E402

CONFIG = tempfile.mkdtemp()
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def notices():
    return [r for r in inbox.for_user("iriv", True, CONFIG) if r.get("kind") == "update"]


NOTES = ("Workflow:\n- a line has its own Crossing threshold\n"
         "- a Data Table can write one row per crossing\n\n"
         "Security:\n- finishing first run setup always replaces the shipped password\n")

# The box: laid out for OTA, with an earlier release beside the running one.
up.ota_ready = lambda: True
up.previous_version = lambda: "1.0.6"
with open(up._p(up.CHECK_FILE[up.CHANNEL_STABLE]), "w", encoding="utf-8") as f:
    json.dump({"latest": "v1.0.7", "notes": NOTES}, f)

# -- 1. the first start on a new version posts once ---------------------------
row = up.announce_installed(CONFIG, running="1.0.7")
check("the first start after an update posts a notice to the admins",
      row is not None and row["to"] == inbox.ADMINS and row["kind"] == "update", row)
check("...titled with the version now running", row and row["title"] == "Updated to v1.0.7", row)
check("...carrying the release notes the card showed, headings and lines intact",
      row and row["body"] == NOTES.strip(), row and row["body"])
check("...and carrying no page link: the notice is the news, not a pointer",
      row and row["page"] == "" and row["tab"] == "", row)
bell_src = read("frontend", "src", "components", "NotificationCenter.jsx")
check("...and the bell draws no Go to link on an update notice, old ones included",
      bell_src.count('kind !== "update"') >= 2)
check("...visible to an admin reading the inbox", len(notices()) == 1, notices())
check("a second hub start on the same version posts nothing more",
      up.announce_installed(CONFIG, running="1.0.7") is None and len(notices()) == 1)
check("...and the version is remembered on the store, not in memory",
      json.load(open(up._p(up.ANNOUNCED_FILE), encoding="utf-8"))["version"] == "1.0.7")

# -- 1b. the marker alone is not enough: the notice has to be THERE ------------
# The inbox lives in config/ inside the release and the applier carries the
# live release's config into the one going live, so a notice written between
# two apply runs is copied over and lost -- while the marker, which lives on
# the store, survives. The box then believed it had announced a version whose
# notice no longer existed. Seen on .116 on 2026-09-25.
for r in notices():
    inbox.delete("iriv", r["id"], True, CONFIG)
check("the notice is gone, the marker still says 1.0.7", not notices()
      and json.load(open(up._p(up.ANNOUNCED_FILE), encoding="utf-8"))["version"] == "1.0.7")
row = up.announce_installed(CONFIG, running="1.0.7")
check("...so the next start posts it again rather than trusting the marker",
      row and row["title"] == "Updated to v1.0.7" and len(notices()) == 1, row)
check("...and having posted it, the start after that is quiet again",
      up.announce_installed(CONFIG, running="1.0.7") is None and len(notices()) == 1)

# -- 2. the next version is announced again -----------------------------------
with open(up._p(up.CHECK_FILE[up.CHANNEL_STABLE]), "w", encoding="utf-8") as f:
    json.dump({"latest": "v1.0.9", "notes": "Logs: something"}, f)
row = up.announce_installed(CONFIG, running="1.0.8")
check("a later version is announced in its turn", row and row["title"] == "Updated to v1.0.8", row)
check("...and when the cache names some OTHER version the notice still says where to look",
      row and "System tab" in row["body"] and "Logs:" not in row["body"], row and row["body"])

# -- 2b. a commit message is not release notes ----------------------------------
# The v1.0.7 release body once carried the tag's COMMIT message, and the notice
# copied it into every admin's inbox: "fix: remove_orphan checks the slug ...
# Found by Pipi ... Co-Authored-By". The notice shows release notes or a
# pointer, never a developer's paragraph.
COMMIT = ("fix: remove_orphan checks the slug before it builds a path\n\n"
          "The route hands the URL segment straight to remove_orphan. \"..\" is not a\n"
          "claimed slug, so the package join resolved to the store itself.\n\n"
          "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\n")
check("a commit message cleans to nothing", up.clean_notes(COMMIT) == "", up.clean_notes(COMMIT))
check("real notes clean to themselves, trailers gone",
      up.clean_notes(NOTES + "Co-Authored-By: x <x@y>\n") == NOTES.strip())
check("a version subject ahead of real notes is dropped",
      up.clean_notes("chore: version 1.0.7\n" + NOTES) == NOTES.strip())
check("one-line notes count as notes",
      up.clean_notes("Update: applying a new version no longer fails") != "")
with open(up._p(up.CHECK_FILE[up.CHANNEL_STABLE]), "w", encoding="utf-8") as f:
    json.dump({"latest": "v1.0.9", "notes": COMMIT}, f)
# Asking GitHub again goes online, so it happens only while "Check for
# updates automatically" is on (off by default; smoke_otaautocheck.py pins
# that the off box stays quiet here).
from core.services import update_auto                          # noqa: E402
update_auto.set_enabled(True, CONFIG)
calls = []
up.check = lambda force=False, config_dir=None, channel=None: calls.append(channel)
row = up.announce_installed(CONFIG, running="1.0.9")
check("a cache holding a commit message makes the notice fall back to the pointer",
      row and "System tab" in row["body"] and "Pipi" not in row["body"], row and row["body"])
check("...after asking GitHub once more for what changed",
      calls == list(up.CHANNELS), calls)
# ...and when the re-check brings the real notes, they are used
with open(up._p(up.CHECK_FILE[up.CHANNEL_STABLE]), "w", encoding="utf-8") as f:
    json.dump({"latest": "v1.0.6", "notes": ""}, f)


def refetch(force=False, config_dir=None, channel=None):
    if channel == up.CHANNEL_STABLE:
        with open(up._p(up.CHECK_FILE[up.CHANNEL_STABLE]), "w", encoding="utf-8") as f:
            json.dump({"latest": "v1.1.0", "notes": NOTES}, f)


up.check = refetch
row = up.announce_installed(CONFIG, running="1.1.0")
check("a stale cache is refreshed and the fresh notes are what the notice carries",
      row and row["body"] == NOTES.strip(), row and row["body"])

# -- 3. what is not an update ---------------------------------------------------
n0 = len(notices())
os.remove(up._p(up.ANNOUNCED_FILE))
up.previous_version = lambda: ""
check("a fresh install (no earlier release beside this one) posts nothing",
      up.announce_installed(CONFIG, running="2.0.0") is None and len(notices()) == n0)
check("...but records the version, so the NEXT one is still announced",
      json.load(open(up._p(up.ANNOUNCED_FILE), encoding="utf-8"))["version"] == "2.0.0")
up.previous_version = lambda: "2.0.0"
up.ota_ready = lambda: False
check("a source checkout or a box not laid out for OTA posts nothing",
      up.announce_installed(CONFIG, running="2.0.1") is None and len(notices()) == n0)

# -- 4. the body is allowed the room the notes need ----------------------------
long_body = "x" * 5000
r = inbox.post("iriv", "update", "t", long_body, CONFIG, limit=inbox.BODY_MAX_LONG)
check("an update notice may carry the whole release notes", len(r["body"]) == inbox.BODY_MAX_LONG, len(r["body"]))
r = inbox.post("iriv", "note", "t", long_body, CONFIG)
check("...while an ordinary notice keeps its short cap", len(r["body"]) == inbox.BODY_MAX, len(r["body"]))
r = inbox.post("iriv", "note", "t", long_body, CONFIG, limit=10 ** 9)
check("...and nothing may ask for more than the long cap", len(r["body"]) == inbox.BODY_MAX_LONG)

# -- 4b. the restart that finishes an update -----------------------------------
# The applier swaps the tree and restarts the hub and its masters; it does not
# restart the detection apps, the media server, the front panel or the root
# helpers. Until the box comes up again it is part new version and part the one
# it had, which is invisible and is what "I updated and nothing changed" is.
import json as _json                                        # noqa: E402

def _status(raw):
    with open(up._p("status.json"), "w", encoding="utf-8") as f:
        _json.dump(raw, f)

_status({"phase": "done", "started": 10 ** 12})
check("a finished update this box has not restarted since is reported",
      up.reboot_pending() is True
      if os.path.exists("/proc/uptime") else up.reboot_pending() is False,
      "started far in the future, so no boot can be after it")
_status({"phase": "done", "started": 1})
check("...and once the box has booted since, it is not",
      up.reboot_pending() is False, "booted long after a job started at epoch 1")
_status({"phase": "failed", "started": 10 ** 12})
check("an update that did NOT finish never asks for a restart",
      up.reboot_pending() is False)
_status({"phase": "done"})
check("...nor does one with no start time to compare against",
      up.reboot_pending() is False)
os.remove(up._p("status.json"))
check("the state the page reads carries it",
      "reboot_pending" in up.status())

panel = read("frontend", "src", "components", "UpdatePanel.jsx")
check("the page asks for the restart when the update finishes",
      "offerReboot(s.version)" in panel and "Restart the box to finish?" in panel
      and '"/api/hub/panel/power", { action: "reboot" }' in panel)
check("...offering Later, because the operator is standing at a running line",
      'cancelLabel: "Later"' in panel)
check("...and keeps offering it, from the box's own state, until it is done",
      "status?.reboot_pending" in panel
      and "Restart to finish the update" in panel)

# -- 5. the hub asks at start-up, and the bell draws the lines -------------------
hub = read("hub_server.py")
applier = read("scripts", "apply_update.py")
check("carrying config onto itself is skipped, not raised",
      "os.path.abspath(s) == os.path.abspath(d)" in applier)
check("the hub posts the notice at start-up and never dies on it",
      "update_service.announce_installed(config_dir)" in hub
      and "could not post the update notice" in hub)
bell = read("frontend", "src", "components", "NotificationCenter.jsx")
check("the bell keeps the notes' line breaks",
      "whitespace-pre-line" in bell.split("{m.body && (")[1][:200])
check("a long message shows its first lines and Read more, which opens a dialog",
      "Read more" in bell and "PREVIEW_CHARS" in bell and "setReading(m)" in bell
      and "<Dialog open={!!reading}" in bell)
check("an update notice always offers Read more, and a notice with no list fetches the release notes",
      'm.kind === "update") && (' in bell and 'api.get("/api/hub/update?channel=stable")' in bell
      and "fetchedNotes || reading.body" in bell)
check("...and an update notice is laid out by area in that dialog, like the Update card",
      "releaseNoteGroups(reading.body)" in bell and 'reading.kind === "update"' in bell)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
