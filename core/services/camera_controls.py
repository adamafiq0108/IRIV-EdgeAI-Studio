# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""What a camera lets you adjust, read from the camera itself.

Every camera is different. A cheap UVC module offers brightness and nothing
else; an Arducam offers eleven controls, three of which are locked while auto
exposure owns them; an IP camera offers none at all over RTSP, because the
adjusting happens in its own web page. So this asks the DEVICE what it has
rather than drawing a fixed panel of sliders and hoping -- a slider that moves
and changes nothing is worse than no slider.

The answers come from `v4l2-ctl --list-ctrls-menus`, which is the driver's own
account of itself: name, type, range, step, default, current value, and
whether the control is active right now. Nothing is inferred from the camera's
model name.

THREE THINGS THAT MADE THIS MORE THAN A PARSER:

`flags=inactive` means the control exists but something else is driving it --
exposure_time_absolute while auto_exposure is in Auto Mode. The operator has
to be told which switch to turn off, not handed a dead slider.

The DETECTOR usually holds the camera. V4L2 lets a second process set controls
on a streaming device, so most changes land instantly; but a camera opened
through a vendor SDK (the Orbbec) is held exclusively and cannot be touched
from here at all. Both cases end the same way -- the value is SAVED to the
camera's config -- so the record of what an operator chose does not depend on
whether the device happened to be reachable at that moment.

