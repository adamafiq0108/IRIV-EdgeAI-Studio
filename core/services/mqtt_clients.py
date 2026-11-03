# -*- coding: utf-8 -*-
"""
mqtt_clients.py -- named MQTT Connections: a broker set up once and picked by
every MQTT Subscribe and MQTT Publish that talks to it.

Asked for on 2026-10-02 by a beta tester (via Tok Aba): "MQTT there no
broker IP? add user and password in mqtt, add topic user can change the topic
themselves". The broker address WAS on each Subscribe node, and not on the
Publish node's face at all; the login was on Subscribe only. Twelve nodes on
one broker meant twelve copies of its address and password.

So it is the Modbus Server pattern (detector_control [[modbus_clients]],
ModbusServerEditor.jsx), field for field where it can be: stored per system
as [[mqtt_clients]] in config.local.toml, where the app's config poll picks it
up; a node names a connection by id; changing the broker is one edit.

THE PASSWORD

Kept in config.local.toml beside the row -- where the app reads it, and
where SMTP passwords already live -- and NOWHERE a browser can read: the list
says "set" / "not set", compiled functions and outputs carry only the
connection id (so /api/functions and the outputs route, which any workflow
viewer can read, never see it), and the graph keeps no login at all. A blank
password on save keeps the stored one. A connection to THIS box's broker
(127.0.0.1) with no username still logs in through the platform's own
broker login (app/broker_login.py), so it needs no password typed at all.

Fields skipped for now, from the tester's list: driver, simulate, failover,
return-to-online, buffer timeout and max buffered messages. TLS is a field
that exists and is refused when on ("coming later") so a config written for
it later reads the same way.
"""

import os
import re

from core.services import detector_control as dc

MQTT_CLIENT_DEFAULTS = {
    "name": "", "host": "127.0.0.1", "port": 1883,
    "username": "", "password": "",
    "client_id": "", "keepalive_s": 60, "reconnect_s": 1, "tls": False,
    # Set on a Connection re-created from an export: the export never carries
    # a password, so the page asks for it until one is typed.
    "password_needed": False,
}
MAX_MQTT_CLIENTS = 32
#: A client id the broker will take: MQTT 3.1.1 promises 1-23 of these, and
#: every broker in the field allows more. Blank = the app makes one up.
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_USERNAME_MAX = 120
_PASSWORD_MAX = 200
#: The node types that pick a connection, and so hold it in use.
MQTT_CLIENT_NODES = ("variable.mqtt", "output.mqtt_publish", "output.mqtt")
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def label(c):
    name = str(c.get("name") or "").strip()
    return name or f"{c.get('host')}:{c.get('port')}"


def clean(body, current=None):
    """One connection, validated and defaulted. `current` is the stored row on
    an edit: a partial body changes only what it names, and a BLANK password
    keeps the stored one (the page never has it to send back)."""
    body = dict(body or {})
    out = dict(MQTT_CLIENT_DEFAULTS)
    out.update({k: v for k, v in (current or {}).items() if k in out})
    for key in MQTT_CLIENT_DEFAULTS:
        if key == "password":
            continue
        if key in body and body[key] is not None:
            out[key] = body[key]
    pw = body.get("password")
    if body.get("clear_password"):
        out["password"] = ""
    elif isinstance(pw, str) and pw != "":
        out["password"] = pw

    out["name"] = str(out["name"] or "").strip()[:60]
    host = str(out["host"] or "").strip()[:120] or "127.0.0.1"
    if any(ch.isspace() for ch in host) or "/" in host:
        raise ValueError("the IP address must be an address or a name, with "
                         "no spaces")
    out["host"] = host
    out["port"] = dc._clamp_int(out["port"], 1, 65535, "the port")
    # A stored password belongs to the broker it was typed for. Kept across
    # an edit that MOVES the connection (another host or port) without
    # re-entering it, the box would log in to whatever answers at the new
    # address with the old broker's credentials -- and an account allowed to
    # edit connections but never shown the password could collect it with a
    # listener of its own. So a move drops it and asks for it again.
    typed = isinstance(pw, str) and pw != ""
    if current and not typed and out["password"]:
        was_host = str(current.get("host") or "").strip() or "127.0.0.1"
        try:
            was_port = int(current.get("port") or MQTT_CLIENT_DEFAULTS["port"])
        except (TypeError, ValueError):
            was_port = None
        if host != was_host or out["port"] != was_port:
            out["password"] = ""
            out["password_needed"] = True
    user = str(out["username"] or "").strip()
    if len(user) > _USERNAME_MAX or not user.isprintable():
        raise ValueError("the user name is too long or has a control character")
    out["username"] = user
    pw = str(out["password"] or "")
    if len(pw) > _PASSWORD_MAX or not pw.isprintable():
        raise ValueError("the password is too long or has a control character")
    out["password"] = pw if user else ""     # a password with no user is noise
    cid = str(out["client_id"] or "").strip()
    if cid and not _CLIENT_ID_RE.match(cid):
        raise ValueError("the client ID may use letters, digits, '_', '.' and "
                         "'-' (up to 64) - or leave it blank")
    out["client_id"] = cid
    out["keepalive_s"] = dc._clamp_int(out["keepalive_s"], 5, 3600, "keep alive")
    out["reconnect_s"] = dc._clamp_int(out["reconnect_s"], 1, 300,
                                       "the reconnect time")
    if out["tls"] in (True, "true", "on", 1, "1"):
        raise ValueError("SSL/TLS is coming later - leave it off for now")
    out["tls"] = False
    out["password_needed"] = bool(out["password_needed"]) and not out["password"] \
        and bool(out["username"])
    return out


