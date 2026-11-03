"""Vendor SDKs a camera needs, and installing one when it is missing.

Some cameras are not UVC devices. An Orbbec Gemini opens through its own SDK,
and without the wheel the camera enumerates on USB, appears in `lsusb`, and
cannot be opened at all -- which reads as a broken camera rather than a
missing package. So the panel says which SDK is wanted, whether it is here,
and offers to fetch it.

WHY THIS IS AN ALLOWLIST AND NOT A SEARCH.

"Find the SDK online and install it" is, written literally, arbitrary code
execution on an appliance triggered from a web page. A box that installs
whatever a search turns up is one poisoned result away from running somebody
else's code as the user that owns the detector.

So the box ships knowing exactly which packages exist, at which version, and
will install those and nothing else:

  * the name is chosen from KNOWN below -- never taken from the request
  * the version is PINNED here, so the same box gets the same code today and
    in a year, and an upstream account takeover does not silently reach it
  * it installs from PyPI over TLS with pip's own verification, from an index
    stated here rather than inherited from the environment (--isolated)
  * wheels only (--only-binary), so no package's setup.py ever runs as the
    user that owns the detector
  * --no-deps, because a vendor wheel that drags its own OpenCV in swaps the
    detector's out from under it
  * into the SYSTEM's own venv, never system-wide
  * one at a time, box-wide
  * master or developer only, and audited, because it changes what runs

STILL OUTSTANDING, and the honest gap: the wheel's HASH is not pinned. Every
other way code reaches this box is digest-verified -- store bundles are
ed25519-signed with a sha256 per file, OTA releases likewise -- and this is
the exception. Version pinning is not the same thing: PyPI forbids reusing a
filename but allows adding new files to an existing release, so an upstream
account takeover could publish a fresh wheel under this exact version for a
platform tag not yet covered. Closing it means --require-hashes with a
requirements file listing all 24 wheel hashes for the pinned version, since
pip does not honour --hash on a command line. Before beta.

There is deliberately no field for a URL, a package name or a version. Adding
a camera means adding an entry here, in a commit somebody reviewed.

An offline box cannot do this and says so plainly rather than hanging: these
appliances are often on closed networks, and "no internet" is a normal answer,
not a fault.
"""
import os
import subprocess
import sys
import threading
import time

#: Every SDK this box will install, and the exact version it installs.
#:
#: `module` is what proves it is there -- the import name, which is not the
#: package name for pyorbbecsdk2 and would be a silent "already installed"
#: forever if it were assumed.
KNOWN = {
    "depth": {
        "package": "pyorbbecsdk2",
        "version": "2.1.2",
        "module": "pyorbbecsdk",
        "label": "Orbbec depth camera SDK",
        "why": "Orbbec cameras open through their own SDK rather than as a "
               "plain USB camera. Without it the camera is detected but "
               "cannot be started.",
        # The wheel depends on opencv-python 5.x while the detector runs 4.10.
        # Letting pip resolve that replaces OpenCV under a running system.
        "no_deps": True,
    },
}

#: One install at a time, box-wide. Two pip runs into one environment have no
#: locking inside pip and can leave a package half-unpacked -- and each one
#: holds a worker from the event loop's DEFAULT executor, which is shared with
#: every other threaded call in the app (password reset among them). Eight
#: stuck ten-minute installs is a master that answers nothing. A second caller
#: is told an install is running rather than queued behind it.
_INSTALL_LOCK = threading.Lock()

#: How long an install may take before it is called off. A wheel is ~32MB, so
#: this is generous for a slow link and still bounded -- a pip that hangs on a
#: half-open connection must not hold the request for ever.
INSTALL_TIMEOUT_S = 600


def wanted_for(kind):
    """The SDK this kind of camera needs, or None."""
    return KNOWN.get((kind or "").strip().lower())


def _python_for(venv_dir=""):
    """The interpreter to install into: the system's own venv, else ours.

    Never a bare "pip": the detector runs from a venv, and a package installed
    beside the wrong interpreter is installed where nothing will import it.
    """
    if venv_dir:
        for rel in ("bin/python", "Scripts/python.exe"):
            candidate = os.path.join(venv_dir, rel)
            if os.path.exists(candidate):
                return candidate
    return sys.executable


