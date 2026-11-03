# Forgot password: an address, a request, an approval, a link -- and the inbox.
#
# The forgot page used to reset the MASTER's password and only that: no
# address typed, one link to the one address the box knew. Now every account
# has an address (required on create; master and the developer alone may set
# it), the forgot page asks for it, and what happens depends on whose it is:
# master gets the link at once, a developer is told to use the box, and
# everyone else becomes a REQUEST that master or the developer approve from
# the bell's Inbox -- approving is what sends the link. The link is bound to
# the account that asked, dies when that password changes, and the change is
# announced in the account's own inbox and the admins'.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import auth                                # noqa: E402
from core.services import inbox                               # noqa: E402
from core.services import reset_requests as rr                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def refuses(fn, *a, word="", **k):
    try:
        fn(*a, **k)
        return False
    except ValueError as exc:
        return word in str(exc) if word else True


CFG = tempfile.mkdtemp()
auth.set_master_password("masterpass1", CFG)
auth.set_admin_email("Owner@Example.com", CFG)
master = auth.admin_username(CFG)

# -- addresses --------------------------------------------------------------------
check("the master address is normalised", auth.admin_email(CFG) == "owner@example.com")
auth.create_user("alice", "alicepass1", "*", CFG, email="Alice@Example.com")
check("an account is made with its address, normalised",
      auth.user_email("alice", CFG) == "alice@example.com")
check("...and the address finds the account",
      auth.account_for_email("ALICE@example.com", CFG) == "alice"
      and auth.account_for_email("owner@example.com", CFG) == master
      and auth.account_for_email("nobody@example.com", CFG) == "")
check("a second account cannot take alice's address",
      refuses(auth.create_user, "bob", "bobpass123", "*", CFG,
              email="alice@example.com", word="already"))
check("...nor the master's",
      refuses(auth.create_user, "bob", "bobpass123", "*", CFG,
              email="owner@example.com", word="administrator"))
check("...and the master cannot take alice's",
      refuses(auth.set_admin_email, "alice@example.com", CFG, word="already"))
check("a bad address is refused", refuses(auth.create_user, "bob", "bobpass123", "*",
                                          CFG, email="not-an-email"))
auth.create_user("bob", "bobpass123", "*", CFG, email="bob@example.com")
check("an address can be changed", auth.set_user_email("bob", "b2@example.com", CFG)
      == "b2@example.com" and auth.user_email("bob", CFG) == "b2@example.com")
check("...to nobody else's", refuses(auth.set_user_email, "bob", "alice@example.com", CFG))
row = next(u for u in auth.public_users(CFG) if u["username"] == "alice")
check("the accounts page sees the address", row.get("email") == "alice@example.com")
check("...and the master's", auth.admin_account(CFG).get("email") == "owner@example.com")

# -- tokens are per account ------------------------------------------------------
t_alice = auth.issue_reset_token(CFG, "alice")
t_master = auth.issue_reset_token(CFG)
check("a token names its account", auth.reset_token_user(t_alice, CFG) == "alice"
      and auth.reset_token_user(t_master, CFG) == master)
check("...and cannot be pointed at another",
      auth.reset_token_user(t_alice.replace("|alice|", "|bob|"), CFG) == "")
check("a token for an account that does not exist is worthless",
      auth.reset_token_user(auth.issue_reset_token(CFG, "ghost"), CFG) == "")
auth.set_user_password("alice", "alicepass2", CFG)
check("alice's token dies when alice's password changes",
      auth.reset_token_user(t_alice, CFG) == "" and auth.reset_token_user(t_master, CFG) == master)
check("the old 4-part token shape is no longer valid",
      auth.reset_token_user("pwreset|1|2|3", CFG) == "")

# -- requests --------------------------------------------------------------------
r1 = rr.create("alice", "alice@example.com", "10.0.0.5", CFG, now=1000)
r2 = rr.create("bob", "b2@example.com", "10.0.0.6", CFG, now=1001)
check("two requests wait", [r["user"] for r in rr.pending(CFG, now=1002)] == ["alice", "bob"])
r1b = rr.create("alice", "alice@example.com", "10.0.0.7", CFG, now=1003)
check("a second ask from the same account replaces the first",
      [r["id"] for r in rr.pending(CFG, now=1004)] == [r2["id"], r1b["id"]])
check("taking a request removes it, once",
      rr.take(r2["id"], CFG, now=1005)["user"] == "bob"
      and rr.take(r2["id"], CFG, now=1005) is None)
check("an unanswered request expires",
      rr.pending(CFG, now=1003 + rr.REQUEST_TTL_S + 1) == [])

# -- inbox -----------------------------------------------------------------------
inbox.notify_admins("reset_request", "Password reset requested: alice", "…", CFG,
                    request_id="x1", user="alice")
inbox.post("alice", "password_changed", "Your password was changed", "…", CFG)
check("alice sees her notice and not the admins'",
      [n["kind"] for n in inbox.for_user("alice", False, CFG)] == ["password_changed"])
