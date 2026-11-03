# -*- coding: utf-8 -*-
"""
update.py -- over-the-air updates of the platform itself.

A customer appliance has no shell and no git. This module is how it learns a
new version exists and how an admin applies it:

    check()    ask GitHub what the latest release is (cheap, ETag'd)
    apply()    hand the job to scripts/apply_update.py, which runs as its OWN
               systemd unit and does the download-verify-flip-restart-gate
    rollback() ask that same script to put the previous release back
    status()   what the running (or last) job did

Two rules shape everything here.

**This process must not be the one applying the update.** The hub spawns its
masters as children, so they share the hub's systemd cgroup; anything the hub
starts is killed the moment the update restarts irivai-hub -- including an
updater started that way, half-way through swapping the tree, with nothing
left alive to notice or roll back. So apply() does not fork a worker: it asks
systemd to start irivai-apply.service, which lives in its own cgroup and
survives the hub it is replacing.

**Nothing here writes to the live tree.** Verification, staging and the swap
all happen in the applier; this module reads status files and starts jobs.

Releases are published as one signed bundle in a private repo, one repo per
channel (config/update.toml). Each channel's device token is read-only,
scoped to its own repo alone, kept in config/update.local.toml at 0600, and
never appears in a URL, an argument list, a log line or an API response.
"""

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error

from core import config
from core.services import ghnet, hubport, update_auto
from core.version import VERSION, is_newer

#: Release repos, one per channel. config/update.toml [release] overrides.
DEFAULT_RELEASE = {"owner": "adamafiq0108", "repo": "irivai-release",
                   "dev_repo": "irivai-release-dev"}

#: The two release channels, each in a repo of its OWN, each read with a token
#: of its own (TOKEN_KEY). That is the access boundary: a fine-grained token
#: scopes to a repository, never to a tag or an asset, so a developer bundle
#: published beside the customer ones could be fetched with the read token
#: every customer box holds -- and the dev bundle carries core/dev and
#: scripts/dev as plain .py (Pipi gate 1.0.15-dev, M1). Dev bundles live in a
#: separate repo that only dev boxes hold a token for. The tag suffix below
#: still tells the channels apart, as a second fence.
CHANNEL_STABLE = "stable"
CHANNEL_DEV = "dev"
CHANNELS = (CHANNEL_STABLE, CHANNEL_DEV)

#: The key in config/update.local.toml holding each channel's token. Never one
#: for both: the customer token must never be sent to the dev repo, nor the dev
#: token to the customer one, and a customer box never holds `dev_token`.
TOKEN_KEY = {CHANNEL_STABLE: "token", CHANNEL_DEV: "dev_token"}

#: What a channel with no token says. Plain words: on the Developer build tab
#: this is the normal state of a dev box nobody has given the dev token yet.
NO_TOKEN = {CHANNEL_STABLE: "no update token is configured for this box",
            CHANNEL_DEV: ("no update token is configured for developer builds "
                          "on this box - paste the developer token to get them")}

#: Tag marker for a developer release: v2.1.3-dev. `core.version.parse` stops
#: at the first non-digit in each part, so this compares as 2.1.3 and orders
#: correctly against customer releases.
DEV_SUFFIX = "-dev"

#: Where each channel's cached check answer lives under the update dir.
#: Separate files: one channel must never overwrite the other's idea of what
#: the latest release is, or an admin would be offered a version that belongs
#: to the other channel entirely.
CHECK_FILE = {CHANNEL_STABLE: "check.json", CHANNEL_DEV: "check-dev.json"}

#: OTA signing public key (ed25519, raw, base64). Deliberately NOT the store
#: key: an update rewrites the code that verifies everything else, so it gets
#: a key of its own. Filled in at first keygen; config/update.toml
#: [signing].pubkey overrides.
PUBKEY_B64 = "TjgmysV+4XU8VRGl7UqzQNaE7azT6dvuiqF7MLIUcLE="

#: Manifest `app` names, one per channel. The applier refuses a manifest whose
#: app is not the one its job asked for, so a dev bundle handed to a box on the
#: stable channel is rejected before a single byte of it is unpacked -- even
#: though both channels are signed by the same OTA key.
MANIFEST_APP = "irivai-platform-release"
MANIFEST_APP_DEV = "irivai-platform-release-dev"

#: Asset names inside a release. Fixed, so a box knows what to fetch without
#: guessing from a listing.
BUNDLE_ASSET = "bundle.tar.gz"
MANIFEST_ASSET = "manifest.json"
SIG_ASSET = "manifest.sig"

#: The oneshot unit that actually applies an update (scripts/install.sh).
APPLY_UNIT = "irivai-apply.service"

#: Terminal phases: a status in one of these describes a finished job.
DONE_PHASES = ("idle", "done", "failed", "rolled_back", "refused")


class UpdateError(ValueError):
    """An update failure with an operator-readable message."""


class UpdateBusy(UpdateError):
    """A job is already running.

    Its own class because the route has to answer 409 rather than 400 for
    this one case, and it used to tell them apart by searching the message
    for "already running". A substring match on prose is a trap: reword the
    sentence, or translate it, and a double-clicked Update button silently
    goes back to reading as a bad request the operator could fix.
    """


class UpdateSuperseded(UpdateError):
    """The version Update was pressed for is no longer the newest one.

    Raised by apply() after it asks GitHub again, and carries `newest` so the
    route can say which version the card now offers. Its own class for the
    same reason as UpdateBusy: the browser has to tell "press Update again"
    apart from "the update failed", and must not do it by reading prose.
    """

    def __init__(self, newest, asked):
        self.newest = str(newest or "").lstrip("vV")
        self.asked = str(asked or "").lstrip("vV")
        super().__init__(f"version {self.newest} was published since this box "
                         f"last checked and is newer than {self.asked} - "
                         "press Update again to install it")


# ---------------------------------------------------------------------------
# Paths and settings
# ---------------------------------------------------------------------------

def update_dir():
    """Where check/status/staging live: under the store dir, on the NVMe when
    there is one, and OUTSIDE the code tree -- these files must survive the
    very swap they are describing."""
    path = os.path.join(str(config.store_dir()), "update")
    os.makedirs(path, exist_ok=True)
    return path


def _p(name):
    return os.path.join(update_dir(), name)


def channel_name(channel=None):
    """Normalise a channel name, refusing anything not in CHANNELS.

    Every caller that came from a request goes through here first: a channel
    reaches this module as a string a browser sent, and it decides which repo
    is contacted and which token is spent.
    """
    name = str(channel or CHANNEL_STABLE).strip().lower()
    if name not in CHANNELS:
        raise UpdateError(f"unknown update channel {name!r}")
    return name


