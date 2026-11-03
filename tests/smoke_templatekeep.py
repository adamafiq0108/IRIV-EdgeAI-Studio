# Templates a "Check for template updates" installed survive an update.
#
# Reported 2026-09-29 on .116: the check added AI Smart Parking, then the box
# went back to the four templates it shipped with. The check writes
# config/formats.toml, and that file belongs to the RELEASE: an update swaps
# in a new release directory and carries only the *.local.toml files across.
# So every update quietly undid the check. The check now also records what it
# installed in templates_installed.local.toml (carried like every other local
# file), and the hub puts those templates back when it starts.
#
# The other half, pinned just as hard: a NEWER release wins. When the release
# itself changed or removed a template after the check installed it, the
# release is the newer word and the record is dropped, or an old check would
# hold a box on stale templates for ever.
import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                          # noqa: E402
from core.services import provision                              # noqa: E402
from core.services import template_updates as tu                 # noqa: E402

bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def release(mode="customer"):
    """A fresh release's config dir: its own formats.toml, a deploy marker."""
    d = tempfile.mkdtemp()
    io.open(os.path.join(d, "deploy.toml"), "w", encoding="utf-8").write(
        f'mode = "{mode}"\n')
    config.load.cache_clear()
    provision.save_format("counting", {"label": "People Counting", "widgets": [],
                                       "draft": False}, d)
    provision.save_format("blank", {"label": "Blank", "widgets": [],
                                    "draft": False}, d)
    return d


def update(src):
    """What apply_update.carry_config does: a new release, local files over."""
    dest = release()
    for name in os.listdir(src):
        if name.endswith(".local.toml"):
            shutil.copy2(os.path.join(src, name), os.path.join(dest, name))
    config.load.cache_clear()
    return dest


def published(formats):
    return (config._toml_dump({"formats": formats}) + "\n").encode("utf-8")


def labels(cfg):
    return {k: v.get("label") for k, v in provision._formats_overlay(cfg).items()}


UPSTREAM = {
    "counting": {"label": "People Counting v2", "widgets_json": "[]"},
    "parking": {"label": "AI Smart Parking", "widgets_json": "[]"},
}
real_fetch = tu._fetch
tu._fetch = lambda config_dir=None: published(UPSTREAM)

try:
    box = release()
    r = tu.check(box)
    check("the check installs the new template and the changed one",
          [t["key"] for t in r["added"]] == ["parking"]
          and [t["key"] for t in r["updated"]] == ["counting"], r)
    check("...and records them outside the release",
          os.path.isfile(os.path.join(box, "templates_installed.local.toml")))

    # -- an update: new release directory, only the local files carried -----
    box2 = update(box)
    check("an update alone would put the box back on what it shipped with",
          "parking" not in labels(box2))
    back = tu.restore(box2)
    check("the hub start puts the installed templates back",
          sorted(back) == ["counting", "parking"]
          and labels(box2).get("parking") == "AI Smart Parking"
          and labels(box2).get("counting") == "People Counting v2", labels(box2))
    check("...not as drafts an admin cannot see",
          not provision._formats_overlay(box2)["parking"].get("draft"))
    check("a plain restart after that has nothing to do", tu.restore(box2) == [])
    r2 = tu.check(box2)
    check("...and a second check finds nothing new",
          not r2["added"] and not r2["updated"], r2)

    # -- a newer release changed a template the check had installed ---------
    box3 = update(box2)
    provision.save_format("counting", {"label": "People Counting v3",
                                       "widgets": [], "draft": False}, box3)
    back = tu.restore(box3)
    check("a release that changed the template wins over the older check",
          back == ["parking"] and labels(box3)["counting"] == "People Counting v3",
          (back, labels(box3)))
    check("...and the record for it is dropped",
          "counting" not in tu._installed(box3))

    # -- hidden here stays hidden ------------------------------------------
    box4 = update(box3)
    f = dict((config.load(provision.FORMATS_FILE, box4) or {}).get("formats") or {})
    f["parking"] = {"label": "AI Smart Parking", "hidden": True}
    provision._write_formats(f, box4)
    check("a template the box hid is not brought back", tu.restore(box4) == []
          and provision._formats_overlay(box4)["parking"].get("hidden"))

    # -- the developer hub installs nothing, so restores nothing ------------
    dev = release("dev")
    shutil.copy2(os.path.join(box2, "templates_installed.local.toml"),
                 os.path.join(dev, "templates_installed.local.toml"))
    config.load.cache_clear()
    check("the developer hub never restores", tu.restore(dev) == []
          and "parking" not in labels(dev))
finally:
    tu._fetch = real_fetch

check("the hub restores at start", "template_updates.restore(config_dir)"
      in read("hub_server.py"))
check("the record is a *.local.toml, which every update carries",
      tu.INSTALLED_FILE + ".local.toml" == "templates_installed.local.toml"
      and 'name.endswith(".local.toml")' in read("scripts", "apply_update.py"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
