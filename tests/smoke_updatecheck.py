# "Could not check for updates" is an ordinary notice, raised by a REAL
# outage, and it clears itself.
#
# Beta feedback: the line sat on the Important tab and nothing could clear
# it -- it was derived from the last check's error, so one Wi-Fi blip put a
# red line there until the next successful check, with GitHub's raw error as
# the sentence. Pinned:
#   1. one failed check: no notice; the second in a row: ONE inbox notice,
#      plain words, the raw error only as `detail` (hover)
#   2. more failures do not pile up more notices
#   3. the next check that gets an answer withdraws it, and the count resets
#   4. an expired token and "no release yet" are answers, not outages
#   5. the Important tab no longer derives the line; Messages shows the
#      detail on hover
#   6. the sentence follows the cause: only a network failure is "no internet
#      connection"; a 404 needs the settings or token looked at; a 403/429
#      is GitHub limiting requests
#   7. removing the update token mid-outage withdraws the notice
#   8. the streak count and the notice id live in the check file, so a hub
#      RESTART mid-outage neither posts a second notice nor forgets the first
#      -- the next good check after the restart still withdraws it
#   9. an admin who DISMISSES the notice mid-outage is not nagged again by the
#      same outage (the checker runs every few hours for days)
#  10. a NEW outage after a good check is a new notice
# (8-10 were smoke_updatecheck_streak.py; merged here, same fixtures.)
import importlib
import io
import os
import sys
import tempfile
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import inbox                                # noqa: E402
from core.services import update as upd                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()


class _Resp:
    headers = {"ETag": 'W/"x"'}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        return b'[{"tag_name": "v9.9.9", "body": "", "assets": [], "draft": false, "prerelease": false}]'


MODE = {"m": "down", "code": None}


def fake_api(path, tok, accept="", headers=None, config_dir=None, channel=None):
    if MODE["m"] == "down":
        raise urllib.error.URLError("[Errno -3] Temporary failure in name resolution")
    if MODE["m"] == "token":
        raise urllib.error.HTTPError(path, 401, "Unauthorized", {}, None)
    return _Resp()


upd._api = fake_api
upd.token = lambda config_dir=None, channel=None: "ghp_fake"
upd._write_json(upd._p(upd.CHECK_FILE["stable"]), {})


def notices():
    return [r for r in inbox.for_user("admin", True, CFG)
            if r.get("kind") == "update_check"]


upd.check(config_dir=CFG, channel="stable")
check("1 one failed check: no notice yet (a blip)", notices() == [], notices())
upd.check(config_dir=CFG, channel="stable")
n = notices()
check("1 the second in a row: one notice", len(n) == 1, n)
check("1 ...in plain words",
      n and n[0]["title"] == "Could not check for updates"
      and n[0]["body"] == ("Could not check for updates - no internet "
                           "connection; the box will try again later"), n)
check("1 ...the raw error only as its detail (hover)",
      n and "name resolution" in n[0].get("detail", "")
      and "name resolution" not in n[0]["body"], n)
check("1 ...an ordinary notice the reader can delete",
      n and inbox.delete("admin", n[0]["id"], is_admin=True, config_dir=CFG)
      is not False)
# Put it back the way a real streak would have it, for the clearing check.
upd._write_json(upd._p(upd.CHECK_FILE["stable"]), {})
for _ in range(4):
    upd.check(config_dir=CFG, channel="stable")
check("2 a longer outage is still ONE notice", len(notices()) == 1, notices())
MODE["m"] = "up"
upd.check(config_dir=CFG, channel="stable")
cached = upd._read_json(upd._p(upd.CHECK_FILE["stable"]))
check("3 the next check that gets through withdraws it",
      notices() == [] and cached.get("check_fails") == 0
      and not cached.get("check_notice"), (notices(), cached))
MODE["m"] = "token"
for _ in range(3):
    upd.check(config_dir=CFG, channel="stable")
check("4 an expired token is not an outage (it has its own notice)",
      notices() == [], notices())
check("4 ...nor is 'no release yet' or a missing token",
      not upd._check_failed("no release has been published on this channel yet")
      and not upd._check_failed("no update token is configured for this box")
      and upd._check_failed("could not reach GitHub (x)"))

