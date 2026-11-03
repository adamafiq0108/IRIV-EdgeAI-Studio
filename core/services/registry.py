# -*- coding: utf-8 -*-
"""
registry.py -- Discovers system packages.

A system package is <systems_dir>/<slug>/system.py defining SPEC. This module
finds them, validates them, and hands back an ordered list. master_server.py
asks the registry; it never names a system.

That indirection is the whole point: adding a system is a new folder and a
config line, with a zero-line diff to the product.
"""

import dataclasses
import importlib.util
import logging
import pathlib

from core.system_spec import SystemSpec

logger = logging.getLogger(__name__)


class SystemLoadError(Exception):
    """A system package exists but is unusable. Never silently swallowed."""


def _load_spec(system_py, slug):
    """Import <slug>/system.py in isolation and return its SPEC."""
    module_name = f"_cytron_system_{slug}"
    spec = importlib.util.spec_from_file_location(module_name, system_py)
    if spec is None or spec.loader is None:
        raise SystemLoadError(f"{slug}: cannot load {system_py}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise SystemLoadError(f"{slug}: system.py raised on import: {e}") from e

    if not hasattr(module, "SPEC"):
        raise SystemLoadError(f"{slug}: system.py defines no SPEC")

    system = module.SPEC
    if not isinstance(system, SystemSpec):
        raise SystemLoadError(
            f"{slug}: SPEC must be a SystemSpec, got {type(system).__name__}"
        )
    if system.slug != slug:
        # Folder name is the identity the deployment config and the agent's
        # system_slug both key off. A mismatch here would half-register the
        # system and be miserable to trace.
        raise SystemLoadError(
            f"{slug}: SPEC.slug is {system.slug!r} but the folder is {slug!r}"
        )
    return system


def discover(systems_dir, enabled=None, extra_dirs=None, names=None):
    """Return [SystemSpec], ordered.

    `names` is {slug: display name} from the deployment config -- an operator
    renaming a system on the hub. Applied HERE, at the single point every
    consumer reads a system's name from, so the card, the nav, the page header
    and the email digest cannot disagree about what a system is called. The
    package on disk is never rewritten: a name is a deployment's choice, the
    same way `cover` already is (see system_spec.COVERS).

    Scans the built-in `systems_dir` AND every `extra_dirs` root (the persistent
    generated-systems store, outside the repo). A generated system with the same
    slug as a built-in never appears twice -- the first root wins, so a built-in
    is not shadowed.

    `enabled` (from config/systems.toml) filters and is the deployment's switch.
    None means take everything found. A broken package raises rather than
    disappearing: a system silently missing is the worst failure mode here.
    """
    # (root, apply_enabled_filter). The built-in dir honours the `enabled`
    # switch; the generated store does NOT -- a system the operator created is
    # enabled by its very presence there, so a lost/empty enabled list never
    # hides it.
    roots = [(pathlib.Path(systems_dir), True)]
    roots += [(pathlib.Path(d), False) for d in (extra_dirs or [])]

    found, seen = [], set()
    for root, apply_enabled in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            if child.name in seen:
                continue                                   # first root wins
            system_py = child / "system.py"
            if not system_py.exists():
                logger.warning("[REGISTRY] %s has no system.py, skipping", child.name)
                continue
            if apply_enabled and enabled is not None and child.name not in enabled:
                logger.info("[REGISTRY] %s present but not enabled in config", child.name)
                continue
            seen.add(child.name)
            spec = _load_spec(system_py, child.name)
            renamed = str((names or {}).get(child.name) or "").strip()
            if renamed and renamed != spec.display_name:
                spec = dataclasses.replace(spec, display_name=renamed[:60])
            found.append(spec)

    if not any(root.is_dir() for root, _ in roots):
        raise SystemLoadError(f"no systems dir found: {systems_dir}")

    found.sort(key=lambda s: (s.order, s.slug))
    logger.info("[REGISTRY] discovered %d system(s): %s",
                len(found), ", ".join(s.slug for s in found))
    return found


class Registry:
    """Discovered systems, looked up by slug."""

    def __init__(self, systems_dir, enabled=None, extra_dirs=None, names=None):
        self._systems = discover(systems_dir, enabled, extra_dirs, names)
        self._by_slug = {s.slug: s for s in self._systems}

    def __len__(self):
        return len(self._systems)

    def __iter__(self):
        return iter(self._systems)

    def all(self):
        return list(self._systems)

    def get(self, slug):
        return self._by_slug.get(slug)

    def widgets(self, slug, capabilities):
        """Analytics widgets for a system, minus those the device can't feed."""
        system = self.get(slug)
        if not system:
            return []
        return [w for w in system.analytics
                if not w.capability or w.capability in capabilities]
