# -*- coding: utf-8 -*-
"""
power_state.py -- "detection is still stopping", and the refusal it earns.

Stop flips the AI switch and answers at once; the app then releases the
camera and the accelerator on its next config poll, which takes seconds (a
Hailo VDevice closes slowly, an RTSP reader has to be torn down). In that
window the status says running=false (the switch's word) while the app's
own state still says ai_on=true -- and a Start pressed then, from this tab,
another tab or the dashboard, raced the teardown: the switch went back on
before the chip was free, and the app opened a camera it was closing.

Its own small module, with no imports from the rest of core, so the error
can be mapped to a 409 by the app-wide handlers (core/routes/logs.py)
without those having to import the detector service.
"""


class StoppingError(RuntimeError):
    """A Start asked while the previous run is still winding down. Mapped to
    HTTP 409 app-wide; the words are the ones the toast shows."""


MESSAGE = ("Detection is still stopping - the camera and the AI chip are "
           "being released. Wait a moment, then press Start again.")


def ai_stopping(st, app_alive, run_alive, fresh_age_s, fresh_s):
    """True while the switch says OFF but the app still reports its AI ON.

    Only a unified app (the switch lives inside it) has such a window; an
    older app is stopped as a process and the stop call waits for the pid.
    Only on a FRESH heartbeat: an app that stopped writing is not "still
    stopping", it is stuck, and holding Start for ever on a stale report
    would leave the operator with no button at all."""
    st = st or {}
    return bool(app_alive and not run_alive
                and st.get("mode") == "unified"
                and st.get("ai_on") is True
                and fresh_age_s < fresh_s)
