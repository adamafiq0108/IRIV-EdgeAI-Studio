# -*- coding: utf-8 -*-
"""
template_updates.py -- pull the built-in templates a developer has published.

The developer hub's "Publish templates" button commits config/formats.toml to
the source repo, and the next release carries it to every box. Between
releases a box can ask for the templates on their own: this module fetches
the published file from the place every box can already read (the release
repo, with the box's own update token -- services/source_publish.mirror()),
checks the signature the developer hub put beside it, works out which
templates are new or changed against what the box holds, and writes those,
and only those, into the local formats.toml through the same validator the
System Templates page uses.

Three rules, set by the product owner:

  * a template update never touches a SYSTEM. formats.toml is what a NEW
    system starts from; systems already created keep their own workflow,
    dashboard and zones, and only a release update may change those.
  * nothing is committed from here. This reads GitHub. The one write to
    GitHub in the product is the Publish button on the developer hub, and
    it runs when pressed, never on a timer.
  * the box asks. There is no background check: the fetch runs when someone
    is about to create a system -- the developer's "Check for template
    updates" button, and (since 2026-10-14, Tok Aba) the wizard opening,
    through auto_refresh() at the bottom of this file. Opening the wizard
    is the moment a newer template matters, and nothing runs on a timer.

THE DEVELOPER HUB IS THE SOURCE, NOT A DESTINATION. On a box declared a dev
box (config/deploy.toml) the check installs nothing: the published copy is
at best what this hub sent and at worst older than the edits in progress,
and a template deleted here before re-publishing must not come back through
the same button. There the check COMPARES -- which templates differ from the
published copy, which are not published yet, which were removed here -- so
the developer knows whether to press Publish. On every other box a template
whose workbench system lives locally is kept for the same reason; a template
that exists locally but not upstream is kept too (this installs, it does not
prune); a draft upstream is not a published template and is skipped.

Trust: the file is signed with the store key (core/services/store.py; the
public half ships in every box), and this box installs nothing that does not
verify. A repository write is not a signature -- config/update.toml is
explicit that the repo split is not an access boundary -- so a leaked publish
token can put a file in the repo but cannot make a box take it. Every entry
still goes through format_entry(), the same validation an authored template
gets, and the workflow inside is cleaned again by clean_graph() when a system
is created from it.
"""

import hashlib
import json
import logging
import threading
import time
import tomllib
import urllib.error

from core import config
from core.services import ghnet
from core.services import provision as provision_service
from core.services import source_publish
from core.services import store as store_service
from core.services import update as update_service
from core.services import update_auto

log = logging.getLogger(__name__)

#: Machine-written record of the last check: config/templates_update.local.toml.
STATE_FILE = "templates_update"

#: What the check installed: config/templates_installed.local.toml. The check
#: writes formats.toml, and formats.toml belongs to the RELEASE: an update
#: swaps in a new release directory and carries only the *.local.toml files
#: across, so without this record every update put the box back on the
#: templates it shipped with, and the admin had to press the button again.
#: Each entry keeps the template as installed and a fingerprint of what the
#: release held for that key before it (`base`), so restore() can tell "the
#: release still ships the old one" (re-apply) from "a newer release changed
#: it" (the release wins, the record is dropped).
INSTALLED_FILE = "templates_installed"

#: formats.toml is tens of kilobytes. A file larger than this is not the one
#: the developer hub publishes, whatever the repo says.
MAX_BYTES = 4 << 20

#: A base64 ed25519 signature is 88 characters; anything past this is not one.
MAX_SIG_BYTES = 4096

#: The JSON blobs a stored entry carries, and the document each one holds.
_BLOBS = (("workflows_json", "workflows"), ("dashboard_json", "dashboard"),
          ("tables_json", "tables"), ("zones_json", "zones"),
          ("settings_json", "settings"))

#: Flags on a stored entry that describe its STATE on a box rather than its
#: content. Never part of the comparison, never copied from upstream.
_STATE_KEYS = ("draft", "hidden")