def _rows(local):
    return [dict(r) for r in (local.get("mqtt_clients") or []) if isinstance(r, dict)]


def _public(row):
    """A row as a browser may see it: no password, only whether one is set."""
    out = {k: v for k, v in row.items() if k != "password"}
    out["password_set"] = bool(row.get("password"))
    out["label"] = label(row)
    out["this_box"] = str(row.get("host") or "").lower() in LOOPBACK
    return out


def list_clients(systems_dir, slug, public=True):
    """Every saved connection, cleaned. `public` drops the password."""
    cfg = dc._merged(dc._pkg_dir(systems_dir, slug))
    out = []
    for row in cfg.get("mqtt_clients") or []:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            continue
        try:
            c = clean(row)
        except ValueError:
            c = clean({k: v for k, v in row.items() if k in ("name", "host", "port")})
        c["id"] = str(row["id"]).strip()[:40]
        out.append(_public(c) if public else c)
    return out


def resolve(systems_dir, slug, conn_id):
    """The full row (with password) for the box's own use, or None."""
    want = str(conn_id or "").strip()
    return next((c for c in list_clients(systems_dir, slug, public=False)
                 if c["id"] == want), None)


def refs(slug, conn_id):
    """[{"node", "label"}] -- every workflow node that picks this connection."""
    from core.services import workflow as workflow_service   # noqa: PLC0415
    try:
        store = workflow_service.load_store(slug) or {}
    except Exception:                                          # noqa: BLE001
        return []
    out = []
    for w in store.get("workflows") or []:
        for n in ((w.get("graph") or {}).get("nodes") or []):
            if n.get("type") not in MQTT_CLIENT_NODES:
                continue
            d = n.get("data") or {}
            if str(d.get("connection") or "") != str(conn_id):
                continue
            spec = workflow_service.REGISTRY.get(n["type"]) or {}
            out.append({"node": n.get("id"),
                        "label": str(d.get("name") or spec.get("label") or n["type"])})
    return out


def _new_id(rows):
    taken = {str(r.get("id") or "") for r in rows}
    cid = "mq_" + os.urandom(4).hex()
    while cid in taken:
        cid = "mq_" + os.urandom(4).hex()
    return cid


def _roots(systems_dir):
    """The two places systems live on a box: the repo's systems dir and the
    store's (detector_control.running_apps scans the same pair)."""
    roots = [str(systems_dir)]
    try:
        from core import config                              # noqa: PLC0415
        gen = str(config.generated_systems_dir())
        if gen not in roots:
            roots.append(gen)
    except Exception:                                        # noqa: BLE001
        pass
    return roots


