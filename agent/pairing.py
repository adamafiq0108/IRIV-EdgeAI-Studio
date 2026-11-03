# -*- coding: utf-8 -*-
"""
pairing.py -- Pairing state and token verification for the agent.

The master's serial is broadcast over mDNS, so it identifies a master; it
authenticates nothing. Pairing exchanges it for a real bearer token, and every
authenticated endpoint checks that token in constant time.
"""

import hmac
import json
import os
import pathlib
import time

_DEFAULT_PATH = pathlib.Path(
    os.environ.get("CYTRON_AGENT_STATE", "/var/lib/cytron-agent/pairing.json")
)

#: How long an operator-opened unlock stays open.
UNLOCK_WINDOW_S = 300


class PairingStore:
    """Persisted {master_serial, token} plus a transient unlock window."""

    def __init__(self, path=None):
        self.path = pathlib.Path(path) if path else _DEFAULT_PATH
        self._unlocked_until = 0.0
        self._state = self._load()

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {"paired": False, "master_serial": None, "token": None}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        # Token is a credential. Best-effort on filesystems that support it.
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    # -- state --------------------------------------------------------------

    @property
    def paired(self):
        return bool(self._state.get("paired"))

    @property
    def master_serial(self):
        return self._state.get("master_serial")

    def unlock(self):
        """Open the pairing window. Operator action only."""
        self._unlocked_until = time.time() + UNLOCK_WINDOW_S

    @property
    def unlocked(self):
        return time.time() < self._unlocked_until

    def may_pair(self):
        """Pairing is allowed only while unpaired, or inside an unlock window.

        Without this an already-deployed device would accept a pair request
        from any host on the LAN and hand its video and event history to it.
        """
        return (not self.paired) or self.unlocked

    def pair(self, master_serial, token, paired_by=None):
        # paired_by is accepted for interface parity with database-backed
        # stores (v1.1 credential pairing records who authorized it); the
        # file store has nowhere meaningful to surface it beyond the state.
        if not token:
            raise ValueError("token required")
        if not self.may_pair():
            raise PermissionError(
                "already paired; an operator must open the unlock window first"
            )
        self._state = {"paired": True, "master_serial": master_serial,
                       "token": token, "paired_by": paired_by}
        self._unlocked_until = 0.0
        self._save()

    def unpair(self):
        self._state = {"paired": False, "master_serial": None, "token": None}
        self._save()

    # -- rotation (v1.3/#11): keep-both-valid, parity with PgPairingStore ----

    def begin_rotation(self, new_token):
        if not new_token:
            raise ValueError("new token required")
        self._state["pending_token"] = new_token
        self._save()

    def commit_rotation(self):
        pending = self._state.pop("pending_token", None)
        if pending:
            self._state["token"] = pending
        self._save()

    def abort_rotation(self):
        self._state.pop("pending_token", None)
        self._save()

    # -- auth ---------------------------------------------------------------

    def verify(self, presented):
        """Constant-time token check. `==` here leaks the token by timing.
        Accepts the current OR a pending (mid-rotation) token so the switch
        never opens a 401 gap."""
        if not presented:
            return False
        for expected in (self._state.get("token"), self._state.get("pending_token")):
            if expected and hmac.compare_digest(str(presented), str(expected)):
                return True
        return False
