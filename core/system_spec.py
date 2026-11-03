# -*- coding: utf-8 -*-
"""
system_spec.py -- What a system package declares about itself.

A system package is a folder with a system.py that defines SPEC. That is the
entire master-side integration: the hub card, the nav, and the analytics
widgets all come from here. Adding a system never edits master_server.py.

Keep this small. Everything a system might want to *do* belongs in the
contract (docs/contract-v1.md) or in its adapter; this is only what the
dashboard needs to render it.
"""

import dataclasses
import re

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Cover styles shipped by core CSS. Deployments override per system in
#: config/systems.toml -- never by editing a system package.
COVERS = ("cover-a", "cover-b", "cover-c", "cover-d", "cover-e", "cover-f")


@dataclasses.dataclass(frozen=True)
class WidgetSpec:
    """One analytics widget.

    Analytics is the only genuinely per-system part of the dashboard -- Main,
    Video Stream, Footage, Settings and Email are generic and ship in core/.

    `capability` gates the widget the same way capabilities gate nav: if the
    device doesn't report it, the widget never renders. Nothing branches on
    slug (docs/conventions.md sec 1).
    """
    id: str
    title: str
    kind: str                      # tile | line | bar | table | donut
    data_fn: object                # callable(events, stats) -> dict
    capability: str = ""
    span: int = 1                  # grid columns, 1-4

    def __post_init__(self):
        if self.kind not in ("tile", "line", "bar", "area", "table",
                             "donut", "pie", "gauge", "timeline"):
            raise ValueError(f"{self.id}: unknown widget kind {self.kind!r}")
        if not callable(self.data_fn):
            raise TypeError(f"{self.id}: data_fn must be callable")
        if not 1 <= self.span <= 4:
            raise ValueError(f"{self.id}: span must be 1-4, got {self.span}")


@dataclasses.dataclass(frozen=True)
class SystemSpec:
    """Everything the dashboard needs to render a system."""
    slug: str
    display_name: str
    subtitle: str = ""
    icon: str = ""                 # single glyph for the hub card
    cover: str = "cover-a"
    analytics: tuple = ()
    extra_pages: tuple = ()        # only for pages core/ genuinely cannot serve
    order: int = 100               # hub card ordering; ties break on slug

    def __post_init__(self):
        if not _SLUG_RE.match(self.slug or ""):
            raise ValueError(
                f"slug {self.slug!r} must be lowercase letters, digits and "
                "underscores, starting with a letter"
            )
        if not self.display_name:
            raise ValueError(f"{self.slug}: display_name is required")
        if self.cover not in COVERS:
            raise ValueError(f"{self.slug}: cover {self.cover!r} not in {COVERS}")
        for w in self.analytics:
            if not isinstance(w, WidgetSpec):
                raise TypeError(f"{self.slug}: analytics must contain WidgetSpec, got {type(w).__name__}")
        ids = [w.id for w in self.analytics]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"{self.slug}: duplicate widget ids {sorted(dupes)}")