def job_hub_port(config_dir=None):
    """The port the hub answers on once an update restarts it."""
    if hubport.source() == "argv":
        return hubport.running_port() or hubport.port(config_dir)
    return hubport.port(config_dir)


def signing_pubkey(config_dir=None):
    """The OTA key a bundle must be signed with.

    On a CUSTOMER box only the constant and the COMMITTED config/update.toml
    count: config/update.local.toml is the machine-writable half (the token
    page writes it, through config.save_local, as the service account), and
    a [signing].pubkey slipped into it -- by a bug in any writer, or by
    anything running as that account -- would let a bundle signed with
    someone else's key install itself as the platform. The committed file
    ships with the release that carries this code. A developer box still
    honours the local override, which is how a test key is tried.

    "Developer box" is read from the committed config/deploy.toml alone
    (what install.sh writes), not merged with a deploy.local.toml for the
    same reason. What this does NOT claim: the install tree is the service
    account's, so code running as it can rewrite the committed files too.
    This closes the config-writer path, not code execution.
    """
    base = config.root(config_dir)
    try:
        mode = config._read(base / "deploy.toml").get("mode", "customer")
        dev = str(mode).strip().lower() == "dev"
    except ValueError:
        dev = False
    if dev:
        sign = (config.load("update", config_dir) or {}).get("signing") or {}
    else:
        try:
            sign = config._read(base / "update.toml").get("signing") or {}
        except ValueError:
            sign = {}
    return str(sign.get("pubkey") or PUBKEY_B64)


def settings(config_dir=None, channel=None):
    channel = channel_name(channel)
    cfg = config.load("update", config_dir) or {}
    rel = cfg.get("release") or {}
    chk = cfg.get("check") or {}
    try:
        hours = float(chk.get("interval_hours") or 6)
    except (TypeError, ValueError):
        hours = 6.0
    key = "dev_repo" if channel == CHANNEL_DEV else "repo"
    return {
        "channel": channel,
        "owner": str(rel.get("owner") or DEFAULT_RELEASE["owner"]),
        # Each channel's OWN repo, never a fallback to the other's: a dev box
        # that lost dev_repo must not go reading the customer repo with its
        # dev token (or offer customer releases as developer ones).
        "repo": str(rel.get(key) or DEFAULT_RELEASE[key]),
        "pubkey": signing_pubkey(config_dir),
        # The manifest's `app` differs per channel even though the key and the
        # repo do not. It is the one guard that survives everything else: the
        # applier refuses a manifest whose app is not the one its job asked
        # for, so a developer bundle handed to a box on the customer channel is
        # rejected before a byte of it is unpacked.
        "manifest_app": MANIFEST_APP_DEV if channel == CHANNEL_DEV else MANIFEST_APP,
        "interval_s": max(900.0, hours * 3600.0),
    }


def dev_available(config_dir=None):
    """Whether the developer channel is offered on THIS box at all.

    The lock that matters, and the reason it is here rather than in a route:
    `is_dev_box()` reads config/deploy.toml, which no API and no UI can write
    (config/deploy.toml says so, and means it). So an admin on a customer
    appliance who creates an account, a role, or anything else named "dev"
    changes nothing -- there is no dev channel on that box to be let into.
    Developer-ness of the ACCOUNT is checked separately, in the route; both
    have to be true, and both fail closed.

    What makes the dev bundle unreachable as well as un-offered is that it
    lives in a repo of its own, read with a token only dev boxes hold. See
    the CHANNELS note above.
    """
    return bool(config.is_dev_box(config_dir))


def require_channel(channel, config_dir=None):
    """Normalise, then refuse the dev channel on a box that does not have one.

    Belt to the route's braces: the service refuses even if a caller reached
    it without passing the route's developer check.
    """
    channel = channel_name(channel)
    if channel == CHANNEL_DEV and not dev_available(config_dir):
        raise UpdateError("the developer channel is not available on this box")
    return channel


def token(config_dir=None, channel=None):
    """`channel`'s read-only release token, or ''. Never returned by any
    route -- callers get a boolean. Each channel reads its own key only."""
    key = TOKEN_KEY[channel_name(channel)]
    return str((config.load_local("update", config_dir) or {}).get(key)
               or "").strip()


def set_token(value, config_dir=None, channel=None):
    """Store (or clear) `channel`'s release token, leaving the other channel's
    alone. 0600 and atomic via save_local."""
    key = TOKEN_KEY[channel_name(channel)]
    value = str(value or "").strip()
    with update_auto.local_lock:                 # same file as the switch
        local = dict(config.load_local("update", config_dir) or {})
        if value:
            local[key] = value
        else:
            local.pop(key, None)
        config.save_local("update", local, config_dir)
    return bool(value)


# ---------------------------------------------------------------------------
# Small JSON files, written atomically. Every one of these is read by another
# process (the applier, the boot-time recovery script, the next hub) mid-
# update, so a torn file is a real failure mode, not a theoretical one.
# ---------------------------------------------------------------------------

def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else (default or {})
    except (OSError, ValueError):
        return default if default is not None else {}


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------

def _api(path, tok, accept="application/vnd.github+json", headers=None,
         config_dir=None, channel=None):
    s = settings(config_dir, channel)
    url = f"https://api.github.com/repos/{s['owner']}/{s['repo']}{path}"
    return ghnet.open_url(url, tok, accept=accept, agent="irivai-update",
                          headers=headers)


def assets_in_repo(assets, owner, repo):
    """True when every asset URL in `assets` belongs to `owner/repo`.

    The token rides with each asset request. A cache written against another
    repo (the dev channel before it moved to a repo of its own) would send
    this channel's token to that repo; the applier checks the same rule.
    """
    prefix = f"https://api.github.com/repos/{owner}/{repo}/releases/assets/"
    return all(str((a or {}).get("url") or "").lower().startswith(prefix.lower())
               for a in (assets or {}).values())


def is_dev_tag(tag):
    """True for a developer release tag (v2.1.3-dev). Case-insensitive: a tag
    is typed by a person, and `-DEV` reaching a customer box as a customer
    release is not a mistake worth allowing."""
    return str(tag or "").strip().lower().endswith(DEV_SUFFIX)


