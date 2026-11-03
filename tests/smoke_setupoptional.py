# First run with the recovery email and the sending mailbox left blank, and a
# finished box that an update must not send back through it.
#
# Tok Aba, 2026-10-05: the recovery email and the whole sending mailbox are
# optional at first run, but a mailbox the owner STARTED (an address with no
# app password) is refused in plain words rather than dropped. And a box that
# already finished setup was being shown the setup screen again after an OTA
# update: the login route had a second first-run rule of its own, "no
# recovery email and no recorded opt-out", keyed on a field that is optional.
# A box set up without an address therefore went back through first run at
# its next sign-in. setup_pending -- "still on the shipped password" -- is now
# the one rule everywhere, and it reads auth.local.toml, which apply_update
# carries into every new release directory.
#
# Drives the real route (fastapi stubbed: it is not on a dev box) and the
# real applier's carry_config.
#
#     py -3 tests/smoke_setupoptional.py
import asyncio
import importlib.util
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class _JSONResponse:
    def __init__(self, content=None, status_code=200, headers=None):
        self.content, self.status_code = content, status_code
        self.headers = dict(headers or {})

    def set_cookie(self, *a, **k):
        pass


if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail, self.headers = status_code, detail, headers
    fa.HTTPException = HTTPException
    fa.Request = object
    fa.Query = lambda *a, **k: None
    fa.__path__ = []
    sys.modules["fastapi"] = fa
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = resp.RedirectResponse = resp.Response = \
        resp.StreamingResponse = resp.FileResponse = _JSONResponse
    sys.modules["fastapi.responses"] = resp

from fastapi import HTTPException                            # noqa: E402
from core.routes import auth as auth_routes                  # noqa: E402
from core.services import auth as auth_service               # noqa: E402
from core.services import mail_accounts as mail_service      # noqa: E402

SHIPPED = auth_service.SHIPPED_PASSWORD


def submit(cfg, body):
    async def _json():
        return body
    req = types.SimpleNamespace(
        url=types.SimpleNamespace(path="/api/auth/account-setup", hostname="box",
                                  scheme="http"),
        method="POST", headers={}, cookies={},
        client=types.SimpleNamespace(host="127.0.0.1"),
        app=types.SimpleNamespace(state=types.SimpleNamespace(config_dir=cfg,
                                                              registry=[])),
        state=types.SimpleNamespace(user=auth_service.admin_username(cfg),
                                    session_sid=""),
        json=_json, body=_json)
    try:
        return 200, asyncio.run(auth_routes.setup_json(req))
    except HTTPException as e:
        return e.status_code, e.detail


def fresh_box():
    cfg = tempfile.mkdtemp()
    auth_service.set_master_password(SHIPPED, cfg)
    return cfg


BLANK = {"email": "", "smtp_sender": "", "smtp_password": "", "smtp_host": ""}

# -- 1. the mailbox started but not finished is refused, in plain words --------
CFG = fresh_box()
for label, extra, want in (
        ("an address with no app password",
         {"smtp_sender": "alerts@example.com"}, "Add the app password too"),
        ("an app password with no address",
         {"smtp_password": "abcd efgh"}, "Add the sending address too"),
        ("an SMTP server alone",
         {"smtp_host": "smtp.example.com"}, "Add the sending address too"),
        ("a sending address that is not one",
         {"smtp_sender": "not-an-address", "smtp_password": "abcd"},
         "The sending address is not a valid email address.")):
    code, detail = submit(CFG, {**BLANK, **extra, "password": "a-good-pass",
                                "current": SHIPPED})
    check(f"{label} is refused", code == 400 and want in str(detail),
          (code, detail))
    check("...before anything was written",
          auth_service.master_password_is_default(CFG) is True
          and not mail_service.load(CFG))
check("the refusals say how to skip it",
      "clear the mailbox fields to set it up later" in str(
          submit(CFG, {**BLANK, "smtp_sender": "a@example.com",
                       "password": "a-good-pass", "current": SHIPPED})[1]))