def target(config_dir=None):
    """Where the published file is read from: {owner, repo, branch, path}."""
    return source_publish.mirror(config_dir)


def authoring(config_dir=None):
    """Is this the hub the templates come FROM? Then the check compares and
    installs nothing (see the banner)."""
    return bool(config.is_dev_box(config_dir))


def status(config_dir=None):
    """What the wizard shows beside the button: when the box last checked,
    what that found, and whether it can check at all. Never a token.

    Guarded: the listing this rides on is what the create wizard is built
    from, and a hand-edited state file must not take that page down."""
    try:
        local = config.load_local(STATE_FILE, config_dir) or {}
    except ValueError:
        local = {}
    try:
        has_token = bool(update_service.token(config_dir))
    except ValueError:
        has_token = False
    return {"checked_at": int(local.get("checked_at") or 0),
            "last": local.get("last") or None,
            "target": target(config_dir),
            "token": has_token,
            "authoring": authoring(config_dir)}


def _remember(config_dir, **fields):
    config.save_local(STATE_FILE, {"checked_at": int(time.time()),
                                   "last": fields}, config_dir)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

class Unreachable(ValueError):
    """GitHub could not be reached at all (no DNS, no route, a timeout).
    A ValueError like every other fetch failure, so the button's 400 path is
    unchanged; its own class so the wizard's refresh can call it "offline"
    rather than reading the message."""


#: A deadline (time.monotonic()) the current thread's GitHub reads must keep.
#: Thread-local rather than a parameter so _fetch keeps its one-argument
#: shape (the smokes swap it for a stub); only auto_refresh's worker sets it,
#: and the button keeps ghnet's own generous timeout.
_budget = threading.local()


def _timeout():
    deadline = getattr(_budget, "deadline", None)
    if deadline is None:
        return ghnet.TIMEOUT_S
    # Never zero (urllib reads that as non-blocking); a spent budget still
    # gets half a second so the read fails as a timeout, not a crash.
    return max(0.5, deadline - time.monotonic())


def _get(tgt, path, tok, limit):
    """One file from the target repo, as bytes; None when it is not there.
    Every other failure is a ValueError in the operator's words."""
    url = (f"https://api.github.com/repos/{tgt['owner']}/{tgt['repo']}"
           f"/contents/{path}?ref={tgt['branch']}")
    try:
        # The raw media type returns the file's bytes rather than a JSON
        # wrapper with a base64 body, and is not subject to the 1 MB cap the
        # wrapper has.
        with ghnet.open_url(url, tok, accept="application/vnd.github.raw+json",
                            agent="irivai-templates",
                            timeout=_timeout()) as resp:
            blob = resp.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ValueError(_explain(exc, tgt))
    except (urllib.error.URLError, OSError) as exc:
        raise Unreachable(f"could not reach GitHub: {exc}")
    if len(blob) > limit:
        raise ValueError(f"the published {path} is too large to be what the "
                         "developer hub publishes")
    return blob


def _fetch(config_dir=None):
    """The published formats.toml as bytes, verified against the signature
    published beside it -- or ValueError saying why not."""
    tgt = target(config_dir)
    tok = update_service.token(config_dir)
    where = f"{tgt['owner']}/{tgt['repo']}@{tgt['branch']}"
    blob = _get(tgt, tgt["path"], tok, MAX_BYTES)
    if blob is None:
        if not tok:
            raise ValueError(
                "this box has no update token, so it cannot read the published "
                "templates - set one in the System tab under the notification "
                "bell, where updates are checked")
        raise ValueError(
            f"the product owner has not published templates yet - there is "
            f"nothing at {where}/{tgt['path']}")
    sig = _get(tgt, tgt["path"] + ".sig", tok, MAX_SIG_BYTES)
    if sig is None:
        raise ValueError(
            "the published templates carry no signature, so this box will not "
            "install them - the developer hub publishes them signed once it "
            "holds config/store_signing.pem")
    # StoreError is a ValueError, so the route's 400 path takes it as is.
    store_service.verify_bytes(blob, sig.decode("ascii", "replace"),
                               config_dir, what="the published template file")
    return blob