def is_customer_tag(tag):
    """True only for a plain numeric release tag: v2, v2.1, v2.1.3.

    Deliberately not "anything that is not a dev tag". This predicate is what
    decides whether a tag someone typed is offered to the entire fleet -- and every near miss should fall out of the
    customer channel rather than into it. `v2.1.3-devel`, `v2.1.3-dev-rc1`,
    `v2.1.3-rc1` and `nightly` are all NOT customer releases: a box that ends
    up offered one would download it, fail the manifest's app check, and report
    a failed update for a release that was never meant for it.

    Nothing is lost by being strict. A tag that means to reach appliances can
    always be spelled as a version.
    """
    s = str(tag or "").strip().lstrip("vV")
    if not s:
        return False
    parts = s.split(".")
    return len(parts) <= 3 and all(p.isdigit() for p in parts)


def _pick(releases, channel):
    """The newest release belonging to `channel`, or None.

    Drafts and prereleases are skipped outright -- a draft has no downloadable
    assets, and a prerelease is a release someone marked as not-ready. Ordering
    is by parsed version rather than by the list's own order, so a release
    re-published or edited later cannot jump the queue by date.
    """
    if not isinstance(releases, list):
        return None
    from core.version import parse
    want_dev = channel_name(channel) == CHANNEL_DEV
    kept = []
    for r in releases:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        tag = str(r.get("tag_name") or "").strip()
        # Each channel names what it WANTS, and a tag matching neither belongs
        # to neither. An "anything else is a customer release" rule would hand
        # every experiment and mis-typed tag in the repo to the whole fleet.
        if not tag or not (is_dev_tag(tag) if want_dev else is_customer_tag(tag)):
            continue
        kept.append((parse(tag), tag, r))
    if not kept:
        return None
    kept.sort(key=lambda item: item[0])
    return kept[-1][2]


#: What a check that never got an answer from GitHub says, by cause. The raw
#: exception ("<urlopen error [Errno 113] No route to host>") rides in
#: `details` for whoever reads the log; as the sentence it sent an operator
#: to an errno table (board report). ECONNREFUSED is in the offline set on
#: purpose: api.github.com does not refuse, a captive portal or a dead proxy
#: on the way does.
CHECK_OFFLINE = ("No internet connection - check the cable, Wi-Fi or the "
                 "default route")
CHECK_UNREACHABLE = "Could not reach GitHub - the box will try again later"
#: ENETUNREACH, ETIMEDOUT, ECONNREFUSED, EHOSTUNREACH, and getaddrinfo's
#: EAI_NONAME, EAI_AGAIN, EAI_NODATA (DNS is the first thing to fail with no
#: uplink).
OFFLINE_ERRNOS = frozenset({101, 110, 111, 113, -2, -3, -5})


def reach_error(exc):
    """The operator's sentence for `exc`, a failure to reach GitHub at all.

    urllib wraps the socket's error as URLError.reason -- an OSError with an
    errno, a socket.timeout, or now and then a plain string spelling the
    errno ("[Errno -3] ...") -- so the number is read from whichever it is.
    """
    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(cause, (socket.timeout, TimeoutError)):
        return CHECK_OFFLINE
    errno = getattr(cause, "errno", None)
    if errno is None:
        m = re.search(r"\[Errno (-?\d+)\]", str(cause))
        errno = int(m.group(1)) if m else None
    if errno in OFFLINE_ERRNOS or "timed out" in str(cause).lower():
        return CHECK_OFFLINE
    return CHECK_UNREACHABLE


def _heal(config_dir=None):
    from core.services import routeheal                       # noqa: PLC0415
    return routeheal.heal_for_retry(config_dir)


def _explain():
    """routeheal's sentence for a winning default route that is a known dead
    end (board .47: a gateway that is the box itself), "" otherwise."""
    from core.services import gatewayfault                    # noqa: PLC0415
    return gatewayfault.explain()


def is_offline(error):
    """Is `error` the "no internet" family: CHECK_OFFLINE, or its worded
    form naming the dead route ("No internet: port eth1's gateway ...")."""
    error = str(error or "")
    return error == CHECK_OFFLINE or error.startswith("No internet:")


#: One GitHub conversation at a time. Two "Check now" clicks (or a click
#: while the timer's own check is out) used to send two requests and hold
#: two route workers for up to a 60 s timeout each; the second caller now
#: gets the cached state back at once, marked `checking`, and the first
#: answer lands in the cache for both. Nothing here waits on the loop: the
#: routes call check() through asyncio.to_thread.
_check_lock = threading.Lock()


def check(force=False, config_dir=None, channel=None, wait=False, counted=True):
    """Ask GitHub for the latest release and cache the answer.

    Cheap by design: the previous ETag goes out as If-None-Match, so an
    up-to-date box gets a 304 that costs no rate limit and no body. Every
    failure mode is recorded rather than raised, because this runs on a timer
    -- a box with a flaky uplink must show "could not check", not lose the
    last good answer.

    `wait`: a caller that must not skip (the background timer) queues behind
    a running check; a route does not, and answers `checking` instead.
    `counted`: a failure counts toward the "could not check" notice
    (_check_notice); apply()'s confirm check passes False.
    """
    channel = require_channel(channel, config_dir)
    if not _check_lock.acquire(blocking=wait):
        return dict(public_state(None, channel, config_dir), checking=True)
    try:
        # Its own answer, so not "checking" (the lock is still held here).
        return dict(_check(force, config_dir, channel, counted), checking=False)
    finally:
        _check_lock.release()


