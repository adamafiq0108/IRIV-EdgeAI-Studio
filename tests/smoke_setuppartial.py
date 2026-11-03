# A refused account-setup submission writes nothing.
#
# UAT-107 (Ochobot, first run on board "iriv", 2026-10-04): POST
# /api/auth/account-setup with password "abc" answered 400 "password must be
# at least 8 characters" -- and the recovery email in the same body had
# already been saved, because the route wrote the address first and the
# password rule only ran after it. The form showed an error; half of it had
# landed anyway.
#
# Drives the real route (fastapi stubbed: it is not on a dev box).
#
#     py -3 tests/smoke_setuppartial.py
import asyncio
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

CFG = tempfile.mkdtemp()
SHIPPED = auth_service.SHIPPED_PASSWORD
auth_service.set_master_password(SHIPPED, CFG)
ADMIN = auth_service.admin_username(CFG)
STATE = types.SimpleNamespace(config_dir=CFG, registry=[])


def submit(body):
    async def _json():
        return body
    req = types.SimpleNamespace(
        url=types.SimpleNamespace(path="/api/auth/account-setup", hostname="box",
                                  scheme="http"),
        method="POST", headers={}, cookies={},
        client=types.SimpleNamespace(host="127.0.0.1"),
        app=types.SimpleNamespace(state=STATE),
        state=types.SimpleNamespace(user=ADMIN, session_sid=""),
        json=_json, body=_json)
    try:
        return 200, asyncio.run(auth_routes.setup_json(req))
    except HTTPException as e:
        return e.status_code, e.detail


code, detail = submit({"email": "new@example.com", "password": "abc",
                       "current": SHIPPED})
check("a short password is refused", code == 400 and "8 characters" in str(detail),
      (code, detail))
check("...and the recovery email in the same body was NOT saved",
      auth_service.admin_email(CFG) == "", auth_service.admin_email(CFG))

code, detail = submit({"email": "new@example.com", "password": "a-good-pass",
                       "username": "not a name", "current": SHIPPED})
check("a bad new name is refused", code == 400, (code, detail))
check("...before the password or email were written",
      auth_service.master_password_is_default(CFG) is True
      and auth_service.admin_email(CFG) == "")

code, detail = submit({"email": "two@a.com, three@b.com", "password": "a-good-pass",
                       "current": SHIPPED})
check("a bad email is refused", code == 400, (code, detail))
check("...before the password was written",
      auth_service.master_password_is_default(CFG) is True)

code, detail = submit({"email": "new@example.com", "password": "a-good-pass",
                       "current": "wrong-one"})
check("a wrong current password is refused", code == 403, (code, detail))
check("...and nothing was written",
      auth_service.master_password_is_default(CFG) is True
      and auth_service.admin_email(CFG) == "")

code, out = submit({"email": "new@example.com", "password": "a-good-pass",
                    "current": SHIPPED})
check("a good submission goes through", code == 200, (code, out))
check("...and writes both", auth_service.admin_email(CFG) == "new@example.com"
      and auth_service.master_password_is_default(CFG) is False)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