def _explain(exc, tgt):
    """A GitHub error in the words of the person who has to act on it."""
    where = f"{tgt['owner']}/{tgt['repo']}@{tgt['branch']}"
    if exc.code == 401:
        return ("GitHub refused this box's update token - it has expired or "
                "was revoked")
    if exc.code == 403:
        return (f"GitHub refused the request (HTTP 403) - the update token may "
                f"not be allowed to read {where}, or GitHub is rate limiting "
                "this box; try again in a minute")
    return f"GitHub answered {exc.code} reading the templates from {where}"


def _parse(blob):
    """{key: raw entry} from the published file, or ValueError."""
    try:
        data = tomllib.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"the published template file is not valid: {exc}")
    formats = data.get("formats") if isinstance(data, dict) else None
    if not isinstance(formats, dict):
        raise ValueError("the published file holds no templates")
    return {str(k): v for k, v in formats.items() if isinstance(v, dict)}


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------

def entry_data(entry):
    """A raw formats.toml entry as the object save_format takes: the JSON
    blobs parsed back into the documents they hold, the state flags left
    behind. ValueError on a blob that is not JSON."""
    data = {k: entry.get(k)
            for k in ("label", "hint", "category", "classes", "widgets",
                      "zone", "model", "features", "blurb", "heading")
            if k in entry}
    # Named always: an upstream that dropped its own words clears them here.
    data.setdefault("features", [])
    data.setdefault("blurb", "")
    data.setdefault("heading", "")
    for blob_key, name in _BLOBS:
        raw = entry.get(blob_key)
        if raw:
            try:
                data[name] = json.loads(raw)
            except (TypeError, ValueError):
                raise ValueError(f"the template's {name} is not valid JSON")
    return data


def _same(key, data, local_entry):
    """Would installing `data` write exactly what the box already holds?

    Both sides in the STORED shape: the published entry is validated the way
    save_format would store it, and compared with the local record minus its
    state flags. Comparing the raw entries instead would call every blob that
    was re-serialised a change, and the button would report an update it had
    not made."""
    _key, want = provision_service.format_entry(key, data)
    have = {k: v for k, v in (local_entry or {}).items()
            if k not in _STATE_KEYS}
    return want == have


def _authored_here(config_dir, systems_dir):
    """Keys whose workbench system lives on this box. Those templates are
    authored here: the published copy is at best what this box sent, and at
    worst older than the edits in progress."""
    links = provision_service.format_workbench(config_dir)
    if not links:
        return set()
    if systems_dir is None:
        return set(links)
    try:
        live = set(provision_service.system_slugs(systems_dir, config_dir))
    except Exception:                                       # noqa: BLE001
        return set(links)
    return {key for key, slug in links.items() if slug in live}


def _named(key, entry):
    return {"key": key, "label": str((entry or {}).get("label") or key)}


def _fingerprint(entry):
    """A stable digest of a stored entry's CONTENT, state flags left out; ""
    for no entry at all."""
    if not entry:
        return ""
    body = {k: v for k, v in entry.items() if k not in _STATE_KEYS}
    raw = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _installed(config_dir=None):
    """{key: {base, entry}} of what earlier checks installed; {} unreadable."""
    try:
        raw = config.load_local(INSTALLED_FILE, config_dir) or {}
    except ValueError:
        return {}
    out = {}
    for key, rec in (raw.get("formats") or {}).items():
        if isinstance(rec, dict) and isinstance(rec.get("entry"), dict):
            out[str(key)] = {"base": str(rec.get("base") or ""),
                             "entry": rec["entry"]}
    return out


def _save_installed(config_dir, installed):
    config.save_local(INSTALLED_FILE, {"formats": installed}, config_dir)


