# -*- coding: utf-8 -*-
"""
snapcam.py -- what a Snapshot camera does between snaps.

A Snapshot Detection Stream looks at nothing between triggers, but a camera
left running still decodes every frame, and for a high-resolution or USB camera
that decode is most of the CPU the mode was meant to save. Two ways to stop it:

  close  release the device after a short idle spell and open it again when a
         snap starts (lowest CPU; the snap waits for the open)
  keep   leave it open but read one frame a second (an RTSP stream still has to
         be decoded to stay in step, so this saves less, but it avoids the slow
         RTSP reconnect)

`auto` picks keep for a network stream and close for everything else; the
Detection Stream node can force either. A file or the synthetic camera is left
alone. After a wake the first frames are discarded (auto exposure, and the
slow-rate frame is stale) so the snap is the moment of the press.

tick() is called once per loop pass for every camera; it holds the decisions
and starts the open/close on a thread, so the loop (and every other camera) is
never blocked. No imports beyond the standard library.
"""

import threading
import time

#: Seconds a Snapshot camera must be idle before it is put to sleep. Long enough
#: that a second press right after the first does not pay for a reopen.
IDLE_GRACE_S = 3.0
#: Frames thrown away after a wake, and the longest that wait may take.
WARM_FRAMES = 3
WARM_MAX_S = 2.5
#: A /raw viewer seen this recently means another system is drinking this
#: camera: it must stay awake. Filled by run.py's _raw_jpeg.
RAW_SEEN = {}
RAW_FRESH_S = 10.0

_NETWORK = ("rtsp://", "rtsps://", "http://", "https://", "rtmp://", "udp://", "tcp://")


def mode_of(pipe):
    """'close', 'keep' or 'off' for this camera's idle spell."""
    cam = pipe.cam
    if getattr(cam, "synthetic", False) or getattr(cam, "file_path", None) is not None \
            or getattr(cam, "held_still", False):
        return "off"
    cfg = (getattr(pipe, "cfg", None) or {}).get("snapshot") or {}
    if cfg.get("idle") in ("close", "keep"):
        return cfg["idle"]
    src = str(getattr(cam, "source", "")).strip().lower()
    return "keep" if src.startswith(_NETWORK) else "close"


def _run(gate, fn):
    def _go():
        try:
            fn()
        except Exception:                                   # noqa: BLE001
            pass
        finally:
            gate.cam_state["busy"] = False
    gate.cam_state["busy"] = True
    threading.Thread(target=_go, name="snapcam", daemon=True).start()


def tick(pipe, now=None):
    """Decide this pass: wake, sleep or slow the camera, and set gate.hold."""
    now = time.time() if now is None else now
    gate = getattr(pipe, "snap", None)
    cam = pipe.cam
    if getattr(pipe, "parked", False) or getattr(pipe, "opening", False):
        if gate is not None:
            gate.hold = False
        return
    if gate is None:
        # Not a Snapshot camera (any more): make sure it is running normally.
        if getattr(cam, "low_rate", False):
            cam.low_rate = False
        if getattr(cam, "asleep", False):
            cam.asleep = False
            pipe.open_async()
        return
    st = gate.cam_state
    mode = mode_of(pipe)
    if mode == "off" or st.get("busy"):
        gate.hold = bool(st.get("busy")) and gate.left > 0
        return
    shared = now - RAW_SEEN.get(pipe.id, 0.0) < RAW_FRESH_S
    needed = gate.left > 0 or shared
    asleep = getattr(cam, "asleep", False)
    seq = cam.latest()[1] if hasattr(cam, "latest") else 0
    if needed:
        st["idle_since"] = None
        if asleep:
            st["warm"] = None
            _run(gate, cam.wake)
            gate.hold = gate.left > 0
            return
        if getattr(cam, "low_rate", False):
            cam.low_rate = False
            st["warm"] = None
        if gate.left > 0:
            if st.get("warm") is None:
                st["warm"] = (seq + WARM_FRAMES, now + WARM_MAX_S)
            target, deadline = st["warm"]
            gate.hold = seq < target and now < deadline
        else:
            gate.hold = False
        return
    # Nothing owed.
    gate.hold = False
    st["warm"] = None
    if asleep:
        return
    # Wait until a frame has been seen at all (the waiting picture is sized
    # from it), but not for ever.
    if st.get("idle_since") is None:
        st["idle_since"] = now
    waited = now - st["idle_since"]
    if waited < IDLE_GRACE_S or (seq == 0 and waited < 10.0):
        return
    if mode == "close":
        _run(gate, cam.sleep)
    elif not getattr(cam, "low_rate", False):
        cam.low_rate = True