def _check(force, config_dir, channel, counted=True):
    """check() with the lock held."""
    cached = _read_json(_p(CHECK_FILE[channel]))
    s = settings(config_dir, channel)
    if not assets_in_repo(cached.get("assets"), s["owner"], s["repo"]):
        # From another repo: its release, ETag and URLs mean nothing here.
        cached = {k: v for k, v in cached.items()
                  if k not in ("latest", "notes", "assets", "etag")}
    tok = token(config_dir, channel)
    out = {"current": VERSION, "channel": channel,
           "latest": cached.get("latest") or "",
           "notes": cached.get("notes") or "", "assets": cached.get("assets") or {},
           "publishing": bool(cached.get("publishing")),
           "etag": cached.get("etag") or "",
           "checked_at": cached.get("checked_at") or 0,
           "update_available": False, "error": "", "details": "",
           "token_set": bool(tok)}
    if not tok:
        # Reported, not written: erasing the cache here would throw away the
        # ETag and the release this box already knows about, so re-pasting a
        # token would cost a full re-fetch and leave Update refusing until the
        # next check landed.
        out["error"] = NO_TOKEN[channel]
        # ...but an outage notice from before the token was removed goes:
        # nothing will ever check again to withdraw it. Only the streak
        # fields are written back, so the cache (ETag, release) is kept.
        if cached.get("check_notice") or cached.get("check_fails"):
            _check_notice(out, cached, channel, config_dir)
            _write_json(_p(CHECK_FILE[channel]),
                        dict(cached, check_fails=out["check_fails"],
                             check_notice=out["check_notice"]))
        return public_state(out, channel, config_dir)

    headers = {}
    if out["etag"] and not force:
        headers["If-None-Match"] = out["etag"]
    try:
        # Listed and filtered, NOT /releases/latest. Each channel has its own
        # repo now, but the tag filter stays as a second fence: a dev release
        # mis-published to the customer repo is still never offered.
        def ask():
            with _api("/releases?per_page=30", tok, headers=headers,
                      config_dir=config_dir, channel=channel) as resp:
                return json.loads(resp.read(4 << 20) or b"[]"), resp.headers.get("ETag")
        try:
            body, etag = ask()
        except (urllib.error.URLError, OSError) as exc:
            # Before "no internet": a dead default route beside a live one
            # (board .22) is fixed once and asked again (routeheal.py).
            if isinstance(exc, urllib.error.HTTPError) \
                    or reach_error(exc) != CHECK_OFFLINE or not _heal(config_dir):
                raise
            body, etag = ask()
        out["etag"] = etag or out["etag"]
        rel = _pick(body, channel)
        if rel is None:
            out["latest"], out["notes"], out["assets"] = "", "", {}
            out["publishing"] = False
            out["error"] = ("no release has been published on this channel "
                            "yet")
        else:
            out["latest"] = str(rel.get("tag_name") or "").strip()
            out["notes"] = str(rel.get("body") or "")[:4000]
            # Asset API URLs, not browser URLs: the API url is what a private
            # repo serves with a token. The signed storage URL it redirects to
            # is never stored -- it carries short-lived credentials in its
            # query string.
            assets = {}
            for a in (rel.get("assets") or []):
                name = str(a.get("name") or "")
                if name in (BUNDLE_ASSET, MANIFEST_ASSET, SIG_ASSET):
                    assets[name] = {"url": str(a.get("url") or ""),
                                    "size": int(a.get("size") or 0)}
            out["assets"] = assets
            # The publisher creates the release first and uploads its three
            # assets after it (scripts/dev/publish_release.py), so a check
            # that lands in between sees the newest release with nothing to
            # download yet. It is STILL the newest -- falling back to the one
            # below it is how a box ends up installing an older version --
            # so it is offered as "being published" and apply() waits for
            # the assets rather than installing anything else.
            out["publishing"] = not all(
                n in assets for n in (BUNDLE_ASSET, MANIFEST_ASSET, SIG_ASSET))
            out["error"] = ""
    except urllib.error.HTTPError as exc:
        if exc.code == 304:                     # unchanged -- keep everything
            # A 304 confirms the CACHED answer, and that answer can be "this
            # channel has no release yet" (latest empty). Blanking the error
            # here would leave the panel silent -- neither up to date nor
            # empty -- until the next forced fetch.
            out["error"] = ("" if out["latest"] else
                            "no release has been published on this channel "
                            "yet")
        elif exc.code == 401:
            # Fine-grained tokens expire (a year at most). This has to read as
            # its own condition: "check failed" sends someone hunting a
            # network fault that is not there.
            out["error"] = "token_expired"
        elif exc.code == 404:
            out["error"] = ("no releases found - check the release repo "
                            "setting and the token's access")
        elif exc.code in (403, 429):
            out["error"] = "GitHub is rate-limiting this box; try again later"
        else:
            out["error"] = f"GitHub answered {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out["error"] = reach_error(exc)
        out["details"] = f"could not reach GitHub ({exc})"
        if out["error"] == CHECK_OFFLINE:
            # Name the dead default route when there is one to name.
            out["error"] = _explain() or CHECK_OFFLINE

    out["current"] = VERSION
    out["update_available"] = bool(
        out["latest"] and is_newer(out["latest"], VERSION))
    out["checked_at"] = int(time.time())
    if counted or not _check_failed(out.get("error")):
        _check_notice(out, cached, channel, config_dir)
    else:
        # An Update press's own confirm check that got no answer: the press
        # is refused in words on the page; it is not a miss of the timer's
        # and with "Check for updates automatically" off two refused presses
        # posted a notice promising the box "will try again later" when it
        # would not (Yaya, 2026-10-05). The streak is left as it was.
        out["check_fails"] = int(cached.get("check_fails") or 0)
        out["check_notice"] = str(cached.get("check_notice") or "")
    _write_json(_p(CHECK_FILE[channel]), out)
    return public_state(out, channel, config_dir)


#: A failed check is said out loud only when it happens this many times IN A
#: ROW. One miss is a Wi-Fi blip on a box that will check again by itself;
#: a notice for it was a permanent red line on the Important tab that nothing
#: the operator did could clear (beta feedback).
CHECK_FAILS_NOTICE = 2
CHECK_NOTICE_TITLE = "Could not check for updates"
CHECK_NOTICE_BODY = ("Could not check for updates - no internet connection; "
                     "the box will try again later")
#: ...said by CAUSE: only a failure to reach GitHub at all is "no internet".
#: A 404 is a setup fault that waiting will not fix; a rate limit is GitHub
#: asking the box to slow down.
CHECK_NOTICE_SETUP = ("Could not check for updates - the release settings or "
                      "the update token need attention")
CHECK_NOTICE_LIMITED = ("GitHub is limiting requests; the box will try again "
                        "later")
CHECK_NOTICE_OTHER = ("Could not check for updates - GitHub answered with an "
                      "error; the box will try again later")
CHECK_NOTICE_UNREACHABLE = ("Could not check for updates - could not reach "
                            "GitHub; the box will try again later")


def _check_notice_body(error):
    """The notice's sentence for a counted failure `error` (check())."""
    error = str(error or "")
    if is_offline(error):
        return CHECK_NOTICE_BODY
    if error == CHECK_UNREACHABLE:
        return CHECK_NOTICE_UNREACHABLE
    if error.startswith("no releases found"):
        return CHECK_NOTICE_SETUP
    if error.startswith("GitHub is rate-limiting"):
        return CHECK_NOTICE_LIMITED
    return CHECK_NOTICE_OTHER


def _check_failed(error):
    """Is `error` a failed CHECK (the box could not get an answer), as
    against an answer: an expired token has a notice of its own, and "no
    release yet" or a missing token is not a fault of the connection."""
    return bool(error) and error != "token_expired" \
        and not error.startswith("no release has been published") \
        and not error.startswith("no update token")