check("master sees the admins' notice and not alice's",
      [n["kind"] for n in inbox.for_user(master, True, CFG)] == ["reset_request"])
check("unread counts per reader",
      inbox.unread_count(master, True, CFG) == 1 and inbox.unread_count("alice", False, CFG) == 1)
inbox.mark_read(master, None, CFG)
check("master reading it does not read it for the developer",
      inbox.unread_count(master, True, CFG) == 0 and inbox.unread_count("dev", True, CFG) == 1)
check("alice cannot delete the admins' notice",
      inbox.delete("alice", inbox.for_user(master, True, CFG)[0]["id"], False, CFG) is False)
check("...an admin can", inbox.delete(master, inbox.for_user(master, True, CFG)[0]["id"], True, CFG))

# -- the routes say what the design says ---------------------------------------------
src = io.open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8").read()
forgot = src[src.index("def _forgot_side_effects"):src.index('name="auth.reset_requests"')]
check("the forgot route reads an email and answers the same for hit and miss",
      'body.get("email")' in forgot and "_FORGOT_ANSWER" in forgot)
check("...master gets the link at once, a developer is told to use the box",
      "is_master(username" in forgot and "is_developer(username" in forgot
      and "make_dev_account" in forgot)
check("...everyone else becomes a request in the admins' inbox",
      "resetreq_service.create(" in forgot and "notify_admins(" in forgot)
approve = src[src.index('name="auth.reset_request_approve"'):src.index('name="auth.reset_request_reject"')]
check("approving takes the request and sends the link",
      "resetreq_service.take(" in approve and "_send_reset_link, request" in approve)
reset = src[src.index('name="auth.reset_json"'):src.index("# -- the inbox")]
check("the reset sets the password of the account the token names",
      "reset_token_user(token" in reset and "set_user_password(who" in reset
      and "set_master_password(password" in reset)
check("...ends its sessions and tells the account and the admins",
      "revoke_sessions_of(who" in reset and 'inbox_service.post(who, "password_changed"' in reset
      and 'notify_admins("password_changed"' in reset)
create = src[src.index('name="users.create"'):src.index('name="users.update"')]
# An address is OPTIONAL now (smoke_optionalemail.py): a box on a closed
# network has nothing to send a reset link through, and one that cannot be
# reached reads as a way back in without being one. What must NOT slip is the
# other half -- a malformed address is still a mistake, and still refused.
check("a new account may have no address, but not a malformed one",
      "an email address is required" not in create
      and 'auth_service.clean_email(body.get("email"))' in create
      and 'if str(body.get("email") or "").strip():' in create)
update = src[src.index('name="users.update"'):src.index('name="users.reset_password"')]
check("changing an address is master/developer only",
      '"email" in body' in update and "_require_admin(request)" in update)
check("the inbox routes exist", '"/api/inbox"' in src and '"/api/inbox/read"' in src
      and '"/api/auth/reset-requests"' in src)
# The open-prefix matcher is boundary-anchored: the request routes share
# the first 15 characters of an open path and must still sit behind the
# session wall. Mirrors routes/auth._under without importing FastAPI.
def _under(path, prefixes):
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "?")
               for p in prefixes)


check("/api/auth/reset-requests is not under the open /api/auth/reset prefix",
      _under("/api/auth/reset", ("/api/auth/reset",))
      and not _under("/api/auth/reset-requests", ("/api/auth/reset",))
      and not _under("/api/auth/reset-requests/x/approve", ("/api/auth/reset",))
      and 'startswith(p + "/")' in src)
check("one address only: no lists, no header characters",
      refuses(auth.clean_email, "victim@x.com,attacker@y.com")
      and refuses(auth.clean_email, "Name <a@b.com>")
      and refuses(auth.clean_email, "a@b.com;c@d.com")
      and auth.clean_email("  A@B.com ") == "a@b.com")
auth.update_user("bob", enabled=False, config_dir=CFG)
check("a frozen account's address finds nobody, and its token is worthless",
      auth.account_for_email("b2@example.com", CFG) == ""
      and auth.reset_token_user(auth.issue_reset_token(CFG, "bob"), CFG) == "")
auth.update_user("bob", enabled=True, config_dir=CFG)
check("the forgot route answers before it acts, the same for hit and miss",
      "run_in_executor" in forgot and "return dict(_FORGOT_ANSWER)" in forgot
      and "status_code=502" not in forgot and forgot.count('record_failure("forgot"') == 1)
# One helper since Pipi's proxy review (2026-10-07): core/net.py client_ip,
# the LAST hop (the one the local relay or tunnel appended).
NET = io.open(os.path.join(ROOT, "core", "net.py"), encoding="utf-8").read()
check("the client address is one validated hop, trusted only behind the box",
      "return net.client_ip(request)" in src and "ipaddress.ip_address(hop)" in NET
      and 'forwarded.split(",")[-1]' in NET and '"127.0.0.1", "::1"' in NET)
check("nobody is told whether the master has an address",
      "has_admin_email" not in src)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
