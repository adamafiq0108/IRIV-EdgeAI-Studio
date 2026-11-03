# -*- coding: utf-8 -*-
"""
update_revive.py -- the hub brings back the systems an update took down.

Why the HUB does this, and not the applier that ran the update:

The applier (scripts/apply_update.py) runs as irivai-apply.service, a
Type=oneshot unit. Its revive_masters() spawns the replacement masters from
inside that unit, and spawn_master's start_new_session changes the SESSION,
not the CGROUP: every master it starts is still a member of the apply unit's
control group, and when the oneshot's main process exits systemd stops the
rest of the group (the default KillMode=control-group). verify_masters()
passed because it ran while the unit was still alive. Boards .21 and .22 lost
every user-created system this way on 2026-10-05, three updates in a row, the
master log's last write matching "Deactivated successfully" to the second
(docs/team/yaya/2026-10-05-boards-21-22-health.md). Only masters with a unit
of their own (irivai-master@demo) came back, restarted by systemd.

Nothing on the box fixed that afterwards: the hub autostarts only at its own
start, and the start that followed the flip saw the maintenance flag and
skipped. So this module is that afterwards. The hub that comes up after the
flip is the NEW release's hub, which is what makes the fix land on the very
update that installs it: the old applier still runs the update, but the new
hub is what finishes it. Everything it spawns is in irivai-hub.service's
cgroup (KillMode=process there, by design), which outlives any update.

Belt and braces beside this: install.sh and upgrade_steps.sh give the apply
unit KillMode=process, so on a box that has had either the applier's own
revive is real again and this pass finds everything answering and does
nothing. A box in the field cannot get a unit change without root, which is
why the hub side is the fix and the unit change only the extra.

What comes back: the masters the applier recorded as running before the
flip (update/was_masters.json, written by the 1.0.16+ applier), or, for the
one update run by an older applier that records nothing, the masters whose
log in the live tree was written since the job began (the applier's doomed
spawn wrote exactly those), plus every Autostart-flagged system, which is
what the skipped boot autostart would have started. Nothing is started that
already answers, nothing the operator powered off, nothing that systemd owns
(irivai-master@<slug>, which restarts itself), and the pass runs once per
job (update/revived.json) so a later hub restart does not restart a system
somebody stopped on purpose.
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.parse

from core.services import update as update_service

logger = logging.getLogger(__name__)

RECORD_FILE = "was_masters.json"        # written by the applier, read here
MARKER_FILE = "revived.json"            # written here, once per job
UNIT = "irivai-apply.service"
#: Terminal phases after which there is something to bring back. "refused"
#: and "idle" never got as far as stopping anything.
FINISHED = ("done", "rolled_back", "failed")
#: Spawns per system, and the wait after each pass before looking again.
ATTEMPTS = 3
BACKOFF_S = (10, 20, 40)
#: How long to wait for a running applier (its unit allows 3600 s).
WAIT_JOB_S = 3900
#: After the apply unit is inactive, systemd's SIGTERM to the doomed masters
#: needs a moment to free their ports, or the pass would see them answering.
GRACE_S = 3
#: Looked up at call time so a test can run the pass without the waits.
_SLEEP = time.sleep


def _read(name, default=None):
    try:
        with open(update_service._p(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default if default is not None else {}


def pending():
    """The finished job of THIS boot that nobody has revived for, or None."""
    raw = _read("status.json")
    if raw.get("phase") not in FINISHED:
        return None
    started = int(raw.get("started") or 0)
    if not started:
        return None
    mine = update_service._boot_id()
    if raw.get("boot_id") and mine and raw["boot_id"] != mine:
        return None                     # a reboot's own autostart handled it
    if int(_read(MARKER_FILE).get("started") or 0) == started:
        return None
    return {"started": started, "phase": raw["phase"],
            "version": str(raw.get("version") or "").lstrip("vV")}


def _unit_active():
    """Is the apply unit still up (its cgroup not yet swept)? False where
    there is no systemd to ask."""
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "is-active", UNIT],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.stdout.strip() in ("active", "activating", "deactivating",
                                "reloading")


def unit_managed():
    """Slugs with a systemd unit of their own (the installer's env files):
    systemd restarts those, and a second master from here would only fight
    it for the port."""
    try:
        return {n[:-4] for n in os.listdir("/etc/irivai") if n.endswith(".env")}
    except OSError:
        return set()


def _local_port(entry):
    from core.services import provision                    # noqa: PLC0415
    parsed = urllib.parse.urlparse((entry or {}).get("url") or "")
    if parsed.port and provision.is_local_master(parsed.hostname):
        return parsed.port
    return None


def wanted(masters, started, base):
    """{slug: port} to bring back: the applier's record for this job, else the
    masters whose log the doomed spawn wrote, plus the Autostart ones."""
    out = {}
    record = _read(RECORD_FILE)
    recorded = (record.get("masters") or {}) \
        if int(record.get("started") or 0) == started else None
    for slug, entry in (masters or {}).items():
        port = _local_port(entry)
        if not port:
            continue
        if (entry or {}).get("autostart"):
            out[slug] = port
        elif recorded is not None:
            if slug in recorded:
                out[slug] = port
        else:
            log = os.path.join(base, "logs", f"master-{slug}.log")
            try:
                if os.path.getmtime(log) >= started - 1:
                    out[slug] = port
            except OSError:
                pass
    return out


def revive(masters, systems_dir, base, config_dir, job, answers=None,
           bring_up=None, owned=None, sleep=None, notify=None):
    """One bounded pass over wanted(): spawn what does not answer, look
    again after a backoff, give up after ATTEMPTS and say so in the Inbox.
    Returns {revived, already, gave_up, left} (slug lists). The callables
    are parameters so a test can run it without a box."""
    from core.services import provision                    # noqa: PLC0415
    answers = answers or (lambda url: provision._master_answers(url))
    bring_up = bring_up or (lambda slug, entry: provision.autostart_system(
        systems_dir, slug, entry, base))
    owned = unit_managed() if owned is None else set(owned)
    sleep = sleep or _SLEEP
    want = wanted(masters, job["started"], base)
    up, left, tries = set(), {}, {s: 0 for s in want}
    for i in range(ATTEMPTS + 1):
        spawned = 0
        for slug, port in sorted(want.items()):
            if slug in up or slug in left:
                continue
            entry = masters[slug]
            if answers(entry.get("url") or f"http://127.0.0.1:{port}"):
                up.add(slug)
                continue
            if slug in owned:
                left[slug] = "its own systemd unit restarts it"
                continue
            if i == ATTEMPTS or tries[slug] >= ATTEMPTS:
                continue
            tries[slug] += 1
            result = bring_up(slug, entry) or {}
            msg = str(result.get("message") or "")
            if result.get("master") == "skipped" and "powered off" in msg:
                left[slug] = "powered off by the operator"
                continue
            spawned += 1
            logger.info("[HUB] after the update: %s (try %d): %s",
                        slug, tries[slug], msg or result)
        if not spawned:
            break
        sleep(BACKOFF_S[min(i, len(BACKOFF_S) - 1)])
    gave_up = sorted(s for s in want if s not in up and s not in left)
    revived = sorted(s for s in up if tries.get(s))
    if gave_up:
        _notice(gave_up, want, job, config_dir, notify)
    try:
        with open(update_service._p(MARKER_FILE), "w", encoding="utf-8") as f:
            json.dump({"started": job["started"], "at": int(time.time()),
                       "revived": revived, "gave_up": gave_up,
                       "left": left}, f)
    except OSError:
        pass
    return {"revived": revived, "already": sorted(up - set(revived)),
            "gave_up": gave_up, "left": left}


def _notice(gave_up, want, job, config_dir, notify):
    from core.services import inbox, provision             # noqa: PLC0415
    names = provision.system_names(config_dir)
    shown = ", ".join(f"{names.get(s, s)} (port {want[s]})" for s in gave_up)
    body = (f"After the update to v{job['version']} the hub tried {ATTEMPTS} "
            f"times to start {shown} and got no answer. Open the system's "
            "card on the Hub page and press Start; if it still does not "
            "answer, the log logs/master-<system>.log says why.")
    title = ("1 system did not come back after the update"
             if len(gave_up) == 1 else
             f"{len(gave_up)} systems did not come back after the update")
    logger.error("[HUB] %s: %s", title, shown)
    (notify or inbox.notify_admins)("update", title, body, config_dir,
                                    page="systems")


def _wait_for_applier(sleep=None):
    """Block until no applier is alive, the maintenance flag is down and
    the apply unit is inactive, so the masters it started are already gone
    and the ports are free. Bounded: a wedged job must not wedge the hub."""
    sleep = sleep or _SLEEP
    deadline = time.time() + WAIT_JOB_S
    while update_service.job_running() and time.time() < deadline:
        sleep(2)
    # The applier drops the flag before it exits; a flag with no applier
    # behind it is stale (a crash; recover.py clears it at the next boot).
    for _ in range(30):
        if not update_service.maintenance():
            break
        sleep(2)
    for _ in range(60):
        if not _unit_active():
            break
        sleep(2)
    sleep(GRACE_S)


def start(masters, systems_dir, base, config_dir=None):
    """Called at hub start. Returns the thread when an update is under way
    or just finished in this boot, None when there is nothing to do."""
    raw = _read("status.json")
    if not raw:
        return None
    if not (update_service.job_running() or update_service.maintenance()
            or pending()):
        return None

    def _run():
        try:
            _wait_for_applier()
            job = pending()
            if job is None:
                if update_service.status().get("phase") not in FINISHED:
                    logger.warning("[HUB] the update did not finish; its "
                                   "systems stay down until the box is "
                                   "repaired at the next start")
                return
            report = revive(masters, systems_dir, base, config_dir, job)
            logger.info("[HUB] after the update: revived %s; already up %s; "
                        "left alone %s; gave up on %s",
                        report["revived"] or "none",
                        report["already"] or "none",
                        report["left"] or "none",
                        report["gave_up"] or "none")
        except Exception:                                   # noqa: BLE001
            logger.exception("[HUB] bringing systems back after the update failed")

    t = threading.Thread(target=_run, name="update-revive", daemon=True)
    t.start()
    return t