def _check_notice(out, cached, channel, config_dir):
    """Count consecutive failed checks; post ONE ordinary, dismissable inbox
    notice at the CHECK_FAILS_NOTICE-th, and take it away again at the next
    check that gets an answer. The raw error rides along as `detail` (shown
    on hover), not as the sentence."""
    from core.services import inbox                            # noqa: PLC0415
    fails = int(cached.get("check_fails") or 0)
    notice = str(cached.get("check_notice") or "")
    if _check_failed(out.get("error")):
        fails += 1
        if fails >= CHECK_FAILS_NOTICE and not notice:
            try:
                where = " (developer build)" if channel == CHANNEL_DEV else ""
                notice = inbox.notify_admins(
                    "update_check", CHECK_NOTICE_TITLE + where,
                    _check_notice_body(out.get("error")), config_dir,
                    page="settings", tab="system",
                    detail=str(out.get("details") or out.get("error") or ""))["id"]
            except Exception:                                  # noqa: BLE001
                notice = ""
    else:
        fails = 0
        if notice:
            try:
                inbox.delete("", notice, is_admin=True, config_dir=config_dir)
            except Exception:                                  # noqa: BLE001
                pass
            notice = ""
    out["check_fails"], out["check_notice"] = fails, notice


def public_state(raw=None, channel=None, config_dir=None):
    """What a route may return: never the ETag, never an asset URL, never a
    hint of the token beyond whether one is set.

    `config_dir` is threaded through rather than defaulted: a deployment whose
    hub runs with a config root of its own would otherwise be asked whether the
    DEFAULT root has a token, and answer "no token configured" on a box that
    has one -- with the panel offering a token box for a token already stored.
    """
    channel = channel_name(channel)
    raw = raw if raw is not None else _read_json(_p(CHECK_FILE[channel]))
    latest = raw.get("latest") or ""
    return {"current": VERSION,
            "channel": channel,
            "latest": latest,
            "notes": raw.get("notes") or "",
            "checked_at": int(raw.get("checked_at") or 0),
            # Recomputed against the version RUNNING NOW, never read back
            # from the cache. The cache lives on the store and outlives the
            # release that wrote it: checked while v1.0.11 ran, it says
            # update_available for v1.0.12 -- and after the update to v1.0.12
            # it still said so until the next check hours later, so the box
            # went on offering the update it had just installed. A release
            # re-cut under the same version is not newer either.
            "update_available": bool(latest and is_newer(latest, VERSION)),
            # The newest release is listed but its files are not all up yet
            # (see _check): shown as such, and apply() refuses it meanwhile.
            "publishing": bool(latest and raw.get("publishing")),
            "error": raw.get("error") or "",
            # The failure's own text, for the hover; never the sentence.
            "details": raw.get("details") or "",
            "token_set": bool(token(config_dir, channel)),
            # The version, not the directory: a same-version reinstall from
            # a file builds v<version>-r<n> (apply_update.release_target).
            "previous": re.sub(r"-r\d+$", "", previous_version()),
            "busy": bool(job_running()),
            # A check is out right now (the timer's or another admin's).
            "checking": _check_lock.locked(),
            # "Check for updates automatically" (update_auto.py), one switch
            # for both channels; off unless an admin turned it on.
            "auto_check": update_auto.enabled(config_dir)}


def state(channel=None, config_dir=None):
    return public_state(None, channel, config_dir)


# ---------------------------------------------------------------------------
# The background checker
# ---------------------------------------------------------------------------

def start_checker(config_dir=None):
    """Start the background checker (core/services/update_auto.py).

    The loop always runs; it goes online only while an admin has "Check for
    updates automatically" switched on, which is OFF on every box (Tok Aba,
    2026-10-05). Returns whether the switch is on.
    """
    return update_auto.start(config_dir)


# ---------------------------------------------------------------------------
# Job status -- shared with scripts/apply_update.py, which is the writer
# ---------------------------------------------------------------------------

def _boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _alive(pid):
    # pid 0 is not "no process": to os.kill it means THIS process group, so
    # the check always succeeds and a handoff that never reached the updater
    # would read as an update running forever -- with the Update button
    # refusing every attempt until someone power-cycled the box.
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def job_running():
    """True only when an applier is genuinely alive right now.

    Freshness cannot be judged by mtime: these boards often have no RTC, so
    the clock jumps when NTP lands and a minutes-old file can look like it is
    from tomorrow. Identity is what survives that -- the boot id proves the
    status was written since this boot, and the pid proves the writer is still
    there. Anything else is a job that died, and a dead job must not wedge the
    Update button forever.
    """
    raw = _read_json(_p("status.json"))
    if not raw or raw.get("phase") in DONE_PHASES:
        return False
    if raw.get("boot_id") and raw["boot_id"] != _boot_id():
        return False
    return _alive(raw.get("pid"))


def reboot_pending():
    """Has an update finished that this box has not restarted since?

    The applier swaps the tree and restarts the hub and its masters, which is
    what makes the new version serve the page. It does NOT restart everything
    on the board: the detection apps, the media server, the front panel and
    the root helpers keep running the code they started with, so until the box
    comes up again it is part new version and part old. That is exactly the
    state nobody can see, and the one that produces "I updated and it did not
    change".

    True only when a job FINISHED and the box has not booted since. Boot time
    from /proc/uptime rather than the boot id, which says only whether the id
    changed, not which came first. A board with no /proc answers False: an
    invented prompt to reboot is worse than none.
    """
    raw = _read_json(_p("status.json"))
    if raw.get("phase") != "done":
        return False
    started = float(raw.get("started") or 0)
    if not started:
        return False
    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            up = float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    # Booted before the job began, so this box has not restarted since it.
    return (time.time() - up) < started


def status():
    """The current or last job, plus whether it is really running."""
    raw = _read_json(_p("status.json"))
    return {"phase": raw.get("phase") or "idle",
            "percent": int(raw.get("percent") or 0),
            "detail": raw.get("detail") or "",
            "error": raw.get("error") or "",
            "version": raw.get("version") or "",
            "started": int(raw.get("started") or 0),
            "reboot_pending": reboot_pending(),
            "running": job_running()}


# ---------------------------------------------------------------------------
# Maintenance flag -- read by the hub's autostart and by provisioning
# ---------------------------------------------------------------------------

def maintenance():
    """True while an update is swapping the tree. The hub stops respawning
    systems and refuses to create or delete them for that window: a system
    written while the code under it is being replaced belongs to neither
    version."""
    return os.path.exists(_p("maintenance"))


