# A check that cannot reach GitHub says why in plain words; the raw failure
# never reaches `error`.
#
# Board report: the System tab read "could not reach GitHub (<urlopen error
# [Errno 113] No route to host>)". fa35fb11 / 8a53fcac worded the INBOX
# notice by cause, but check() still wrote f"could not reach GitHub ({exc})"
# into `error`, which is what the panel's note and the "Check now" answer
# print. Pinned:
#   1. errno 113 / 101 / 110 / 111 and getaddrinfo's -2 / -3 / -5, a socket
#      timeout and a TimeoutError all read "No internet connection - check
#      the cable, Wi-Fi or the default route"; the exception's own text rides
#      in `details`
#   2. a URLError whose reason is a STRING carrying "[Errno N]" (how the
#      fixtures, and some urllib paths, spell it) maps the same way
#   3. no `urlopen error` / `Errno` text can reach `error` for any of them
#   4. a failure of another kind (TLS) is "Could not reach GitHub - the box
#      will try again later", still with no raw text in `error`
#   5. public_state() carries `details`, and the inbox notice's detail is
#      the raw text (the hover), its body the worded sentence
#   6. the worded sentences still count as failed checks (the streak)
import os
import socket
import ssl
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
RAISE = {"exc": None}


def fake_api(path, tok, accept="", headers=None, config_dir=None, channel=None):
    raise RAISE["exc"]


upd._api = fake_api
upd.token = lambda config_dir=None, channel=None: "ghp_fake"


def run(exc):
    RAISE["exc"] = exc
    upd._write_json(upd._p(upd.CHECK_FILE["stable"]), {})
    return upd.check(config_dir=CFG, channel="stable")


def raw_free(error):
    return "urlopen error" not in error and "Errno" not in error \
        and "<" not in error and ">" not in error


# -- 1. the offline family -------------------------------------------------------
OFFLINE = [
    ("errno 113 No route to host (a default route at a dead gateway)",
     urllib.error.URLError(OSError(113, "No route to host"))),
    ("errno 101 Network is unreachable (no default route)",
     urllib.error.URLError(OSError(101, "Network is unreachable"))),
    ("errno 110 Connection timed out",
     urllib.error.URLError(OSError(110, "Connection timed out"))),
    ("errno 111 Connection refused",
     urllib.error.URLError(OSError(111, "Connection refused"))),
    ("getaddrinfo -2 Name or service not known",
     urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))),
    ("getaddrinfo -3 Temporary failure in name resolution",
     urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))),
    ("getaddrinfo -5 No address associated with hostname",
     urllib.error.URLError(socket.gaierror(-5, "No address associated with hostname"))),
    ("a socket timeout", urllib.error.URLError(socket.timeout("timed out"))),
    ("a bare TimeoutError", TimeoutError("timed out")),
    ("a bare OSError 113", OSError(113, "No route to host")),
]
for label, exc in OFFLINE:
    out = run(exc)
    check(f"1 {label} reads as no internet connection",
          out["error"] == upd.CHECK_OFFLINE, out["error"])
    check(f"1 ...with the raw failure in details",
          str(exc) in out["details"] or str(getattr(exc, "reason", "")) in out["details"],
          out["details"])
    check(f"3 ...and nothing raw in error", raw_free(out["error"]), out["error"])

# -- 2. a string reason spelling the errno --------------------------------------
for text in ("[Errno -3] Temporary failure in name resolution",
             "[Errno 113] No route to host"):
    out = run(urllib.error.URLError(text))
    check(f"2 URLError('{text}') maps by the errno in its text",
          out["error"] == upd.CHECK_OFFLINE and raw_free(out["error"])
          and text in out["details"], out)

# -- 4. another kind of failure ---------------------------------------------------
tls = urllib.error.URLError(ssl.SSLError(1, "CERTIFICATE_VERIFY_FAILED"))
out = run(tls)
check("4 a TLS failure is 'could not reach GitHub', not 'no internet'",
      out["error"] == upd.CHECK_UNREACHABLE, out["error"])
check("4 ...with nothing raw in error and the SSL text in details",
      raw_free(out["error"]) and "CERTIFICATE_VERIFY_FAILED" in out["details"], out)
out = run(ValueError("Expecting value: line 1 column 1"))
check("4 a body that is not JSON is the same plain sentence",
      out["error"] == upd.CHECK_UNREACHABLE and "Expecting value" in out["details"], out)

# -- 5. the public shape and the notice ------------------------------------------
run(urllib.error.URLError(OSError(113, "No route to host")))
pub = upd.public_state(None, "stable", CFG)
check("5 public_state carries details beside the worded error",
      pub["error"] == upd.CHECK_OFFLINE and "No route to host" in pub["details"], pub)
# Two in a row posts the notice: body worded, detail raw.
upd.check(config_dir=CFG, channel="stable")
n = [r for r in inbox.for_user("admin", True, CFG) if r.get("kind") == "update_check"]
check("5 the inbox notice keeps its worded body",
      len(n) == 1 and n[0]["body"] == upd.CHECK_NOTICE_BODY, n)
check("5 ...and the raw text only as its detail",
      n and "No route to host" in n[0].get("detail", "")
      and "Errno" not in n[0]["body"], n)

# -- 6. still a failed check ------------------------------------------------------
check("6 both worded sentences count as a failed check",
      upd._check_failed(upd.CHECK_OFFLINE) and upd._check_failed(upd.CHECK_UNREACHABLE))

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
