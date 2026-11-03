# The sending mailbox takes an APP password only (Tok Aba, 2026-10-14).
#
# Pinned on the server (core/services/mail_accounts.py, core/routes/auth.py):
#   1. clean_app_password: a Gmail app password (16 lowercase letters, with or
#      without the spaces Google shows) is accepted and stored WITHOUT spaces;
#      a normal-looking password for Gmail is refused with the plain message,
#      which never quotes what was typed; a blank server is Gmail; any other
#      server takes any non-empty value as typed
#   2. save_account: a new Gmail account with a normal password is refused and
#      nothing is written; with an app password it is stored without spaces
#   3. an account saved BEFORE the rule keeps working: an edit with a blank or
#      masked password keeps the stored one and is not re-checked; an edit
#      that CHANGES the password is checked
#   4. the first-run setup checks it before writing anything (_check_mailbox)
#   5. the frontend helper mirrors the server's rule and words; no em dash
#
#   py -3 tests/smoke_apppassword.py
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import mail_accounts as m                       # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def refused(fn, *a):
    try:
        fn(*a)
    except ValueError as e:
        return str(e)
    return ""


# 1 ---------------------------------------------------------------------------
check("1. a Gmail app password with spaces is stored without them",
      m.clean_app_password("abcd efgh ijkl mnop", "smtp.gmail.com") == "abcdefghijklmnop")
check("...and without spaces as it is",
      m.clean_app_password("abcdefghijklmnop", "smtp.googlemail.com") == "abcdefghijklmnop")
for pw in ("MyPassw0rd!", "qwer tyui", "ABCD EFGH IJKL MNOP", "abcdefghijklmnopq", "abcd-efgh-ijkl-mnop"):
    msg = refused(m.clean_app_password, pw, "smtp.gmail.com")
    check(f"...{pw!r} for Gmail is refused, the message never quoting it",
          msg == m.APP_PASSWORD_MESSAGE and pw not in msg, msg)
check("...a blank server is Gmail", bool(refused(m.clean_app_password, "hunter22", "")))
check("...any other server takes what its provider issued, as typed",
      m.clean_app_password("Any Thing#1", "smtp.office365.com") == "Any Thing#1")
check("...the message is the plain sentence",
      m.APP_PASSWORD_MESSAGE == "That does not look like an app password. Gmail app passwords "
      "have 16 letters (for example abcd efgh ijkl mnop). Create one in your Google "
      "Account security settings.")

# 2 ---------------------------------------------------------------------------
cfg = tempfile.mkdtemp()
msg = refused(m.save_account, {"sender": "a@b.co", "app_password": "MyPassw0rd!",
                               "smtp_host": "smtp.gmail.com"}, cfg)
check("2. a new Gmail account with a normal password is refused, nothing written",
      msg == m.APP_PASSWORD_MESSAGE and m.load(cfg) == [], msg)
aid = m.save_account({"sender": "a@b.co", "app_password": "abcd efgh ijkl mnop",
                      "smtp_host": "smtp.gmail.com"}, cfg)
check("...with an app password it is saved without the spaces",
      m.get(aid, cfg)["app_password"] == "abcdefghijklmnop")
oid = m.save_account({"sender": "o@b.co", "app_password": "Office#Pass 1",
                      "smtp_host": "smtp.office365.com"}, cfg)
check("...another server's password is stored exactly as typed",
      m.get(oid, cfg)["app_password"] == "Office#Pass 1")

# 3 ---------------------------------------------------------------------------
legacy = tempfile.mkdtemp()
m._write([{"id": "old", "name": "Old", "sender": "old@b.co", "app_password": "OldNormalPw1",
           "smtp_host": "smtp.gmail.com", "smtp_port": 587}], legacy)
m.save_account({"id": "old", "sender": "old@b.co", "name": "Renamed", "app_password": "",
                "smtp_host": "smtp.gmail.com"}, legacy)
check("3. an account saved before the rule: a blank password on an edit keeps it, unchecked",
      m.get("old", legacy)["app_password"] == "OldNormalPw1" and m.get("old", legacy)["name"] == "Renamed")
m.save_account({"id": "old", "sender": "old@b.co", "app_password": "********",
                "smtp_host": "smtp.gmail.com"}, legacy)
check("...a masked password keeps it too", m.get("old", legacy)["app_password"] == "OldNormalPw1")
msg = refused(m.save_account, {"id": "old", "sender": "old@b.co", "app_password": "NewNormal2",
                               "smtp_host": "smtp.gmail.com"}, legacy)
check("...an edit that CHANGES the password is checked, and the old one stays",
      msg == m.APP_PASSWORD_MESSAGE and m.get("old", legacy)["app_password"] == "OldNormalPw1", msg)

# 4 ---------------------------------------------------------------------------
try:
    from core.routes import auth as auth_routes                      # noqa: E402
except ImportError:                                                  # no fastapi here
    auth_routes = None
if auth_routes is None:
    route = io.open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8").read()
    body = route.split("def _check_mailbox(", 1)[1].split("@router", 1)[0]
    check("4. the first-run setup checks the app password before writing (source; no fastapi here)",
          "mail_service.clean_app_password(app_password, host)" in body)
else:
    msg = refused(auth_routes._check_mailbox, "a@b.co", "MyPassw0rd!", "")
    check("4. the first-run setup refuses a Gmail normal password before writing",
          msg == m.APP_PASSWORD_MESSAGE, msg)
    check("...and takes an app password", not refused(auth_routes._check_mailbox, "a@b.co",
                                                        "abcd efgh ijkl mnop", ""))

# 5 ---------------------------------------------------------------------------
js = io.open(os.path.join(ROOT, "frontend", "src", "lib", "appPassword.js"), encoding="utf-8").read()
check("5. the frontend helper has the same rule and words",
      "/^[a-z]{4}( ?[a-z]{4}){3}$/" in js and "(gmail|googlemail)" in js
      and "Gmail app \"\n  + \"passwords have 16 letters (for example abcd efgh ijkl mnop)" in js)
src = io.open(os.path.join(ROOT, "core", "services", "mail_accounts.py"), encoding="utf-8").read()
check("...no em dash in the helpers", "\u2014" not in js and "\u2014" not in src)

print("FAIL" if bad else "all good")
sys.exit(1 if bad else 0)