# ---------------------------------------------------------------------------
# Releases on disk
# ---------------------------------------------------------------------------

#: Where install.sh records what it actually built. Read rather than assumed:
#: the installer takes a --target, so /opt is a default and not a law, and a
#: box installed anywhere else was being told it could not take updates while
#: sitting on a perfectly good release layout.
INSTALL_CONF = "/etc/irivai/update.conf"


def _installed(key):
    """One value out of /etc/irivai/update.conf, or ''.

    Parsed by hand and defensively: this file is written by a shell script, is
    read at boot by the recovery script as well, and a malformed line in it
    must degrade to "fall back to the default", never to an exception on the
    path that decides whether the Update button works at all.
    """
    try:
        with open(INSTALL_CONF, encoding="utf-8") as f:
            for line in f:
                name, sep, value = line.partition("=")
                if sep and name.strip() == key:
                    value = value.strip().strip('"').strip("'")
                    # Absolute paths only. An installer that captured a warning
                    # banner into the value once already; a relative or empty
                    # answer means the file is not to be trusted for this key.
                    return value if os.path.isabs(value) else ""
    except OSError:
        pass
    return ""


def releases_dir():
    """Where installed releases live, one directory each.

    What the installer recorded, else derived from where THIS code is running:
    on an appliance the live path is a symlink into this directory. A developer
    checkout has no such parent, and apply() refuses there.
    """
    recorded = _installed("RELEASES_DIR")
    if recorded:
        return recorded
    here = os.path.realpath(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.dirname(here)


def live_link():
    """The stable path the systemd units point at (a symlink on an appliance).

    /opt/irivai-systor is the default target, not the only one -- install.sh
    takes --target, and a box installed under a home directory is a normal
    thing. Hardcoding /opt meant ota_ready() answered False on such a box and
    the Update button refused to work on an installation that was correct in
    every respect.
    """
    return _installed("LIVE_LINK") or "/opt/irivai-systor"


def ota_ready():
    """True when this box is laid out for OTA: the live path is a symlink into
    a releases directory. A source checkout, a dev box, or an older appliance
    that predates the layout answers False -- and the routes say so plainly
    instead of failing half-way through an update."""
    try:
        link = live_link()
        if not os.path.islink(link):
            return False
        target = os.path.realpath(link)
        return os.path.isdir(target) and os.path.basename(
            os.path.dirname(target)) == os.path.basename(releases_dir())
    except OSError:
        return False


def previous_version():
    """The version an admin can roll back to: the newest installed release
    that is not the running one.

    Only on a box laid out for OTA. Without that guard a developer checkout
    lists whatever folders happen to sit beside it and offers one of them as a
    version to "roll back to" -- a button that means nothing, on the page
    where buttons must mean exactly what they say.

    Not the running one, and not a twin of it either: a same-version
    reinstall from a file builds v<ver>-r<n> beside v<ver>, and with both
    on disk Roll back went v<ver> <-> v<ver>-r<n> for ever while the
    genuinely older release (kept for exactly this, apply_update
    prune_releases) was out of reach (Yaya's rehearsal, 2026-10-05). A
    directory whose VERSION file reads the running version is skipped; of
    the rest the newest wins, a later -r<n> over its plain twin.
    """
    if not ota_ready():
        return ""
    try:
        names = [n for n in os.listdir(releases_dir())
                 if os.path.isdir(os.path.join(releases_dir(), n))]
    except OSError:
        return ""
    running = os.path.basename(os.path.realpath(live_link()))
    from core.version import parse

    def version_of(name):
        try:
            with open(os.path.join(releases_dir(), name, "VERSION"), encoding="utf-8") as f:
                return parse(f.read())
        except OSError:
            return parse(name)

    def order(name):
        m = re.search(r"-r(\d+)$", name)
        return parse(name), int(m.group(1)) if m else 0
    others = [n for n in names if n != running and version_of(n) != parse(VERSION)]
    if not others:
        return ""
    others.sort(key=order)
    return others[-1].lstrip("vV")


#: The version the admins were last told about, so the notice below goes out
#: once per version and not on every hub restart.
ANNOUNCED_FILE = "announced.json"

#: Lines that are about the commit, not the release (git trailers), and the
#: shape of a line that IS a release note: "Area:" alone or "Area: change".
#: Same rules as frontend/src/components/releaseNotes.js, which draws the
#: Update card -- the notice must never show what the card would have hidden.
_TRAILER_RE = re.compile(r"^\s*(Co-Authored-By|Signed-off-by|Reviewed-by|Cc)\s*:", re.I)
_SUBJECT_RE = re.compile(r"^(chore|docs|feat|fix|refactor|test|build|ci)(\([^)]*\))?!?:", re.I)
_NOTE_LINE_RE = re.compile(r"^(?:[-*•·]\s+)?(?:[IVXivx]+\.\s+)?[A-Za-z][A-Za-z0-9 /&.]{0,48}:(\s|$)")


def clean_notes(text):
    """Release notes fit to show a person, or "" when the text is not notes.

    A release body once carried the tag's COMMIT message -- "fix: ... Found
    by Pipi on the release gate ... Co-Authored-By" -- and the notice copied
    it word for word into every admin's inbox. Trailers go, a leading
    conventional-commit subject goes, and what is left must contain at least
    one line shaped like a release note or it is not shown at all: better a
    pointer to the Update card than a developer's paragraph.
    """
    lines = [ln.rstrip() for ln in str(text or "").replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if not _TRAILER_RE.match(ln)]
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and _SUBJECT_RE.match(lines[0].strip()):
        lines.pop(0)
    body = "\n".join(lines).strip()
    if not body or not any(_NOTE_LINE_RE.match(ln.strip()) for ln in lines if ln.strip()):
        return ""
    return body


def _notice_stands(running, config_dir=None):
    """Is the notice for this version still in the inbox?

    Answered by reading the inbox rather than by trusting the marker, for
    the reason in announce_installed. A failure to read is answered True:
    the marker is then the only witness there is, and posting a second
    notice on every start would be worse than missing one.
    """
    try:
        from core.services import inbox                    # noqa: PLC0415
        rows = inbox.for_user(inbox.ADMINS, True, config_dir)
    except Exception:                                      # noqa: BLE001
        return True
    return any(r.get("kind") == "update"
               and str(r.get("version") or "").lstrip("vV") == running
               for r in rows)


def _cached_notes(running):
    """The cleaned notes of the release the cache names, when that is the
    version now running; "" otherwise."""
    for ch in CHANNELS:
        cached = _read_json(_p(CHECK_FILE[ch]), {})
        if str(cached.get("latest") or "").strip().lstrip("vV") == running:
            return clean_notes(cached.get("notes"))
    return ""


def reconcile_cache(running=None):
    """Bring every channel's check cache up to the RUNNING version.

    Called at hub start (from announce_installed). A cache written by the
    previous version carries that version as `current` and an
    update_available computed against it; after the update it would go on
    saying "v1.0.12 is available" to a box running v1.0.12 until the next
    check. Rewritten here, keeping the release it knows about (and its
    ETag): update_available is now "is the latest release newer than what
    runs". Returns the channels it changed."""
    running = str(running or VERSION).strip().lstrip("vV")
    changed = []
    for ch in CHANNELS:
        path = _p(CHECK_FILE[ch])
        cached = _read_json(path, {})
        if not cached:
            continue
        latest = str(cached.get("latest") or "")
        want = bool(latest and is_newer(latest, running))
        if (str(cached.get("current") or "").lstrip("vV") != running
                or bool(cached.get("update_available")) != want):
            cached["current"] = running
            cached["update_available"] = want
            _write_json(path, cached)
            changed.append(ch)
    return changed


def announce_installed(config_dir=None, running=None):
    """On the first hub start after an update, tell the admins what changed.

    The Update card showed the release notes BEFORE the operator pressed
    Update; once the box came back on the new version there was nothing on
    it that said so, or what had changed -- the card now reads "up to date"
    and the notes are gone with it. So the first start of a new version
    leaves one notice in the bell's Inbox for master and the developer:
    "Updated to v1.0.7" with the same notes, grouped by area, that the card
    showed. Written to the inbox rather than logged because it is the
    operator's news, and the inbox is the one place a signed-in person is
    sure to see it.

    Once per version (ANNOUNCED_FILE under the update dir, on the store, so
    it survives the swap it describes) -- but the marker is not trusted on
    its own. The INBOX lives in config/ inside the release, and the applier
    carries the live release's config into the one going live, so a notice
    written between two applies is copied over and lost while the marker,
    on the store, survives: the box then believes it has announced a version
    whose notice no longer exists. Seen on .116 on 2026-09-25, where two
    apply runs ran 5 seconds apart. So the marker is only honoured when the
    notice it refers to is still THERE; otherwise the notice is posted
    again. Nothing is posted on a source checkout or a developer box that is
    not laid out for OTA, and nothing on a fresh install -- a box with no
    earlier release beside it did not UPDATE to this version, it started on
    it. Returns the notice, or None.

    The notes come from the check cache, which is what the card drew from:
    the release the box just installed is the one it last saw as "latest",
    so the cache still names it. If the cache is about some other version
    (checked again already, or never), the notice still says the version
    changed and points at the System tab.
    """
    from core.version import VERSION as current
    running = str(running or current).strip().lstrip("vV")
    # Whatever happens below, the check caches stop describing the version
    # this box ran before: their "update available" is recomputed against
    # the one it runs now (reconcile_cache).
    reconcile_cache(running)
    seen = _read_json(_p(ANNOUNCED_FILE), {})
    if str(seen.get("version") or "") == running and _notice_stands(running, config_dir):
        return None
    mark = {"version": running, "at": int(time.time())}
    if not ota_ready() or not previous_version():
        # Recorded, not announced: the next version IS an update, and it
        # must not be silenced by the memory that this one was not.
        _write_json(_p(ANNOUNCED_FILE), mark)
        return None
    notes = _cached_notes(running)
    if not notes and update_auto.enabled(config_dir):
        # The cache does not name this version (checked again since, or the
        # notes it held were not release notes). Ask once, per channel this
        # box may use -- only when the box may go online by itself; a box
        # without a token or a network answers with what it had, and the
        # notice still goes out with the pointer below.
        for ch in CHANNELS:
            try:
                check(force=True, config_dir=config_dir, channel=ch)
            except Exception:                              # noqa: BLE001
                continue
        notes = _cached_notes(running)
    from core.services import inbox                        # noqa: PLC0415
    body = notes or ("This box now runs the new version. What changed is "
                     "listed on the Update card in the System tab.")
    # No page link: the notice IS the news, and "Go to Settings -> System"
    # under it sent people to a card that by then only says "up to date".
    row = inbox.notify_admins("update", f"Updated to v{running}", body,
                              config_dir, limit=inbox.BODY_MAX_LONG,
                              version=running)
    _write_json(_p(ANNOUNCED_FILE), mark)
    return row


# ---------------------------------------------------------------------------
# Starting a job
# ---------------------------------------------------------------------------

def _job_config_dir(config_dir=None):
    """The config root as the APPLIER should see it: absolute, and expressed
    through the live symlink whenever it lives inside the release.

    Two separate failures fixed by one line.

    Absolute, because the config root is normally the relative "config" and
    the hub resolves it against its own working directory. The applier is a
    different process in a different unit with no working directory of its
    own, so it ran from / and looked for /config/update.local.toml -- every
    apply failing preflight with "no update token is configured on this box"
    on a box that had one, 0600, exactly where it belonged.

    Through the symlink, because config lives INSIDE each release and the job
    outlives the flip. Pinned to the outgoing release's real path, everything
    after the swap -- reviving the masters above all -- would read the
    configuration of the version just replaced. Through the link, the token is
    read from the old release before the flip and the systems are read from
    the new one after it, which is what each step actually means.
    """
    real = os.path.abspath(str(config.root(config_dir)))
    link = live_link()
    try:
        target = os.path.realpath(link)
        rel = os.path.relpath(real, target)
    except (OSError, ValueError):
        return real
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return real                      # outside the release; leave it alone
    return os.path.join(link, rel) if rel != os.curdir else link


def _write_job(mode, version, config_dir=None, channel=None):
    channel = require_channel(channel, config_dir)
    s = settings(config_dir, channel)
    job = {"mode": mode,
           "version": str(version or "").lstrip("vV"),
           "current": VERSION,
           "channel": channel,
           # Which key in update.local.toml the applier reads, and so which
           # token goes to which repo. Named here so the applier never has to
           # know what a channel is -- it reads the one scalar it was told to.
           "token_key": TOKEN_KEY[channel],
           "owner": s["owner"],
           "repo": s["repo"],
           "pubkey": s["pubkey"] or PUBKEY_B64,
           "config_dir": _job_config_dir(config_dir),
           # abspath for the same reason _job_config_dir exists: store_dir()
           # honours a relative CYTRON_STORE at its word, and a relative path
           # in job.json is resolved by the applier from / -- it would then
           # snapshot and sync the systems of a directory that does not exist,
           # silently. The hub's working directory is where the value meant.
           "store_dir": os.path.abspath(str(config.store_dir())),
           "releases_dir": releases_dir(),
           "live_link": live_link(),
           "manifest_app": s["manifest_app"],
           "bundle_asset": BUNDLE_ASSET,
           "manifest_asset": MANIFEST_ASSET,
           "sig_asset": SIG_ASSET,
           "assets": (_read_json(_p(CHECK_FILE[channel])).get("assets") or {}),
           # Where the hub will be AFTER the restart the update performs,
           # which is the gate's question. That is the CONFIGURED port --
           # not the one this process bound: after "Save, restart Later" the
           # two differ, the updater polled the old one, saw nothing and
           # rolled a good update back (Pipi, 2026-10-05). Only a unit that
           # bakes --port (source "argv") comes back on the running port.
           "hub_port": job_hub_port(config_dir)}
    _write_json(_p("job.json"), job)
    return job


def _stage_applier():
    """Copy the applier out of the code tree before it runs.

    It has to outlive the tree it is replacing, and it has to keep working
    when the symlink under it flips mid-run. Copying the RUNNING version's
    script also means each release ships the applier that installs the NEXT
    one -- an improvement to the update process reaches a box the same way
    every other change does.
    """
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "scripts", "apply_update.py")
    dest = _p("apply_update.py")
    with open(src, "rb") as f:
        data = f.read()
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dest)
    return dest