def restore(config_dir=None):
    """Put back the templates earlier checks installed, after an update
    replaced formats.toml with the release's own. Called once at hub start.

    Per key: already what was installed -> nothing; the release still holds
    what it held before the install -> re-applied; the release changed or
    removed it since -> the release is newer than the check, it wins, and
    the record is dropped. Hidden here is left alone. Never on the source
    hub, which installs nothing. Returns the keys re-applied."""
    if authoring(config_dir):
        return []
    installed = _installed(config_dir)
    if not installed:
        return []
    local = provision_service._formats_overlay(config_dir)
    back, changed = [], False
    for key in sorted(installed):
        rec, have = installed[key], local.get(key)
        if have is not None and have.get("hidden"):
            continue
        now = _fingerprint(have)
        if now == _fingerprint(rec["entry"]):
            continue
        if now != rec["base"]:
            del installed[key]
            changed = True
            continue
        try:
            provision_service.save_format(
                key, dict(entry_data(rec["entry"]), draft=False), config_dir)
        except Exception as exc:                            # noqa: BLE001
            log.warning("[TEMPLATES] could not restore %s: %s", key, exc)
            continue
        back.append(key)
    if changed:
        _save_installed(config_dir, installed)
    return back


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

#: One check at a time. The button and the wizard's auto refresh both read,
#: compare and write formats.toml plus the installed record; two of them
#: interleaved could each compare against a file the other is rewriting.
_check_lock = threading.Lock()


def check(config_dir=None, systems_dir=None):
    """Fetch the published templates and install the new and changed ones --
    or, on the developer hub, say how they differ from what is here.

    Returns {ok, checked_at, authoring, added, updated, kept, skipped,
    unchanged, differs, unpublished, removed_here, errors, target}. The
    named lists are [{key, label}], labels as THIS box knows them; errors are
    [{key, error}]. A fetch that fails raises ValueError with the reason, and
    the failure is recorded so the wizard can show it beside the button.
    """
    with _check_lock:
        return _check(config_dir, systems_dir)


def _check(config_dir, systems_dir):
    """check() with _check_lock already held."""
    try:
        remote = _parse(_fetch(config_dir))
    except ValueError as exc:
        _remember(config_dir, ok=False, error=str(exc))
        raise
    local = provision_service._formats_overlay(config_dir)
    source_hub = authoring(config_dir)
    installed = {} if source_hub else _installed(config_dir)
    installed_before = dict(installed)
    authored = set() if source_hub else _authored_here(config_dir, systems_dir)

    added, updated, kept, skipped, unchanged, errors = [], [], [], [], [], []
    differs, removed_here = [], []
    for key in sorted(remote):
        entry = remote[key]
        if not provision_service._FORMAT_KEY_RE.match(key):
            skipped.append(key)
            continue
        # A draft upstream is the developer's half-built work, not a
        # published template; a hidden one was withdrawn. Neither installs.
        if entry.get("draft") or entry.get("hidden"):
            skipped.append(key)
            continue
        have = local.get(key)
        # Hidden HERE is this box's decision about the template; the
        # published copy does not reverse it.
        if have is not None and have.get("hidden"):
            skipped.append(key)
            continue
        try:
            data = entry_data(entry)
            if have is not None and _same(key, data, have):
                unchanged.append(key)
                continue
        # Whatever a published entry does to the validator -- a zone that is
        # a string, a blob that is not JSON -- one entry must never cost the
        # others, nor end the check with the box half-changed and no record.
        except Exception as exc:                            # noqa: BLE001
            errors.append({"key": key, "error": str(exc)})
            continue
        if source_hub:
            # This hub is where the templates come from: report, never write.
            (differs if have is not None else removed_here).append(
                _named(key, have if have is not None else entry))
            continue
        if have is not None and key in authored:
            kept.append(_named(key, have))
            continue
        try:
            # draft=False stated outright: a brand-new key would otherwise
            # start as a draft, and a published template that lands as a
            # draft is one the admin who pressed the button cannot see.
            provision_service.save_format(key, dict(data, draft=False),
                                          config_dir)
        except Exception as exc:                            # noqa: BLE001
            errors.append({"key": key, "error": str(exc)})
            continue
        (added if have is None else updated).append(_named(key, entry))
        # Remembered outside the release so an update does not undo it; the
        # base is what the RELEASE held, kept from the first install.
        prev = installed.get(key)
        stored = provision_service._formats_overlay(config_dir).get(key) or {}
        installed[key] = {
            "base": prev["base"] if prev else _fingerprint(have),
            "entry": {k: v for k, v in stored.items() if k not in _STATE_KEYS}}

    # On the source hub: templates here that the published copy does not
    # have yet -- the developer's new work, waiting for Publish.
    unpublished = []
    if source_hub:
        unpublished = [_named(k, v) for k, v in sorted(local.items())
                       if k not in remote and not v.get("draft")
                       and not v.get("hidden")]

    if installed != installed_before:
        _save_installed(config_dir, installed)

    result = {"ok": True, "checked_at": int(time.time()),
              "authoring": source_hub,
              "added": added, "updated": updated, "kept": kept,
              "skipped": skipped, "unchanged": unchanged,
              "differs": differs, "unpublished": unpublished,
              "removed_here": removed_here, "errors": errors,
              "target": target(config_dir)}
    # Preview media rides the same signed path; never fails the check.
    from core.services import template_media_sync           # noqa: PLC0415
    result.update(template_media_sync.after_check(config_dir, source_hub))
    _remember(config_dir, ok=True, authoring=source_hub,
              added=[t["key"] for t in added],
              updated=[t["key"] for t in updated],
              kept=[t["key"] for t in kept],
              differs=[t["key"] for t in differs],
              unpublished=[t["key"] for t in unpublished],
              removed_here=[t["key"] for t in removed_here],
              errors=len(errors))
    return result