#: Is the SDK there? Cached briefly -- see status().
_STATUS_CACHE = {}
_STATUS_TTL_S = 30


def status(kind, venv_dir=""):
    """Is this camera's SDK installed? {"needed", "installed", ...}

    Checked by importing IN THE TARGET INTERPRETER rather than in this one:
    the hub and the detector may be different venvs, and the one that matters
    is the one that will open the camera.
    """
    want = wanted_for(kind)
    if not want:
        return {"needed": False, "installed": True}
    python = _python_for(venv_dir)
    # Starting an interpreter is not free, and this is asked every time the
    # settings panel opens. The answer only changes when an install runs, so a
    # few seconds of memory saves a process per page view.
    cached = _STATUS_CACHE.get((kind, python))
    if cached and time.time() - cached[0] < _STATUS_TTL_S:
        installed = cached[1]
    else:
        try:
            done = subprocess.run(
                [python, "-c", "import {}".format(want["module"])],
                capture_output=True, text=True, timeout=20)
            installed = done.returncode == 0
        except (OSError, subprocess.SubprocessError):
            installed = False
        _STATUS_CACHE[(kind, python)] = (time.time(), installed)
    return {"needed": True, "installed": installed,
            "package": want["package"], "version": want["version"],
            "label": want["label"], "why": want["why"],
            "python": python}


def install(kind, venv_dir=""):
    """Install the pinned SDK for this kind of camera. (ok, message).

    The caller checks permission and writes the audit line -- this only knows
    how to fetch a package it already knew about.
    """
    want = wanted_for(kind)
    if not want:
        return False, "there is no SDK for this kind of camera"
    if not _INSTALL_LOCK.acquire(blocking=False):
        return False, ("an install is already running on this box - wait for "
                       "it to finish")
    try:
        return _install(want, kind, venv_dir)
    finally:
        _INSTALL_LOCK.release()


def _install(want, kind, venv_dir):
    _STATUS_CACHE.pop((kind, _python_for(venv_dir)), None)
    if status(kind, venv_dir).get("installed"):
        return True, "{} is already installed".format(want["label"])
    python = _python_for(venv_dir)
    # The name and version come from KNOWN, never from the request. Written as
    # one pinned specifier so no resolver decision is left to make.
    spec = "{}=={}".format(want["package"], want["version"])
    argv = [python, "-m", "pip", "install", "--disable-pip-version-check",
            # A WHEEL or nothing. There is no sdist for this package today,
            # but one uploaded to the same release tomorrow would have pip run
            # its setup.py as the user that owns the detector -- an arbitrary
            # code path that depends on an upstream maintainer's discipline
            # rather than on anything this box controls.
            "--only-binary=:all:",
            # This process's environment is inherited, and PIP_INDEX_URL,
            # PIP_EXTRA_INDEX_URL and /etc/pip.conf all silently redirect an
            # install. Only someone with host access can set those, so this is
            # depth rather than a hole -- but the index a box installs from
            # should be stated here, not inherited from wherever.
            "--isolated", "--index-url", "https://pypi.org/simple",
            spec]
    if want.get("no_deps"):
        argv.insert(4, "--no-deps")
    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=INSTALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, ("the download did not finish in {} seconds - this box "
                       "may have no route to the internet"
                       .format(INSTALL_TIMEOUT_S))
    except OSError as exc:
        return False, "could not run pip: {}".format(exc)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()
        reason = tail[-1][:300] if tail else "pip gave no reason"
        # The commonest failure on these boxes by a wide margin, and it is not
        # a fault in the product.
        if any(word in reason.lower() for word in
               ("resolve", "network", "unreachable", "temporary failure",
                "connection", "timed out", "proxy")):
            reason = ("this box cannot reach the internet, so the SDK cannot "
                      "be downloaded. Install it by hand from a machine that "
                      "can: pip download {} && copy the wheel over."
                      .format(spec))
        return False, reason
    _STATUS_CACHE.pop((kind, python), None)
    if not status(kind, venv_dir).get("installed"):
        return False, ("the package installed but the camera library still "
                       "will not load - the box may need a restart")
    return True, ("{} {} installed. Restart the system to open the camera."
                  .format(want["label"], want["version"]))