def _start_unit():
    """Hand the job to systemd. The one sudo call this feature makes, and the
    reason for the narrow sudoers rule installed with the units.

    --no-block is not an optimisation. `systemctl start` on a Type=oneshot
    unit waits for the whole job to finish, and this job takes minutes and
    ends by restarting the process making the call: without it the request
    hangs, times out, and tells the operator the update failed to start while
    it is in fact running.
    """
    try:
        proc = subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", "start", "--no-block",
             APPLY_UNIT],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"could not start the updater: {exc}")
    if proc.returncode != 0:
        raise UpdateError("could not start the updater - "
                          + (proc.stderr or "").strip()[:200])


#: What apply() says when the check it makes first gets no usable answer.
CONFIRM_FAILED = ("could not confirm that this is the newest version - {why}; "
                  "try again")


def _confirm_newest(version, config_dir, channel):
    """Ask GitHub NOW, and refuse unless `version` is still the newest
    release of the channel with all of its files up.

    The cache apply() reads is written by a timer that runs every six hours,
    and the Update card shows whatever it last wrote. Three releases went out
    within a day (v1.0.13 to v1.0.15, 2026-10-05); a box on v1.0.12 whose
    timer had run between the second and the third showed v1.0.14, and
    pressing Update installed exactly that -- the cache agreed with the card,
    and nothing asked whether the world had moved on. So the moment of
    installing is also a moment of checking: forced (no ETag, so a stale
    answer is impossible), queued behind any check already out, and the
    cache it leaves behind is the one the job file is written from. A newer
    release is reported as UpdateSuperseded with the version the card now
    shows; a release whose files are still uploading is waited for rather
    than skipped (see _check); and a check that got no answer refuses too:
    the download that follows would need the same connection.
    """
    fresh = check(force=True, config_dir=config_dir, channel=channel, wait=True,
                  counted=False)
    error = str(fresh.get("error") or "")
    if error == "token_expired":
        raise UpdateError(CONFIRM_FAILED.format(
            why="the update token has expired, paste a new one"))
    if _check_failed(error):
        raise UpdateError(CONFIRM_FAILED.format(why=error.lower().rstrip(".")))
    newest = str(fresh.get("latest") or "").strip().lstrip("vV")
    if newest and newest != version and is_newer(newest, version):
        raise UpdateSuperseded(newest, version)
    if newest != version:
        raise UpdateError("that version is no longer the latest release - "
                          "check for updates again")
    if fresh.get("publishing"):
        raise UpdateError(f"version {version} is still being published - its "
                          "files are not all up yet; try again in a few minutes")