def client_id_owner(systems_dir, slug, client_id, except_id=""):
    """(system slug, connection label) of another Connection on this box with
    `client_id`, or None. Two clients with one id on one broker knock each
    other off in a loop (Yaya, review of a34da37, M5) -- and the broker
    cannot be asked which box the other one is on, so the box checks itself."""
    want = str(client_id or "").strip()
    if not want:
        return None
    for root in _roots(systems_dir):
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for other in names:
            if not os.path.isdir(os.path.join(root, other)):
                continue
            try:
                rows = list_clients(root, other, public=False)
            except Exception:                                # noqa: BLE001
                continue
            for r in rows:
                if r.get("client_id") == want and not (
                        other == slug and r["id"] == except_id):
                    return other, label(r)
    return None


@dc.locked
def save(systems_dir, slug, body):
    """Add a connection (no id) or change one (its id). Returns it, public."""
    body = dict(body or {})
    pkg_dir = dc._pkg_dir(systems_dir, slug)
    local = dc._read_toml(dc._local_path(pkg_dir))
    rows = _rows(local)
    cid = str(body.get("id") or "").strip()
    if cid:
        at = next((i for i, r in enumerate(rows) if str(r.get("id") or "") == cid), None)
        if at is None:
            raise LookupError("no MQTT Connection with that id - it may have "
                              "been deleted; reload and pick again")
        row = clean(body, rows[at])
        row["id"] = cid
        _unique_client_id(systems_dir, slug, row, cid)
        rows[at] = row
    else:
        if len(rows) >= MAX_MQTT_CLIENTS:
            raise ValueError(f"at most {MAX_MQTT_CLIENTS} MQTT Connections per system")
        row = clean(body)
        row["id"] = _new_id(rows)
        _unique_client_id(systems_dir, slug, row, row["id"])
        rows.append(row)
    local["mqtt_clients"] = rows
    dc._write_local(pkg_dir, local)
    return _public(row)


def _unique_client_id(systems_dir, slug, row, own_id):
    owner = client_id_owner(systems_dir, slug, row.get("client_id"), own_id)
    if owner:
        where = "" if owner[0] == slug else f" in system '{owner[0]}'"
        raise ValueError(f"client ID '{row['client_id']}' is already used by "
                         f"MQTT Connection '{owner[1]}'{where} - two clients "
                         "with one ID knock each other off the broker; leave "
                         "it blank for an automatic one")


@dc.locked
def delete(systems_dir, slug, conn_id):
    """Remove a connection nothing uses; one a node still picks is refused,
    naming the nodes (the Modbus Server rule)."""
    cid = str(conn_id or "").strip()
    pkg_dir = dc._pkg_dir(systems_dir, slug)
    local = dc._read_toml(dc._local_path(pkg_dir))
    rows = _rows(local)
    if not any(str(r.get("id") or "") == cid for r in rows):
        raise LookupError("no MQTT Connection with that id")
    used = refs(slug, cid)
    if used:
        names = ", ".join(sorted({r["label"] for r in used}))
        raise ValueError(f"this MQTT Connection is used by {len(used)} "
                         f"node{'s' if len(used) != 1 else ''} ({names}) - pick "
                         f"another connection on {'them' if len(used) != 1 else 'it'} first")
    local["mqtt_clients"] = [r for r in rows if str(r.get("id") or "") != cid]
    dc._write_local(pkg_dir, local)
    return True


# ---------------------------------------------------------------------------
# Moving nodes' own broker settings into Connections
# ---------------------------------------------------------------------------

def _same(row, host, port, user, pw):
    h = str(row.get("host") or "").strip().lower()
    want = str(host or "").strip().lower() or "127.0.0.1"
    same_host = h == want or (h in LOOPBACK and want in LOOPBACK)
    return (same_host and int(row.get("port") or 1883) == int(port or 1883)
            and str(row.get("username") or "") == str(user or "")
            and str(row.get("password") or "") == str(pw or ""))


