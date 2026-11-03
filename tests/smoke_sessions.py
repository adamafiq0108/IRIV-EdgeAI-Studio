# The session registry: a session is a THING that can be listed and ended,
# not only a signature that has to be waited out.
#
# The token stays self-proving (HMAC first -- no database anywhere near the
# login path), and the registry row is what makes it revocable: no row, or a
# revoked row, and the token is dead however good the signature is. Legacy
# 3-part tokens (minted before the registry) keep verifying until they expire,
# so a deploy logs nobody out.
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import auth                                 # noqa: E402

CFG = tempfile.mkdtemp()
auth.set_master_password("adminpass1", CFG)
auth.create_user("alice", "alicepass1", "*", CFG)
auth.create_user("bob", "bobpass1", "*", CFG)


def mint(user, device=""):
    tok = auth.issue_session(user, CFG)
    auth.register_session(user, auth.session_sid(tok), device=device,
                          ip="10.0.0.9", config_dir=CFG)
    return tok


# -- mint, list ---------------------------------------------------------------
a1, a2, b1 = mint("alice", "Chrome · Windows"), mint("alice"), mint("bob")
assert a1.count("|") == 3, a1                    # user|sid|expiry|hmac
assert auth.verify_session(a1, CFG) == "alice"
assert auth.verify_session(b1, CFG) == "bob"
rows = auth.sessions_of("alice", CFG)
assert len(rows) == 2 and {r["user"] for r in rows} == {"alice"}, rows
assert rows[0]["device"] in ("Chrome · Windows", ""), rows[0]
assert len(auth.all_sessions(CFG)) == 3
print("== two users, three sessions, all listed with their device words")

# -- revoke ONE: that token dies, the others live ------------------------------
assert auth.revoke_session(auth.session_sid(a1), config_dir=CFG) == "alice"
assert auth.verify_session(a1, CFG) is None, "a revoked session still verified"
assert auth.verify_session(a2, CFG) == "alice"
assert auth.verify_session(b1, CFG) == "bob"
print("== revoking one session kills exactly that one")

# -- revoke OTHERS: only the kept one survives --------------------------------
a3 = mint("alice")
n = auth.revoke_sessions_of("alice", keep_sid=auth.session_sid(a3),
                            config_dir=CFG)
assert n == 1, n                                  # a2 (a1 was already gone)
assert auth.verify_session(a2, CFG) is None
assert auth.verify_session(a3, CFG) == "alice"
assert auth.verify_session(b1, CFG) == "bob", "another user's session died"
print("== 'sign out other sessions' keeps the one that asked")

# -- an unregistered 4-part token is dead on arrival ---------------------------
ghost = auth.issue_session("alice", CFG)          # minted, never registered
assert auth.verify_session(ghost, CFG) is None
print("== a token with no registry row does not verify (that IS revocation)")

# -- legacy 3-part tokens still pass, until expiry -----------------------------
secret = auth.ensure_secret(CFG)
body = f"alice|{int(time.time()) + 300}"
legacy = f"{body}|{auth._sign(secret, body)}"
assert auth.verify_session(legacy, CFG) == "alice"
old = f"alice|{int(time.time()) - 5}"
old = f"{old}|{auth._sign(secret, old)}"
assert auth.verify_session(old, CFG) is None
print("== a pre-registry token keeps working; an expired one does not")

# -- domain separation: a session token is not a reauth/reset token ------------
# Same pipe-delimited shape, different signature domain ("sess|" prefix).
assert auth.verify_reauth(a3, "alice", CFG) is False
assert auth.verify_reset_token(a3, CFG) is False
reauth = auth.issue_reauth("alice", CFG)
assert auth.verify_session(reauth, CFG) is None
print("== session, reauth and reset tokens cannot impersonate each other")

# -- a stolen sid under another name fails -------------------------------------
sid = auth.session_sid(a3)
forged_body = f"bob|{sid}|{int(time.time()) + 300}"
forged = f"{forged_body}|{auth._sign(secret, 'sess|' + forged_body)}"
assert auth.verify_session(forged, CFG) is None, \
    "a signed token under the wrong user for its sid verified"
print("== a sid only verifies under the account it was registered to")

# -- freeze still kills everything at once ------------------------------------
auth.update_user("bob", enabled=False, config_dir=CFG)
assert auth.verify_session(b1, CFG) is None
auth.update_user("bob", enabled=True, config_dir=CFG)
assert auth.verify_session(b1, CFG) == "bob"
print("== freezing the account still ends its sessions instantly")

# -- prune + cap ---------------------------------------------------------------
table = {}
now = int(time.time())
for i in range(auth.MAX_SESSIONS + 40):
    table[f"s{i:04x}"] = {"user": "x", "expiry": now + 1000, "seen": now - i}
table["dead"] = {"user": "x", "expiry": now - 10, "seen": now}
auth._prune(table, now)
assert "dead" not in table
assert len(table) == auth.MAX_SESSIONS, len(table)
# the oldest-seen rows are the ones that went
assert "s0000" in table and f"s{auth.MAX_SESSIONS + 39:04x}" not in table
print(f"== expired rows pruned, table capped at {auth.MAX_SESSIONS}")

# -- registry file lost: sid sessions re-log once, legacy unaffected -----------
os.remove(os.path.join(CFG, "sessions.local.toml"))
assert auth.verify_session(a3, CFG) is None
assert auth.verify_session(legacy, CFG) == "alice"
print("== losing the registry file asks for a re-login, nothing worse")
print("ALL OK")
