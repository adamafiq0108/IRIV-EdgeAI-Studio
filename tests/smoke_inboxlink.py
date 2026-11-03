# A notice says where to go and do something about it.
#
# THE PROBLEM. "Reset link could not be sent - check the Email settings" is
# true and leaves the reader to find Email among nine settings tabs. The
# notice knows which page it is about; it was the only thing in the room that
# did, and it kept it to itself.
#
# WHAT IS PINNED. That the destination is CHECKED rather than passed through
# -- a notice is rendered as something a person clicks, and it is posted from
# routes and services all over the tree, so "nobody would post a bad one" is
# not a check. That a bad destination costs the link and never the notice. And
# that the names the reader sees exist for every destination the server will
# accept, since those live in two files that never import each other.
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

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


# -- what is accepted -----------------------------------------------------------
row = inbox.post("ana", "reset_failed", "T", "B", CONFIG,
                 page="settings", tab="mail")
check("a notice can name a settings tab",
      (row["page"], row["tab"]) == ("settings", "mail"), row)

row = inbox.post("ana", "k", "T", "B", CONFIG, page="health")
check("...or a page with no tabs", (row["page"], row["tab"]) == ("health", ""))

# -- what is refused, and what it costs -----------------------------------------
for label, kw in (
        ("a page that does not exist", {"page": "wherever"}),
        ("a path instead of a page", {"page": "/etc/passwd"}),
        ("a full URL", {"page": "https://example.com"}),
        ("a tab on a page that has none", {"page": "health", "tab": "mail"}),
        ("a tab that does not exist", {"page": "settings", "tab": "nope"}),
        ("a tab with no page at all", {"tab": "mail"})):
    r = inbox.post("ana", "k", "Still arrives", "B", CONFIG, **kw)
    check(f"the link is dropped for {label}", not r["page"] or not r["tab"],
          r)
    check(f"...and the notice still arrives - {label}",
          r["title"] == "Still arrives")

r = inbox.post("ana", "k", "T", "B", CONFIG, page="wherever")
check("an unknown page leaves no page at all, not a broken one", r["page"] == "")

# -- a notice written before this existed ---------------------------------------
#
# Filled in on READ rather than by migrating the stored file: notices expire on
# their own, and a reader that copes costs less than a rewrite that cannot be
# undone.
inbox.post("bob", "k", "Old one", "B", CONFIG)
raw = inbox._rows(inbox._load(CONFIG))
for stored in raw:
    stored.pop("page", None)
    stored.pop("tab", None)
seen = inbox.for_user("bob", False, CONFIG)
check("an older notice with no page reads back as no link, never undefined",
      all(r.get("page") == "" and r.get("tab") == "" for r in seen), seen)
check("...and is still delivered", any(r["title"] == "Old one" for r in seen))

# -- the producers actually say where -------------------------------------------
AUTH = read("core", "routes", "auth.py")
PANEL = read("core", "services", "panel.py")
check("a reset that could not be emailed points at Settings -> Email",
      re.search(r'"reset_failed".{0,400}?page="settings", tab="mail"',
                AUTH, re.S) is not None)
for kind, src in (("reset_request", AUTH), ("reset_approved", AUTH),
                  ("password_changed", AUTH), ("reset_request", PANEL)):
    check(f"the {kind} notice points at a page",
          re.search(rf'"{kind}".{{0,600}}?page="settings"', src, re.S) is not None)

# -- and every destination has a NAME the reader can read ------------------------
#
# The allow-list is in Python and the labels are in JSX; neither imports the
# other, so a page added to one and not the other produces a link that says
# nothing, or none at all. Cheap to pin, invisible otherwise.
UI = read("frontend", "src", "components", "NotificationCenter.jsx")
labels = set(re.findall(r"(\w+):\s*\"", UI.split("const PAGE_LABEL")[1]
                        .split("};")[0]))
check("every page the server accepts has a label in the bell",
      set(inbox.PAGES) <= labels, sorted(set(inbox.PAGES) - labels))
tabs = set(re.findall(r"(\w+):\s*\"", UI.split("const TAB_LABEL")[1]
                      .split("};")[0]))
check("every settings tab the server accepts has one too",
      set(inbox.TABS) <= tabs, sorted(set(inbox.TABS) - tabs))

# -- the shell can follow it -----------------------------------------------------
SHELL = read("frontend", "src", "components", "HubShell.jsx")
check("the bell is given a way to navigate", "onGo={go}" in SHELL)
check("...and the settings panel remounts, so a reader already on Settings "
      "does not click and watch nothing happen",
      "key={goto}" in SHELL and "setGoto" in SHELL)
check("...through the same stored key the tab strip already uses",
      'localStorage.setItem("hub.settings.tab"' in SHELL
      and 'LAST_TAB = "hub.settings.tab"'
      in read("frontend", "src", "components", "SettingsHub.jsx"))
check("the popover closes on the way, rather than hanging over the page it "
      "just opened", "setOpen(false); onGo(" in UI)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