def migrate_graph(graph, systems_dir, slug):
    """Give every MQTT node saved with its OWN broker settings a Connection.

    A node without the `connection` key is from before Connections: its host,
    port, username and password (Subscribe) or host and port (Publish) move
    into a Connection -- an existing identical one if there is one, else a
    new one shared by every node with the same four values -- and the node
    keeps only the id. The login leaves the graph with it, which also stops
    it travelling in exported workflows. Returns True when the graph changed.
    Never raises: a migration that cannot run leaves the node on its old,
    still-working settings.
    """
    nodes = [n for n in (graph or {}).get("nodes") or []
             if n.get("type") in MQTT_CLIENT_NODES
             and "connection" not in (n.get("data") or {})
             # Tried once and could not be moved (an address like "my
             # broker"): left as it is, with the reason on the node -- and
             # not tried, and the store not rewritten, on every open after.
             and not (n.get("data") or {}).get("mqtt_unmigrated")]
    if not nodes:
        return False
    try:
        rows = list_clients(systems_dir, slug, public=False)
    except Exception:                                        # noqa: BLE001
        return False
    created = []
    for n in nodes:
        d = n.setdefault("data", {})
        host = str(d.get("host") or "127.0.0.1").strip() or "127.0.0.1"
        port = d.get("port") or 1883
        user = str(d.get("username") or "").strip()
        pw = str(d.get("password") or "") if user else ""
        match = next((r for r in rows + created if _same(r, host, port, user, pw)), None)
        if match is None:
            try:
                body = {"host": host, "port": port, "username": user, "password": pw,
                        "name": ("This box" if host.lower() in LOOPBACK
                                 and int(port or 1883) == 1883 and not user
                                 else f"{host}:{port}")}
                match = save(systems_dir, slug, body)
                match = {**match, "password": pw}
            except (ValueError, OSError) as exc:
                d["mqtt_unmigrated"] = (str(exc)[:200]
                                        or "this broker address is not valid")
                continue
            created.append(match)
        d["connection"] = match["id"]
        for key in ("host", "port", "username", "password"):
            d.pop(key, None)
    return True


# ---------------------------------------------------------------------------
# Export / import (templates.export_system / import_system)
# ---------------------------------------------------------------------------

def export_rows(systems_dir, slug):
    """This system's Connections for an export: everything but the password
    (an export is a file people mail to each other), marked password_needed
    where one was set, so the importing box asks for it."""
    out = []
    for r in list_clients(systems_dir, slug, public=False):
        row = {k: v for k, v in r.items() if k != "password"}
        row["password_needed"] = bool(r.get("password")) or bool(r.get("password_needed"))
        out.append(row)
    return out


def import_rows(systems_dir, slug, rows):
    """Re-create exported Connections in a freshly imported system. Returns
    ({exported id: new id}, [warnings]). Rows with the same host, port and
    user name become ONE Connection, as migrate_graph does; an existing one
    that matches is reused. The client ID is dropped when it would clash
    with another on this box (M5) -- the app then makes one up."""
    mapping, warnings = {}, []
    have = list_clients(systems_dir, slug, public=False)
    for r in rows or []:
        if not isinstance(r, dict) or not str(r.get("id") or "").strip():
            continue
        old = str(r["id"]).strip()
        host = str(r.get("host") or "127.0.0.1")
        port = r.get("port") or 1883
        user = str(r.get("username") or "")
        match = next((h for h in have
                      if str(h.get("host") or "").lower() == host.lower()
                      and int(h.get("port") or 1883) == int(port)
                      and str(h.get("username") or "") == user), None)
        if match is None:
            body = {k: v for k, v in r.items() if k in MQTT_CLIENT_DEFAULTS
                    and k not in ("password",)}
            if client_id_owner(systems_dir, slug, body.get("client_id")):
                body["client_id"] = ""
            try:
                match = save(systems_dir, slug, body)
            except (ValueError, OSError) as exc:
                warnings.append(f"MQTT Connection '{r.get('name') or host}' "
                                f"could not be re-created: {exc}")
                continue
            have.append(match)
            if match.get("password_needed"):
                warnings.append(f"MQTT Connection '{label(match)}' needs its "
                                "password - exports never carry one")
        mapping[old] = match["id"]
    return mapping, warnings


def remap(obj, mapping):
    """`obj` (a workflow store, or a list of functions / outputs) with every
    Connection id in `mapping` replaced -- node data `connection`, a
    function's params.conn, an output's conn. In place; also returned."""
    if not mapping:
        return obj
    if isinstance(obj, dict):
        for key in ("connection", "conn"):
            if isinstance(obj.get(key), str) and obj[key] in mapping:
                obj[key] = mapping[obj[key]]
        for v in obj.values():
            remap(v, mapping)
    elif isinstance(obj, list):
        for v in obj:
            remap(v, mapping)
    return obj