# -- 6. the sentence follows the CAUSE -----------------------------------------
def streak(mode, code=None):
    """Two failed checks of one kind from a clean state; the notice body."""
    MODE["m"], MODE["code"] = mode, code
    upd._write_json(upd._p(upd.CHECK_FILE["stable"]), {})
    for r in notices():
        inbox.delete("admin", r["id"], is_admin=True, config_dir=CFG)
    upd.check(config_dir=CFG, channel="stable")
    upd.check(config_dir=CFG, channel="stable")
    got = notices()
    return got[0]["body"] if len(got) == 1 else got


real_fake = fake_api


def fake_api2(path, tok, accept="", headers=None, config_dir=None, channel=None):
    if MODE["m"] == "http":
        raise urllib.error.HTTPError(path, MODE["code"], "x", {}, None)
    return real_fake(path, tok, accept, headers, config_dir, channel)


upd._api = fake_api2
check("6 a network failure says 'no internet connection'",
      streak("down") == upd.CHECK_NOTICE_BODY
      and "no internet connection" in upd.CHECK_NOTICE_BODY)
body = streak("http", 404)
check("6 a 404 (release repo / token access) is a setup fault, not 'try later'",
      body == ("Could not check for updates - the release settings or the "
               "update token need attention"), body)
for code in (403, 429):
    body = streak("http", code)
    check(f"6 a {code} says GitHub is limiting requests",
          body == "GitHub is limiting requests; the box will try again later",
          body)
body = streak("http", 502)
check("6 any other answer is neither 'no internet' nor a setup fault",
      "no internet" not in body and "attention" not in body, body)

# -- 7. removing the token mid-outage withdraws the notice ----------------------
streak("down")
check("7 (an outage notice is up)", len(notices()) == 1, notices())
upd.token = lambda config_dir=None, channel=None: ""
st = upd.check(config_dir=CFG, channel="stable")
cached = upd._read_json(upd._p(upd.CHECK_FILE["stable"]))
check("7 the token is removed: the notice is withdrawn, not left for ever",
      notices() == [] and not cached.get("check_notice")
      and cached.get("check_fails") == 0, (notices(), cached))
check("7 ...and the check answers 'no token' as before",
      st.get("error") == "no update token is configured for this box", st)
upd.token = lambda config_dir=None, channel=None: "ghp_fake"

src = io.open(os.path.join(ROOT, "frontend", "src", "components",
                           "NotificationCenter.jsx"), encoding="utf-8").read()
important = src[src.index("function buildNotices("):src.index("function Empty(")]
check("5 the Important tab no longer derives 'Could not check for updates'",
      "Could not check for updates" not in important
      and "token_expired" in important)
check("5 Messages shows a notice's detail on hover",
      "title={m.detail || undefined}" in src)

# -- 8. a restart in the middle of an outage ----------------------------------
def wire(module):
    module._api = fake_api2
    module.token = lambda config_dir=None, channel=None: "ghp_fake"


def run(n=1):
    for _ in range(n):
        upd.check(config_dir=CFG, channel="stable")


upd._write_json(upd._p(upd.CHECK_FILE["stable"]), {})
for r in notices():
    inbox.delete("admin", r["id"], is_admin=True, config_dir=CFG)
MODE["m"], MODE["code"] = "down", None
run(2)
first = notices()
check("8 an outage: one notice at the second failed check", len(first) == 1, first)
upd = importlib.reload(upd)          # the hub restarts: module state is gone
wire(upd)
run(3)
check("8 after a restart the same outage posts no second notice",
      [r["id"] for r in notices()] == [r["id"] for r in first], notices())
MODE["m"] = "ok"
run()
check("8 ...and the first good check after the restart withdraws it",
      notices() == [], notices())

# -- 9/10. dismissed mid-outage; the next outage ----------------------------------
MODE["m"] = "down"
run(2)
got = notices()
check("10 a new outage after a good check is a new notice", len(got) == 1, got)
inbox.delete("admin", got[0]["id"], True, CFG)
run(6)
check("9 dismissed mid-outage: the same outage does not post it again",
      notices() == [], notices())
MODE["m"] = "ok"
run()
MODE["m"] = "down"
run(2)
check("10 ...the next outage (after a good check) does", len(notices()) == 1,
      notices())

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all update-check notice checks passed")
