# -*- coding: utf-8 -*-
"""
board_release.py -- the terminals this box has handed to another program
(Node-RED, a customer script), and which rules use each terminal, which is
what decides whether one may be handed over.

Lifted out of board_io.py, which re-exports every name here (the routes and
the tests know them there), so that file stays under its line ceiling.
Nothing changed in the move. The board table, and released() and
port_users() as set_released calls them, are read off board_io at call time:
a test that stubs board_io.port_users still reaches set_released.
"""

import os


#: The config file holding which terminals this box has handed to something
#: else. Box-wide on purpose: a GPIO line is one piece of hardware, and two
#: systems disagreeing about who owns DO0 is the exact conflict this prevents.
RELEASED_CONFIG = "board"


def released(config_dir=None):
    """The terminals handed to another program (Node-RED, a customer script).

    The platform will not drive, read, or offer these: a released terminal is
    one this box has promised to keep its hands off. Returns a set of port ids.
    """
    from core.services import board_io                       # noqa: PLC0415
    from core import config as config_mod                    # noqa: PLC0415
    try:
        table = config_mod.load(RELEASED_CONFIG, config_dir) or {}
    except Exception:                                       # noqa: BLE001
        return set()
    names = (table.get("board") or {}).get("released") or []
    # Only ids this board actually has: a stale name from an older map must
    # not silently release nothing while the page shows it as released.
    return {str(n).upper() for n in names if str(n).upper() in board_io.PORT_BY_ID}


def _display_names(config_dir=None):
    """{slug: the name the operator gave the system}.

    A slug is a filename -- `counting_template_editor` -- and it is what the
    board page has been printing. It is not what the system is CALLED
    anywhere else on the product, so a terminal read as held by something the
    operator has never heard of. The name lives in systems.local.toml, where
    a rename writes it.
    """
    from core import config as config_mod                    # noqa: PLC0415
    names = {}
    try:
        masters = config_mod.systems(config_dir).get("masters", {}) or {}
    except Exception:                                       # noqa: BLE001
        return names
    for slug, entry in masters.items():
        name = str((entry or {}).get("name") or "").strip()
        if name:
            names[slug] = name
    return names


def port_users(systems_dir=None):
    """{port_id: [{slug, name, label, kind}]} -- every rule currently bound to
    a terminal, across every system on the box.

    Read from each system's COMPILED config rather than its canvas, because
    the compiled config is what actually drives pins: a node left on the
    canvas but not compiled drives nothing, and a rule that compiled is real
    even if nobody has the editor open. This is what makes "refuse to release
    a terminal something is using" answerable.
    """
    from core import config as config_mod                    # noqa: PLC0415
    from core.services import board_io, detector_control     # noqa: PLC0415
    if systems_dir is None:
        systems_dir = config_mod.generated_systems_dir()
    out = {}
    names = _display_names()

    def add(port, slug, label, kind):
        pid = str(port or "").upper()
        if pid in board_io.PORT_BY_ID:
            out.setdefault(pid, []).append(
                # `name` is what the operator calls it; `slug` stays because
                # it is the identity every other API call uses and a rename
                # never changes it.
                {"slug": slug, "name": names.get(slug) or slug,
                 "label": label, "kind": kind})

    for slug in _every_system(systems_dir):
        try:
            cfg = detector_control.system_config(systems_dir, slug)
        except Exception:                                   # noqa: BLE001
            continue
        for o in (cfg.get("outputs") or []):
            if not isinstance(o, dict) or o.get("type") not in ("io_out", "io_pwm"):
                continue
            add(o.get("port"), slug, str(o.get("label") or o.get("id") or "rule"),
                "output")
        for f in (cfg.get("functions") or []):
            if not isinstance(f, dict) or f.get("type") != "board_input":
                continue
            add((f.get("params") or {}).get("port"), slug,
                str(f.get("name") or f.get("id") or "input"), "input")
    return out


def _every_system(systems_dir):
    """Slugs on this box -- running or not.

    Deliberately NOT running_apps(): a stopped system's rules still own their
    terminal. Releasing a pin because the system using it happens to be
    stopped would hand it away and break that system the moment it starts.
    """
    from core import config as config_mod                    # noqa: PLC0415
    roots = {str(systems_dir), str(config_mod.generated_systems_dir())}
    seen = []
    for root in roots:
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for slug in entries:
            if slug in seen:
                continue
            if os.path.isdir(os.path.join(root, slug, "app")):
                seen.append(slug)
    return seen


def set_released(port_id, release, systems_dir=None, config_dir=None):
    """Hand a terminal to another program, or take it back. Returns the new set.

    Releasing is REFUSED while any rule is bound to the terminal, and the
    error names them. The alternative -- releasing anyway -- would drop a
    relay as a side effect of a settings click, and a machine wired to that
    relay does not care that the click was in a settings page.
    """
    from core.services import board_io                       # noqa: PLC0415
    pid = str(port_id or "").upper()
    if pid not in board_io.PORT_BY_ID:
        raise ValueError(f"unknown terminal {port_id!r}")
    current = board_io.released(config_dir)
    if release and pid not in current:
        users = board_io.port_users(systems_dir).get(pid) or []
        if users:
            where = ", ".join(sorted({f"{u['slug']} ({u['label']})" for u in users}))
            raise ValueError(
                f"{pid} is in use by {where}. Remove or disable those rules "
                f"first - releasing it now would drop the line while "
                f"something is driving it.")
        current = current | {pid}
    elif not release:
        current = current - {pid}
    from core import config as config_mod                    # noqa: PLC0415
    config_mod.save_local(RELEASED_CONFIG,
                          {"board": {"released": sorted(current)}}, config_dir)
    return current