# ---------------------------------------------------------------------------
# The wizard's automatic refresh
# ---------------------------------------------------------------------------
# Tok Aba, 2026-10-14: opening "New system" brings the templates up to the
# latest published version first, so a new system starts from the newest
# template -- and when the box is offline, has no token, or the download does
# not verify, it carries on with what it holds, silently. Three things make
# that safe to run on every wizard open:
#
#   * BOUNDED. Each GitHub read gets the time left of AUTO_BUDGET_S (see
#     _timeout), and the caller waits at most that long for the worker; DNS
#     is not covered by a socket timeout, so the wait is the real bound and a
#     worker that overruns finishes on its own and leaves its result cached.
#   * RATE LIMITED. One real network attempt per AUTO_WINDOW_S per box; a
#     call in between gets the cached answer. A box with no default route
#     (update_auto.has_route) is offline without a packet sent, and that is
#     not an attempt, so the first open after the cable goes back in checks.
#   * SINGLE FLIGHT. Concurrent opens join the one worker in flight rather
#     than starting their own, and the worker takes _check_lock like the
#     button does, so the two never interleave on formats.toml.
#
# Everything else about the check is the button's: the same _fetch with the
# same signature verification, the same merge with its protections (the
# source hub installs nothing, a template authored here is kept, hidden here
# stays hidden, a draft upstream is skipped). The answer is deliberately
# small -- {state, count_changed, changed, checked_at} -- and never carries
# the token or the reason text of a failure.

#: The longest the wizard waits for the refresh, and the per-read budget.
AUTO_BUDGET_S = 6.0

#: At most one real network attempt per this many seconds.
AUTO_WINDOW_S = 600

_auto_lock = threading.Lock()
_auto = {"at": None, "result": None, "done": None}
#: The last failure kind logged, so a box that stays offline logs it once.
_auto_warned = {"kind": None}


def _auto_answer(state, changed=(), cached=False):
    return {"state": state, "count_changed": len(changed),
            "changed": list(changed), "checked_at": int(time.time()),
            "cached": bool(cached)}


def summarise(result):
    """A check() result in the auto refresh's words: "updated" when anything
    was added or changed, "current" otherwise."""
    changed = [t["key"] for t in (result.get("added") or [])] \
        + [t["key"] for t in (result.get("updated") or [])]
    return _auto_answer("updated" if changed else "current", changed)


def note_checked(result):
    """The developer's button just ran a real check: count it, so the wizard
    opened right after does not fetch the same file again."""
    with _auto_lock:
        _auto["at"] = time.monotonic()
        _auto["result"] = summarise(result)


