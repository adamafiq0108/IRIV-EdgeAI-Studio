# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
camera_notice.py -- the camera watchdog's Inbox notices.

An app whose camera goes silent restarts its own AI detection
(systems/demo/app/camwatch.py) and reports the episode in its state file
(`cam_watch`). The app has no inbox of its own, so the master's app watcher
(app_supervisor.watch_step, every WATCH_S) passes it on here: one notice to
the admins when the first automatic restart did not bring the camera back,
one when it is back -- never one per restart. The episode id is remembered in
the supervisor's local state, so a master restart does not repeat a notice.
Its own module because app_supervisor.py sits at the services line ceiling.

A camera NOT FOUND stops detection instead (the `pause` beside the episode):
one notice when it stops, one when the camera is back and detection started
again, none while it waits. A pause the user ended with Stop before the
master looked says nothing -- the user was there.
"""

from core import config as config_mod

#: Shared with app_supervisor: one local state file for the watcher.
STATE_NAME = "app_supervisor"


def post(systems_dir, slug, config_dir=None, notify=None, raw=None):
    """Post what the episode in `raw` (default: the app's state file) has
    earned and not yet had. Returns the notices posted, in order: "missing"
    and "resumed" for a stop for a missing camera, "lost" and "back" for a
    restart episode (both of a pair at once when one 30 s look saw a quick
    recovery)."""
    from core.services import detector_control as dc           # noqa: PLC0415
    if notify is None:
        from core.services import inbox                        # noqa: PLC0415
        notify = inbox.notify_admins
    raw = dc.app_state(systems_dir, slug) if raw is None else raw
    ep = (raw or {}).get("cam_watch")
    if not isinstance(ep, dict):
        return []
    hold = ep.get("pause") if isinstance(ep.get("pause"), dict) else {}
    if not ep.get("episode") and not hold.get("id"):
        return []
    key = str(ep.get("episode"))
    try:
        state = dict(config_mod.load_local(STATE_NAME, config_dir))
    except ValueError:
        state = {}
    seen = dict(state.get("camera") or {})
    mine = dict(seen.get(slug) or {})
    # The app's state names cameras by ID (what zones and clips key on); a
    # person is told the camera's NAME, the one the page shows.
    words = names_of(systems_dir, slug)
    said = lambda ids: ", ".join(words.get(str(c), str(c))           # noqa: E731
                                 for c in (ids or ())) or "camera"
    cams = said(ep.get("cameras"))
    name = slug
    try:
        name = str(((dc.system_config(systems_dir, slug) or {}).get("app")
                    or {}).get("display_name") or slug)
    except Exception:                                          # noqa: BLE001
        pass
    posted = []
    pkey = str(hold.get("id") or "")
    if (pkey and hold.get("state") in ("paused", "resumed")
            and mine.get("missing") != pkey):
        gone = said(hold.get("cameras"))
        notify("camera_watch", f"Camera {gone} not found",
               f"Camera {gone} on {name} was not found, so detection "
               "stopped; it starts again when the camera is back.",
               config_dir, page="systems")
        mine["missing"] = pkey
        posted.append("missing")
    if (pkey and hold.get("state") == "resumed"
            and mine.get("missing") == pkey and mine.get("resumed") != pkey):
        back = said(hold.get("back"))
        notify("camera_watch", f"Camera {back} is back",
               f"Camera {back} on {name} is back; detection started again.",
               config_dir, page="systems")
        mine["resumed"] = pkey
        posted.append("resumed")
    # A restart episode that ended in a stop for a missing camera has had
    # its say in the "not found" notice above.
    if (ep.get("episode") and ep.get("failed") and ep.get("state") != "paused"
            and mine.get("lost") != key):
        notify("camera_watch", f"Camera {cams} is not answering",
               f"Camera {cams} on {name} is not answering; detection is "
               "restarting itself every minute until it comes back.",
               config_dir, page="systems")
        mine["lost"] = key
        posted.append("lost")
    if (ep.get("state") == "recovered" and mine.get("lost") == key
            and mine.get("back") != key):
        notify("camera_watch", f"Camera {cams} is back",
               f"Camera {cams} on {name} is answering again; detection is "
               "running.", config_dir, page="systems")
        mine["back"] = key
        posted.append("back")
    if posted:
        seen[slug] = mine
        state["camera"] = seen
        config_mod.save_local(STATE_NAME, state, config_dir)
    return posted


def display_name(cam):
    """What a person calls a camera entry: its name, else its label, else
    its (password-masked) source, else the id. Same rule the page's
    camName() draws the rail with, so a toast and the card agree."""
    from core.services import detector_control as dc           # noqa: PLC0415
    c = cam or {}
    word = str(c.get("name") or c.get("label") or "").strip()
    if word:
        return word
    src = dc.mask_source(str(c.get("source") or "")).strip()
    return src or str(c.get("id") or "camera")


def names_of(systems_dir, slug):
    """{camera id: display_name} for the system, {} when it cannot be read."""
    from core.services import detector_control as dc           # noqa: PLC0415
    try:
        return {str(c.get("id")): display_name(c)
                for c in dc.cameras(systems_dir, slug)}
    except Exception:                                          # noqa: BLE001
        return {}


def _network(cam):
    """A camera reached over the network, as opposed to one plugged in."""
    return "://" in str((cam or {}).get("source") or "")


def absence(cam):
    """The word for a camera that is not there: a network camera is
    "not reachable" (its host did not answer), anything plugged in is
    "not found"."""
    return "not reachable" if _network(cam) else "not found"


def presence(pkg_dir, cameras):
    """{camera id: True / False / None} for `cameras` ({id, source} dicts)
    -- is each one on the box right now? Asked of the system's own
    app/camwatch.py (the probe the app runs, so the page and the app cannot
    disagree), every camera side by side, PROBE_TIMEOUT_S each. None: the
    source cannot be told (a file, a pipeline), or the app copy predates the
    probe. The USB probe opens and closes the node at once; it never holds
    the device from the detector."""
    from core.services import detector_control as dc           # noqa: PLC0415
    ids = [str(c["id"]) for c in cameras]
    try:
        cw = dc._load_app_module(pkg_dir, "camwatch")
        probe = getattr(cw, "probe_all", None)
        if probe is None:
            return {cid: None for cid in ids}
        got = probe({c["id"]: c.get("source") for c in cameras})
    except Exception:                                          # noqa: BLE001
        return {cid: None for cid in ids}
    return {cid: got.get(cid) for cid in ids}


def missing(pkg_dir, cameras):
    """The entries of `cameras` that are NOT on the box, when every one of
    them is absent; [] when any is there, cannot be told, or there are
    none. What a Start refuses on (detector_control.start), the same rule
    the app pauses on (camwatch: a pause needs every camera absent)."""
    if not cameras:
        return []
    got = presence(pkg_dir, cameras)
    if got and all(v is False for v in got.values()):
        return list(cameras)
    return []


def refusal(cameras):
    """Why a Start was refused, with the cameras by the names the page
    shows and the one thing to check."""
    gone = list(cameras)
    one = len(gone) == 1
    names = ", ".join(display_name(c) for c in gone)
    kinds = {absence(c) for c in gone}
    word = kinds.pop() if len(kinds) == 1 else "not found"
    fix = ("Check the camera's address and that it is on the network"
           if word == "not reachable"
           else "Check it is plugged in and switched on")
    return (f"{names} {'is' if one else 'are'} {word} - detection was not "
            f"started. {fix}, then press Start again.")


def waiting(pkg_dir, cameras):
    """What a Start says when NOT ONE of `cameras` ({id, source} dicts) is
    on the box, or "" -- asked of the system's own app/camwatch.py, the very
    probe the app runs, so the page and the app cannot disagree. An app
    copy from before the probe answers "" (it starts as it always did).
    Used by the Starts that may NOT refuse (a boot autostart: the camera
    often comes up after the box); a person's Start is refused instead
    (detector_control.start)."""
    gone = missing(pkg_dir, cameras)
    if not gone:
        return ""
    names = ", ".join(display_name(c) for c in gone)
    return (f"Detection is waiting for the camera: {names} "
            f"{'is' if len(gone) == 1 else 'are'} {absence(gone[0])}. It "
            "starts by itself when the camera is back.")


def start_word(systems_dir, slug, result):
    """A successful Start's answer, told when the system has no camera on
    the box: {"waiting": True} and the plain words above. The app itself
    stops detection after one restart (or at once, on a Start after the
    user stopped a wait) and starts it when the camera is back."""
    from core.services import detector_control as dc           # noqa: PLC0415
    try:
        word = waiting(dc._pkg_dir(systems_dir, slug),
                       dc.live_cameras(systems_dir, slug))
    except Exception:                                          # noqa: BLE001
        word = ""
    return dict(result, waiting=True, message=word) if word else result