# -- 2. everything optional blank: setup still finishes ------------------------
code, out = submit(CFG, {**BLANK, "password": "a-good-pass", "current": SHIPPED})
check("setup finishes with the recovery email and mailbox blank", code == 200,
      (code, out))
check("...the password left the shipped one",
      auth_service.master_password_is_default(CFG) is False)
check("...no half mailbox was stored", mail_service.load(CFG) == [])
check("...and the gate is closed",
      auth_service.setup_pending(auth_service.admin_username(CFG), CFG) is False)

# -- 3. a whole mailbox is still saved ----------------------------------------
CFG2 = fresh_box()
code, out = submit(CFG2, {**BLANK, "password": "a-good-pass", "current": SHIPPED,
                          "smtp_sender": "alerts@example.com",
                          "smtp_password": "abcd efgh ijkl mnop"})
check("a complete mailbox is saved", code == 200
      and [a["sender"] for a in mail_service.load(CFG2)] == ["alerts@example.com"],
      (code, out, mail_service.load(CFG2)))

# -- 4. the shipped password still forces setup -------------------------------
CFG3 = fresh_box()
code, detail = submit(CFG3, {**BLANK})
check("a board on the shipped password cannot finish without a new one",
      code == 400 and "shipped with" in str(detail), (code, detail))
check("...and stays gated",
      auth_service.setup_pending(auth_service.admin_username(CFG3), CFG3) is True)

# -- 5. the login asks the same one rule ----------------------------------------
route = open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8").read()
gate = route.split("needs_setup = ", 1)[1].split("\n", 1)[0].strip()
check("the login's first-run rule is setup_pending, nothing else",
      gate == "auth_service.setup_pending(username, cfg)", gate)
pending_src = open(os.path.join(ROOT, "core", "services", "auth.py"),
                   encoding="utf-8").read().split("def setup_pending(", 1)[1] \
    .split("\ndef ", 1)[0].split('"""', 2)[2]
check("...and that rule reads no optional field (email, mailbox)",
      "email" not in pending_src and "mail" not in pending_src, pending_src)


# -- 6. a simulated OTA update: a new release directory -------------------------
def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


au = _load(os.path.join(ROOT, "scripts", "apply_update.py"), "_smoke_applier_setup")


def updated(old_cfg_setup):
    """A box's config in release A, carried into a fresh release B the way
    do_apply carries it, with B's own stock files in place."""
    rel = tempfile.mkdtemp(prefix="smoke_setupoptional_")
    old, new = os.path.join(rel, "v1.0.15"), os.path.join(rel, "v1.0.16")
    os.makedirs(os.path.join(old, "config"))
    os.makedirs(os.path.join(new, "config"))
    old_setup = os.path.join(old, "config")
    old_cfg_setup(old_setup)
    with open(os.path.join(new, "config", "auth.toml"), "w") as f:
        f.write("[session]\nidle_timeout_minutes = 60\n")
    au.carry_config(old, new)
    return os.path.join(new, "config")


def finished_without_email(cfg):
    # Set up, own password, recovery email never given and no opt-out
    # recorded -- what the old email arm sent back through first run.
    auth_service.set_master_password("owner-chose-this", cfg)


after = updated(finished_without_email)
admin = auth_service.admin_username(after)
check("after an update a finished box is not sent through setup again",
      auth_service.setup_pending(admin, after) is False)
check("...even with no recovery email and no opt-out on file",
      auth_service.admin_email(after) == ""
      and not auth_service.admin_email_optout(after))
check("...because its own password came across with it",
      auth_service.verify_login(admin, "owner-chose-this", after))

after = updated(lambda cfg: auth_service.set_master_password(SHIPPED, cfg))
check("after an update a box still on the shipped password is still gated",
      auth_service.setup_pending(auth_service.admin_username(after), after) is True)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