def _warn_once(kind, msg, *args):
    if _auto_warned["kind"] != kind:
        _auto_warned["kind"] = kind
        log.warning(msg, *args)


def _auto_work(config_dir, systems_dir, budget, done):
    """The worker: one check under the time budget, its answer cached."""
    _budget.deadline = time.monotonic() + budget
    try:
        if not _check_lock.acquire(timeout=budget):
            # The developer's button is mid-check: its result is about to be
            # what this box holds, so there is nothing to add by waiting.
            answer = _auto_answer("skipped")
        else:
            try:
                answer = summarise(_check(config_dir, systems_dir))
                _auto_warned["kind"] = None
            finally:
                _check_lock.release()
    except store_service.StoreError as exc:
        # Kept current: a download that does not verify is not installed.
        # The message is the verifier's -- it names the file, never the token.
        _warn_once("signature", "[TEMPLATES] auto refresh: the published "
                   "templates did not verify, keeping the ones here (%s)", exc)
        answer = _auto_answer("skipped")
    except Unreachable as exc:
        _warn_once("offline", "[TEMPLATES] auto refresh: %s", exc)
        answer = _auto_answer("offline")
    except ValueError as exc:
        # Reached GitHub but nothing usable came back -- the token refused,
        # nothing published, a file that is not valid. The operator carries
        # on with the templates on the device either way.
        _warn_once("skipped", "[TEMPLATES] auto refresh: %s", exc)
        answer = _auto_answer("skipped")
    except Exception as exc:                                # noqa: BLE001
        _warn_once("error", "[TEMPLATES] auto refresh failed: %s",
                   type(exc).__name__)
        answer = _auto_answer("skipped")
    finally:
        _budget.deadline = None
    with _auto_lock:
        _auto["result"] = answer
        _auto["done"] = None
    done.answer = answer
    done.set()


def auto_refresh(config_dir=None, systems_dir=None, budget=AUTO_BUDGET_S,
                 window=AUTO_WINDOW_S):
    """Bring the templates up to the latest published version, quietly.

    Returns {state: updated|current|offline|skipped, count_changed, changed,
    checked_at, cached}. Never raises: every failure is "offline" or
    "skipped", and the wizard carries on with the templates it has."""
    try:
        return _auto_refresh(config_dir, systems_dir, budget, window)
    except Exception as exc:                                # noqa: BLE001
        _warn_once("error", "[TEMPLATES] auto refresh failed: %s",
                   type(exc).__name__)
        return _auto_answer("skipped")


def _auto_refresh(config_dir, systems_dir, budget, window):
    # The source hub installs nothing (see the banner), so there is nothing
    # for the wizard to wait for; its developer has the compare button.
    if authoring(config_dir):
        return _auto_answer("skipped")
    try:
        has_token = bool(update_service.token(config_dir))
    except ValueError:
        has_token = False
    if not has_token:
        return _auto_answer("skipped")
    with _auto_lock:
        done = _auto["done"]
        if done is None:
            now = time.monotonic()
            if _auto["at"] is not None and now - _auto["at"] < window \
                    and _auto["result"] is not None:
                return dict(_auto["result"], cached=True)
            if not update_auto.has_route():
                return _auto_answer("offline")
            done = threading.Event()
            _auto["done"] = done
            _auto["at"] = now
            threading.Thread(target=_auto_work, name="templates-auto",
                             args=(config_dir, systems_dir, budget, done),
                             daemon=True).start()
    if done.wait(budget):
        return dict(done.answer)
    # Still going (a DNS lookup that hangs is not under any socket timeout):
    # the wizard does not wait for it. It finishes on its own and the next
    # open within the window gets what it found.
    return _auto_answer("offline")


def wait_auto(timeout=AUTO_BUDGET_S):
    """Block until an auto refresh in flight (if any) has finished, at most
    `timeout` seconds. Create calls this so a system is never scaffolded from
    a formats.toml the refresh is halfway through rewriting."""
    with _auto_lock:
        done = _auto["done"]
    if done is not None:
        done.wait(timeout)