def apply(version, config_dir=None, channel=None):
    """Begin applying `version`. Returns once the job has been handed off."""
    channel = require_channel(channel, config_dir)
    version = str(version or "").strip().lstrip("vV")
    if not version:
        raise UpdateError("no version was given")
    if not ota_ready():
        raise UpdateError("this box is not set up for over-the-air updates "
                          "(no release layout) - update it from the installer")
    if job_running():
        raise UpdateBusy("an update is already running")
    if not token(config_dir, channel):
        raise UpdateError(NO_TOKEN[channel])
    cached = _read_json(_p(CHECK_FILE[channel]))
    s = settings(config_dir, channel)
    if not assets_in_repo(cached.get("assets"), s["owner"], s["repo"]):
        raise UpdateError("that release was listed from a different release "
                          "repo - check for updates again")
    # The admin approved a version they were shown. If a newer release landed
    # in the seconds since, installing THAT one instead is not what was
    # approved -- re-check and let them look at it first.
    if str(cached.get("latest") or "").lstrip("vV") != version:
        raise UpdateError("that version is no longer the latest release - "
                          "check for updates again")
    if not is_newer(version, VERSION):
        raise UpdateError(f"version {version} is not newer than {VERSION}")
    _confirm_newest(version, config_dir, channel)
    _write_job("apply", version, config_dir, channel)
    _stage_applier()
    _write_json(_p("status.json"), {"phase": "starting", "percent": 0,
                                    "detail": "handing off to the updater",
                                    "version": version,
                                    "started": int(time.time()),
                                    "boot_id": _boot_id(), "pid": 0})
    _start_unit()
    return status()


def rollback(config_dir=None):
    """Put the previous release back. Exempt from the downgrade rule on
    purpose: it installs nothing, it points the box at code that was already
    verified and already ran here."""
    prev = previous_version()
    if not prev:
        raise UpdateError("there is no previous version on this box")
    if not ota_ready():
        raise UpdateError("this box is not set up for over-the-air updates")
    if job_running():
        raise UpdateBusy("an update is already running")
    _write_job("rollback", prev, config_dir)
    _stage_applier()
    _write_json(_p("status.json"), {"phase": "starting", "percent": 0,
                                    "detail": "rolling back",
                                    "version": prev,
                                    "started": int(time.time()),
                                    "boot_id": _boot_id(), "pid": 0})
    _start_unit()
    return status()
