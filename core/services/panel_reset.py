"""Master password reset from the box itself: a code on the front panel.

The emailed link needs an address on file and a mailbox that works. An
owner locked out of a box that has neither still owns the box, and the one
thing they can prove is that they are standing at it. So: hold the USER
button five seconds and the OLED shows a six-digit code, good for ten
minutes. Typed into the Forgot page with a new password, it resets MASTER
and nobody else -- the button belongs to the box, and master is the account
that belongs to the box.

The code lives in config/panel_reset.local.toml (0600, like every other
local file) rather than in the hub's memory: the panel runs in the hub
process, the Forgot page may be served by any master on the box, and they
share the config directory, not a heap. Stored hashed with the session
secret; five wrong guesses burn it. At six digits and five tries that is a
1-in-200,000 shot inside a ten-minute window that only opens when somebody
has their hand on the hardware.
"""
import hashlib
import hmac
import secrets
import time

from core import config
from core.services import auth as auth_service

CODE_DIGITS = 6
VALID_SECONDS = 10 * 60
MAX_ATTEMPTS = 5
_NAME = "panel_reset"


def _digest(code, config_dir):
    key = auth_service.ensure_secret(config_dir).encode()
    return hmac.new(key, str(code).encode(), hashlib.sha256).hexdigest()


def _load(config_dir):
    return dict(config.load_local(_NAME, config_dir).get("reset") or {})


def _save(row, config_dir):
    config.save_local(_NAME, {"reset": row} if row else {}, config_dir)


def begin(config_dir=None):
    """Mint a fresh code (any earlier one dies) and return it, for the screen."""
    code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
    _save({"digest": _digest(code, config_dir),
           "expires": int(time.time()) + VALID_SECONDS,
           "attempts": 0}, config_dir)
    return code


def active(config_dir=None):
    """Whether a code is live right now -- seconds left, or 0."""
    row = _load(config_dir)
    left = int(row.get("expires") or 0) - int(time.time())
    return max(0, left) if row.get("digest") else 0


def cancel(config_dir=None):
    _save({}, config_dir)


def consume(code, config_dir=None):
    """Check a typed code against the live one. True once; the code is spent.

    A wrong guess counts; the fifth clears the code so a guesser gets nothing
    more until somebody holds the button again.
    """
    row = _load(config_dir)
    if not row.get("digest") or int(row.get("expires") or 0) < time.time():
        cancel(config_dir)
        return False
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if hmac.compare_digest(row["digest"], _digest(code, config_dir)):
        cancel(config_dir)
        return True
    row["attempts"] = int(row.get("attempts") or 0) + 1
    if row["attempts"] >= MAX_ATTEMPTS:
        cancel(config_dir)
    else:
        _save(row, config_dir)
    return False