Saved, and therefore survives a restart. A control set live and not saved is
back to the factory default the next time the app opens the camera, which is
the kind of thing that gets discovered at 3am on a night shift.
"""
import re
import subprocess

#: The most controls one request may set. Each one is a v4l2-ctl PROCESS, run
#: serially with a six-second timeout, so a body naming twenty thousand names
#: would hold a threadpool worker for a day and a half spawning them -- and
#: forty such requests would take every worker the master has. No camera
#: reports more than about a dozen usable controls, so this is generous and
#: still bounded. Also bounds what accumulates in config (see
#: detector_control.MAX_SAVED_CONTROLS).
MAX_CONTROLS_PER_REQUEST = 16

#: A control name is a driver symbol. Lower-case ASCII only, and the SAME
#: pattern the stored path uses -- `\w` is Unicode-aware, so it accepts
#: fullwidth and accented forms the config validator rejects, which left the
#: looser of the two filters on the path that reaches a command line.
NAME_RE = re.compile(r"[a-z0-9_]{1,64}")

#: Controls nobody should be handed on this panel. Not because they are
#: dangerous -- because they are not what "camera settings" means to an
#: operator, and a panel of forty rows is a panel nobody reads.
_SKIP = ("privacy", "pan_", "tilt_", "zoom_", "focus_", "iris_",
         "led1_", "region_of_interest")

#: How the driver's own names read on a panel. Anything not here is turned
#: from its_snake_case into Sentence case, which is right often enough that
#: naming every control by hand would be its own maintenance problem.
_LABELS = {
    "brightness": "Brightness",
    "contrast": "Contrast",
    "saturation": "Saturation",
    "hue": "Hue",
    "gamma": "Gamma",
    "gain": "Gain",
    "sharpness": "Sharpness",
    "backlight_compensation": "Backlight compensation",
    "white_balance_automatic": "Auto white balance",
    "white_balance_temperature": "White balance",
    "power_line_frequency": "Flicker (mains frequency)",
    "auto_exposure": "Auto exposure",
    "exposure_time_absolute": "Exposure time",
    "exposure_dynamic_framerate": "Let exposure slow the frame rate",
}

#: A hint under the rows that earn one. Kept short: the panel explains what
#: the control DOES, the camera says what values it takes.
_HINTS = {
    "gain": "Brightens a dark picture electronically. Cheaper than a longer "
            "exposure, and noisier -- raise exposure first.",
    "exposure_time_absolute":
        "How long each frame is collected for. Longer is brighter but blurs "
        "anything moving, and caps the frame rate.",
    "power_line_frequency":
        "Set this to your mains frequency or lights flicker in the picture. "
        "50 Hz in Malaysia and most of the world, 60 Hz in the Americas.",
    "white_balance_temperature":
        "What the camera treats as white. Only adjustable with auto white "
        "balance off.",
}


#: Which switch owns a control while it is locked. "inactive" tells the panel
#: THAT a row is not the operator's right now; this tells them what to turn
#: off, which is the difference between a disabled slider and an instruction.
_LOCKED_BY = {
    "white_balance_temperature": "white_balance_automatic",
    "exposure_time_absolute": "auto_exposure",
    "exposure_absolute": "exposure_auto",
    "gain": "gain_automatic",
}


def _run(argv, timeout=6):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def label_for(name):
    return _LABELS.get(name) or name.replace("_", " ").capitalize()


def _parse_controls(text):
    """`v4l2-ctl --list-ctrls-menus` into structured controls.

    The format is one control per line under a section heading, with menu
    entries indented beneath the menu control they belong to:

        brightness 0x00980900 (int)  : min=-64 max=64 step=1 default=0 value=0
        power_line_frequency 0x00980918 (menu) : min=0 max=2 default=1 value=1
                        0: Disabled
                        1: 50 Hz
    """
    line_re = re.compile(
        r"^\s*(\w+)\s+0x[0-9a-f]+\s+\((\w+)\)\s*:\s*(.*)$")
    menu_re = re.compile(r"^\s+(\d+):\s*(.+?)\s*$")
    out, group, current = [], "", None
    for line in text.splitlines():
        if not line.strip():
            continue
        heading = line.strip()
        if heading.endswith("Controls") and ":" not in heading:
            group = heading.replace(" Controls", "").strip()
            continue
        match = line_re.match(line)
        if match:
            name, kind, rest = match.group(1), match.group(2), match.group(3)
            fields = dict(re.findall(r"(\w+)=(-?\d+)", rest))
            current = {
                "name": name,
                "label": label_for(name),
                "group": group or "Image",
                "type": {"int": "int", "int64": "int", "bool": "bool",
                         "menu": "menu", "intmenu": "menu"}.get(kind, kind),
                "min": int(fields["min"]) if "min" in fields else None,
                "max": int(fields["max"]) if "max" in fields else None,
                "step": int(fields.get("step") or 1),
                "default": int(fields["default"]) if "default" in fields else None,
                "value": int(fields["value"]) if "value" in fields else None,
                # The driver says a control is there but not YOURS right now:
                # something else is driving it. The panel disables the row and
                # says which switch to turn off.
                "locked": "inactive" in rest,
                "read_only": "read-only" in rest,
                "hint": _HINTS.get(name, ""),
                "options": [],
            }
            if any(name.startswith(p) or name == p.rstrip("_") for p in _SKIP):
                current = None
                continue
            out.append(current)
            continue
        entry = menu_re.match(line)
        if entry and current is not None and current["type"] == "menu":
            current["options"].append({"value": int(entry.group(1)),
                                       "label": entry.group(2)})
    return out


def _device_for(source, kind=""):
    """The /dev node to ask about, or "" for a camera that has no node here.

    A slot number resolves through the udev symlink, the same way the capture
    path resolves it -- asking a different node than the one being opened is
    how a panel ends up adjusting a camera nobody is looking at.
    """
    import os
    text = str(source or "").strip()
    if kind == "depth" or text.lower().startswith("orbbec"):
        # The depth camera's own colour node, found by asking rather than by
        # number: the Orbbec's nodes move between enumerations, and after the
        # SDK has held it the by-id links point at nodes that enumerate
        # nothing. Empty when it is not in UVC mode at all.
        import glob
        for node in sorted(glob.glob("/dev/video*")):
            if "Z16" in _run(["v4l2-ctl", "-d", node, "--list-formats"]):
                return node
        return ""
    if not text.isdigit():
        return ""
    link = "/dev/cam-usb" + text
    if os.path.islink(link):
        real = os.path.realpath(link)
        if os.path.exists(real):
            return real
    node = "/dev/video" + text
    return node if os.path.exists(node) else ""


#: Why a camera has no adjustable settings here, said in the terms the
#: operator chose it in. Each of these is a real answer, not a failure.
_NO_CONTROLS = {
    "rtsp": "An IP camera is adjusted in its own web page, not here - open "
            "the camera's address in a browser. What this box can change is "
            "how it READS the stream: size and frame rate, above.",
    "gige": "A GigE camera's exposure and gain are set above, on the camera "
            "panel itself - they are part of how the stream is opened rather "
            "than settings held by a driver.",
    "file": "A recorded file has no camera to adjust. Brightness and exposure "
            "were decided when the footage was taken.",
    "unset": "No camera has been chosen yet.",
}


def controls(source, kind="", saved=None):
    """Every adjustable setting this camera reports, with its current value.

    Returns {"adjustable", "controls", "device", "note", "saved"}. `adjustable`
    False is a real answer with a reason in `note` -- an IP camera is not
    broken for having none.

    `saved` (the camera entry's stored controls) is folded in so the panel can
    show what an operator chose even when the device cannot be read right now,
    which is exactly the case when the detector holds it through an SDK.
    """
    saved = dict(saved or {})
    kind = (kind or "").strip().lower()
    if kind in _NO_CONTROLS:
        return {"adjustable": False, "controls": [], "device": "",
                "note": _NO_CONTROLS[kind], "saved": saved}
    device = _device_for(source, kind)
    if not device:
        note = ("this camera is not readable right now - it is open through "
                "its own SDK, which holds the device exclusively. Settings "
                "saved here are applied when the detector next opens it."
                if kind == "depth" else
                "could not find this camera's device node - it may be "
                "unplugged, or in use by another system")
        return {"adjustable": bool(saved), "controls": [], "device": "",
                "note": note, "saved": saved}
    found = _parse_controls(_run(["v4l2-ctl", "-d", device, "--list-ctrls-menus"]))
    if not found:
        return {"adjustable": False, "controls": [], "device": device,
                "note": ("this camera reports no adjustable controls - some "
                         "modules expose none at all, and there is nothing "
                         "wrong with that"),
                "saved": saved}
    present = {c["name"] for c in found}
    for control in found:
        # What was SAVED wins on the panel, because it is what this system
        # asked for. They differ whenever the device was replugged (back to
        # defaults) or another system changed it, and showing the live value
        # would quietly rewrite the operator's choice into whatever happened
        # to it since.
        if control["name"] in saved:
            control["chosen"] = saved[control["name"]]
        # Name the switch holding this row, but only if the camera really has
        # it -- a row that says "turn off auto exposure" on a camera with no
        # such control sends the operator looking for something that is not
        # there.
        owner = _LOCKED_BY.get(control["name"])
        if control["locked"] and owner in present:
            control["locked_by"] = owner
            control["locked_by_label"] = label_for(owner)
    return {"adjustable": True, "controls": found, "device": device,
            "note": "", "saved": saved}


def apply_live(source, kind, values):
    """Push values at the device NOW, so the operator sees the change.

    Best effort on purpose: a camera held through an SDK cannot be touched
    from here, and that is not an error -- the values are saved either way and
    the caller says so. Returns (applied, problem).
    """
    # The size check comes FIRST, before the device is even resolved: an
    # oversized body is refused whatever camera it names, and resolving a
    # device means globbing /dev and running v4l2-ctl over it.
    values = values or {}
    if len(values) > MAX_CONTROLS_PER_REQUEST:
        return {}, ("too many settings in one go - at most {} at a time"
                    .format(MAX_CONTROLS_PER_REQUEST))
    device = _device_for(source, kind)
    if not device:
        return {}, ("the camera is not reachable from here - the setting is "
                    "saved and takes effect when the detector reopens it")
    applied, failed = {}, []
    for name, value in values.items():
        # The value is turned into a number HERE, before it reaches a command
        # line. "abc" is a ValueError, {} a TypeError, and 1e999 parses out of
        # JSON as float inf, which is an OverflowError -- all three used to
        # climb out of this function as a 500 on a request that is simply
        # malformed.
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            failed.append(str(name)[:40])
            continue
        if not NAME_RE.fullmatch(str(name)):
            failed.append(str(name)[:40])
            continue
        out = _run(["v4l2-ctl", "-d", device,
                    "-c", "{}={}".format(name, number)])
        # v4l2-ctl says nothing on success and complains on failure.
        if out.strip():
            failed.append(name)
        else:
            applied[name] = number
    problem = ""
    if failed:
        problem = ("the camera refused " + ", ".join(sorted(failed))
                   + " - it may be locked by an auto mode, or not settable "
                     "while the detector is streaming")
    return applied, problem
