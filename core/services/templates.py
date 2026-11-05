# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
templates.py -- reusable system blueprints, and whole-system export/import.

A template is everything an operator BUILT for a system -- its workflow graph,
its dashboard and analytics layouts, the classes it watches -- with everything
about the physical site left out. Cameras and zones are deliberately NOT in a
template: an RTSP address and a polygon drawn on one view mean nothing on the
next box, and silently importing them produces rules that read zero forever.

An EXPORT is the other half of that trade: it carries the site as well, so a
system can be moved or rebuilt exactly. Secrets and ports never travel.

Templates live in `store_dir()/templates/<category>/<slug>.json`, where the
directory IS the category -- folders the operator makes are the hierarchy, so
"create a category" needs no schema, and moving a template is a rename.

SECURITY. Everything in this module treats its input as hostile: a template
file is uploaded, and both its PATH (traversal) and its CONTENT (unvalidated
config reaching a running detector) are attacker-controlled. Two rules:
  * every path segment matches SEG_RE and the resolved path must still be
    inside templates_dir();
  * nothing is written to a system's config directly -- every section goes
    through the SAME validators the API uses (validate_function, set_zones,
    set_cameras, builder.validate), so a document that could not be created
    through the UI cannot be created through a file either.
"""

import json
import os
import re
import threading
import time

from core import config
from core.services import builder as builder_service
from core.services import class_filter as class_filter_service
from core.services import detector_control as dc
from core.services import functions as functions_service
from core.services import provision as provision_service
from core.services import workflow as workflow_service

TEMPLATE_APP = "irivai-template"
TEMPLATE_VERSION = 1
EXPORT_APP = "irivai-system-export"
EXPORT_VERSION = 1

#: One category or file name. Deliberately narrow: no dots (so "..", ".hidden"
#: and "x.json.exe" are all impossible), no separators, no drive letters.
SEG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
#: How deep the category tree may nest. Three is a filing system; thirty is a
#: way to make a path long enough to break something.
MAX_DEPTH = 3
#: A template is documents, not media. Anything larger is not a template.
MAX_TEMPLATE_BYTES = 512 * 1024
MAX_EXPORT_BYTES = 1024 * 1024
UNCATEGORIZED = "general"

#: Fields an exported config may carry into a new system. Everything else --
#: ports, hosts, pids, paths -- belongs to the box it is landing on.
_APP_KEYS = ("template", "title")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def templates_dir():
    path = os.path.join(config.store_dir(), "templates")
    os.makedirs(path, exist_ok=True)
    return path


def _segments(category):
    """Validated category path segments. Blank means the default category."""
    text = str(category or "").replace("\\", "/")
    if text.startswith("/"):
        # Containment would hold anyway (the segments are re-joined onto the
        # template root), but silently turning "/etc" into the folder "etc" is
        # a different thing than the operator asked for.
        raise ValueError("a category is a folder name, not a path from the "
                         "root of the disk")
    raw = [s for s in text.split("/") if s]
    if not raw:
        return [UNCATEGORIZED]
    if len(raw) > MAX_DEPTH:
        raise ValueError(f"a category may be at most {MAX_DEPTH} folders deep")
    for seg in raw:
        if not SEG_RE.match(seg):
            raise ValueError(f"folder name {seg!r} may only use lower-case "
                             "letters, digits, - and _")
    return raw


def _safe_path(category, slug=None):
    """An absolute path inside templates_dir(), or ValueError.

    The regex alone would be enough, but the containment check is kept as a
    second, independent barrier: it is the one that still holds if the regex is
    ever loosened by someone who does not know what it was for.
    """
    root = os.path.realpath(templates_dir())
    parts = _segments(category)
    if slug is not None:
        if not SEG_RE.match(str(slug or "")):
            raise ValueError(f"template name {slug!r} may only use lower-case "
                             "letters, digits, - and _")
        parts = parts + [f"{slug}.json"]
    target = os.path.realpath(os.path.join(root, *parts))
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("that location is outside the template folder")
    return target


def _root_for(systems_dir, slug, config_dir=None):
    """The directory a system's package sits IN.

    Systems live under two roots -- the repo's `systems/` for the shipped ones
    and the store for everything created through the UI -- and `dc` takes the
    parent and joins the slug. Per-system code (a master) is already pointed at
    the right root; this module runs in the HUB, which sees both, so it has to
    resolve which one holds this system before touching its config.
    """
    try:
        pkg = provision_service.system_slugs(systems_dir, config_dir).get(slug)
    except Exception:                                       # noqa: BLE001
        pkg = None
    return os.path.dirname(pkg) if pkg else systems_dir


def _zone_list(systems_dir, slug, config_dir=None):
    """The system's zones, read the same way the Workflow route reads them."""
    root = _root_for(systems_dir, slug, config_dir)
    return dc._zone_list(dc._merged(dc._pkg_dir(root, slug)))


def slugify(name):
    out = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    out = re.sub(r"_+", "_", out)[:40]
    if out and not out[0].isalpha() and not out[0].isdigit():
        out = "t_" + out
    return out or "template"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _read(path):
    if os.path.getsize(path) > MAX_TEMPLATE_BYTES:
        raise ValueError("that template file is too large to be a template")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# The IRIV-VS folders on the NVMe -- the owner's on-disk template library
# ---------------------------------------------------------------------------
#
# Two folders, two audiences:
#   /mnt/nvme/IRIV-VS/template        every template PUBLISHED for users --
#                                     whenever a built-in goes live (Move to
#                                     built-in), its full document lands here.
#   /mnt/nvme/IRIV-VS-Admin/template  the developer's archive -- every
#                                     template a developer saves is copied
#                                     here, published or not.
#
# These are MIRRORS for the owner's organisation and backup, not a data
# source the UI reads -- what users see is still decided by the draft flag
# and template ownership. A box without the NVMe keeps the mirrors under the
# store dir so the feature degrades, never crashes.

_NVME_ROOT = "/mnt/nvme"


def _mirror_root(name):
    base = os.environ.get("CYTRON_IRIV_ROOT") or _NVME_ROOT
    # A folder is not a drive: with the NVMe unmounted, /mnt/nvme is an empty
    # directory on the eMMC. Never written into then; the store, like the store.
    if not os.path.isdir(base) or config.unmounted_mount_of(base):
        base = str(config.store_dir())
    path = os.path.join(base, name, "template")
    os.makedirs(path, exist_ok=True)
    return path


def public_templates_root():
    """Where published (user-visible) templates are mirrored."""
    return _mirror_root("IRIV-VS")


def dev_templates_root():
    """Where a developer's saved templates are archived."""
    return _mirror_root("IRIV-VS-Admin")


def mirror_template(doc, root, slug):
    """Best-effort JSON copy into one of the IRIV-VS folders. Mirrors must
    never fail the save that triggered them."""
    try:
        safe = slugify(slug) or "template"
        path = os.path.join(root, f"{safe}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def unmirror_template(root, slug):
    """Drop a mirror copy (a template pulled back to drafts)."""
    try:
        os.remove(os.path.join(root, f"{slugify(slug) or 'template'}.json"))
        return True
    except OSError:
        return False


def list_templates():
    """Every user template, plus the categories that exist (including empty
    ones -- a folder made in advance must not vanish because it has no
    templates in it yet)."""
    root = templates_dir()
    categories, out = set(), []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        rel = "" if rel == "." else rel.replace(os.sep, "/")
        depth = len(rel.split("/")) if rel else 0
        if depth >= MAX_DEPTH:
            dirnames[:] = []            # do not descend past the declared depth
        # A folder whose name we would refuse to create is not shown either.
        dirnames[:] = sorted(d for d in dirnames if SEG_RE.match(d))
        if rel:
            categories.add(rel)
        for name in sorted(filenames):
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                doc = _read(path)
            except (OSError, ValueError):
                continue                # one broken file must not hide the rest
            out.append({
                "slug": name[:-5],
                "category": rel or UNCATEGORIZED,
                "name": str(doc.get("name") or name[:-5]),
                "description": str(doc.get("description") or ""),
                "owner": str(doc.get("owner") or ""),
                "builtin": False,
                "modified": int(os.path.getmtime(path)),
            })
    categories.add(UNCATEGORIZED)
    return {"categories": sorted(categories), "templates": out}


def load_template(category, slug):
    path = _safe_path(category, slug)
    if not os.path.exists(path):
        raise ValueError(f"no template named {slug!r} in {category or UNCATEGORIZED!r}")
    return validate_template(_read(path))


# ---------------------------------------------------------------------------
# Validation -- the chokepoint every uploaded document passes through
# ---------------------------------------------------------------------------

def validate_template(doc):
    """A cleaned template, or ValueError.

    Every section is handed to the validator that owns it, so a template can
    only describe things the UI could also have produced. Unknown keys are
    dropped rather than carried, because a key nobody validates is a key
    nobody checked.
    """
    if not isinstance(doc, dict):
        raise ValueError("a template must be a JSON object")
    if str(doc.get("app") or "") != TEMPLATE_APP:
        raise ValueError("that file is not an IRIV system template")
    try:
        version = int(doc.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version > TEMPLATE_VERSION:
        raise ValueError(f"that template was made by a newer version "
                         f"(v{version}); this box understands v{TEMPLATE_VERSION}")

    clean = {
        "app": TEMPLATE_APP,
        "version": TEMPLATE_VERSION,
        "name": str(doc.get("name") or "Template")[:80],
        "description": str(doc.get("description") or "")[:400],
        "category": "/".join(_segments(doc.get("category"))),
        "created": int(doc.get("created") or time.time()),
        # Who saved it. Templates are personal: an account sees its own (plus
        # ownerless legacy ones); only a developer sees everybody's. Empty
        # stays empty -- inventing an owner would hide legacy templates.
        "owner": str(doc.get("owner") or "")[:80],
    }

    workflows = doc.get("workflows")
    if workflows:
        clean["workflows"] = _clean_store(workflows)
    tables = doc.get("tables")
    if tables:
        if not isinstance(tables, list):
            raise ValueError("the template's tables are not in a readable shape")
        from core.services import datasets as datasets_service  # noqa: PLC0415
        clean["tables"] = [datasets_service.clean_table(t) for t in tables[:16]]
    dashboard = doc.get("dashboard")
    if dashboard:
        clean["dashboard"] = _clean_layout(dashboard)
    zones = doc.get("zones")
    if zones:
        if not isinstance(zones, list):
            raise ValueError("the template's zones are not in a readable shape")
        # Shape-checked lightly here; dc.set_zones is the real validator and
        # runs at landing, where a bad zone becomes a warning, not a loss.
        clean["zones"] = [z for z in zones[:40]
                          if isinstance(z, dict) and z.get("id")]
    clean["config"] = _clean_config(doc.get("config"))
    return clean


def _clean_store(store):
    """A workflow store whose graphs have been through clean_graph().

    clean_graph is the same sanitizer the editor's own save path uses: it drops
    unknown node types, malformed edges and anything over the node caps.
    """
    if not isinstance(store, dict):
        raise ValueError("the template's workflows are not in a readable shape")
    entries = [w for w in (store.get("workflows") or []) if isinstance(w, dict)]
    if not entries:
        return None
    out = []
    for w in entries[:1]:               # the store holds exactly one workflow
        graph = workflow_service.clean_graph(w.get("graph") or {})
        graph = workflow_service.ensure_fn_ids(graph)
        # Several Class Filters need a name each (the dashboard lists them by
        # name); a template written before that rule is named on landing.
        class_filter_service.name_filters(graph)
        out.append({
            "id": workflow_service.MAIN_ID,
            "name": str(w.get("name") or "Main")[:80],
            "description": str(w.get("description") or "")[:400],
            "enabled": True, "author": "", "created": 0, "modified": 0,
            "graph": graph,
        })
    return {"version": 3, "workflows": out}


def _valid_widgets(widgets):
    """Widgets that pass builder.validate, dropping the ones that do not.

    validate() takes the whole list and raises on the first bad member, which
    is right for a browser save -- the operator should see their mistake. For
    an imported file it is wrong: one card referring to a widget kind this
    build no longer has would cost the operator the other twenty-three.
    """
    out = []
    for w in widgets[:builder_service.MAX_WIDGETS]:
        try:
            out.extend(builder_service.validate([w]))
        except (ValueError, TypeError):
            continue
    return out


def _clean_layout(layout):
    """A builder layout whose widgets have been through builder.validate()."""
    if not isinstance(layout, dict):
        raise ValueError("the template's dashboard is not in a readable shape")
    pages = []
    for page in (layout.get("pages") or [])[:builder_service.MAX_PAGES]:
        if not isinstance(page, dict):
            continue
        pid = str(page.get("id") or "").strip()
        if pid not in ("main", "overview"):
            # apply_artifacts sweeps every other page anyway; carrying one
            # would promise the operator something that disappears on first
            # compile.
            continue
        # wf_* rows travel too now: compiled cards are no longer auto-placed
        # (they live in the "From workflow" picker until someone places them),
        # so the AUTHOR's placement in the template is the only thing that
        # puts them on a fresh system's pages -- apply_artifacts refreshes
        # their bodies in place after every compile.
        widgets = [w for w in (page.get("widgets") or []) if isinstance(w, dict)]
        pages.append({
            "id": pid,
            "name": str(page.get("name") or pid)[:60],
            "widgets": _valid_widgets(widgets),
        })
    hidden = [str(h)[:80] for h in (layout.get("hidden") or []) if str(h).strip()][:60]
    placements = {}
    for pid, entry in (layout.get("placements") or {}).items():
        if str(pid) in ("main", "overview") and isinstance(entry, dict):
            placements[str(pid)] = entry
    out = {"pages": pages}
    if hidden:
        out["hidden"] = hidden
    if placements:
        out["placements"] = placements
    return out


def _clean_config(cfg):
    """The handful of settings a template may carry. A strict whitelist: this
    is the section that reaches a running detector."""
    cfg = cfg if isinstance(cfg, dict) else {}
    out = {}
    classes = [str(c).strip()[:64] for c in (cfg.get("detector_classes") or [])
               if str(c).strip()]
    if classes:
        out["detector_classes"] = classes[:64]
    model = str(cfg.get("model") or "").strip()
    if re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$", model):
        out["model"] = model
    app = cfg.get("app") if isinstance(cfg.get("app"), dict) else {}
    app_out = {str(k): str(app[k])[:80] for k in _APP_KEYS if app.get(k)}
    if app_out:
        out["app"] = app_out
    # The capture switches and the feed's status-box rows: booleans only.
    cap = cfg.get("capture") if isinstance(cfg.get("capture"), dict) else None
    if cap is not None:
        out["capture"] = {"snap": bool(cap.get("snap")),
                          "record": bool(cap.get("record"))}
    hud = cfg.get("hud") if isinstance(cfg.get("hud"), dict) else None
    if hud is not None:
        out["hud"] = {str(k)[:20]: bool(v) for k, v in list(hud.items())[:12]}
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def save_template(category, slug, doc):
    doc = validate_template(doc)
    path = _safe_path(category, slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, path)
    return doc


def delete_template(category, slug):
    path = _safe_path(category, slug)
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True


def create_category(path):
    target = _safe_path(path)
    os.makedirs(target, exist_ok=True)
    return os.path.relpath(target, os.path.realpath(templates_dir())).replace(os.sep, "/")


def delete_category(path):
    """Remove an EMPTY category. Deleting a folder full of templates is not
    something to do as a side effect of a mis-click, so it is refused and the
    operator deletes the templates first."""
    target = _safe_path(path)
    root = os.path.realpath(templates_dir())
    if target == root:
        raise ValueError("that is the template folder itself")
    if not os.path.isdir(target):
        return False
    if os.listdir(target):
        raise ValueError("that folder still has templates in it - delete or "
                         "move them first")
    os.rmdir(target)
    return True


# ---------------------------------------------------------------------------
# Snapshot a live system into a template
# ---------------------------------------------------------------------------

def snapshot_system(systems_dir, config_dir, slug, name, description="",
                    category=UNCATEGORIZED, owner=""):
    """Capture what was BUILT on `slug`, minus everything site-specific."""
    store = workflow_service.load_store(slug)
    doc = {
        "app": TEMPLATE_APP, "version": TEMPLATE_VERSION,
        "name": name or slug, "description": description,
        "category": category, "created": int(time.time()),
        "owner": owner,
    }
    root = _root_for(systems_dir, slug, config_dir)
    merged = dc._merged(dc._pkg_dir(root, slug))
    # THE ZONES TRAVEL. They used to be treated as site-specific and stripped,
    # which left every landed chain "waiting for a zone" -- but a template is
    # normally cloned onto the same box and the same camera view, and a wrong
    # polygon is redrawn in seconds where a dead canvas is rebuilt in hours.
    # Only the CAMERA binding is site enough to strip (the landed zone falls
    # back to the first camera). Because the zones keep their ids, the
    # workflow's Zone nodes keep their bindings too.
    zones = []
    for z in merged.get("zones") or []:
        if isinstance(z, dict) and z.get("id"):
            zone = {k: v for k, v in z.items() if k != "camera"}
            zones.append(zone)
    if zones:
        doc["zones"] = zones
    if store:
        # Secret-named node fields (an old node that held its own broker
        # login) are blanked: a template is stored on disk and outlives the
        # account that saved it, and must never be a copy of a credential.
        doc["workflows"] = _scrub_node_secrets(
            _unbind_places(store, keep_zones=bool(zones)))
    layout = builder_service.load(config_dir, slug)
    if layout:
        # A camera id on a widget names THIS system's camera; a template
        # carries none (blank = the new system's first camera).
        doc["dashboard"] = _unbind_cameras(layout)
    det = merged.get("detector") or {}
    app = merged.get("app") or {}
    doc["config"] = {
        "detector_classes": list(det.get("classes") or []),
        # The model the source ran, by name, so the clone starts on the same
        # one (provision.ensure_template_model fetches it if it is missing).
        "model": str(det.get("model") or ""),
        "app": {k: app[k] for k in _APP_KEYS if app.get(k)},
        # The capture switches and the feed's status-box rows: settings an
        # operator made ON the build, so the clone behaves like the original.
        "capture": dc.get_capture(root, slug),
        "hud": dc.get_hud(root, slug),
    }
    # The dataset TABLE DEFINITIONS. They are Database-page content, not part
    # of the workflow store, so they never travelled -- and a landed template
    # whose Data Table names a table that does not exist yet is a compile
    # error that keeps the whole canvas from applying. Definitions carry no
    # site and no data: names and column types, nothing else.
    try:
        from core.services import datasets as datasets_service  # noqa: PLC0415
        defs = datasets_service.definitions(root, slug)
        if defs:
            doc["tables"] = [defs[k] for k in sorted(defs)]
    except Exception:                                       # noqa: BLE001
        pass
    return validate_template(doc)


def _unbind_places(store, keep_zones=False):
    """Strip the SITE out of a workflow graph: camera ids, and -- unless the
    template carries the zones themselves -- zone ids.

    The nodes stay. A template's value is the shape of the logic -- which
    counter feeds which condition feeds which alert -- and deleting the Zone
    nodes would destroy exactly that. With `keep_zones` (the doc carries a
    `zones` section that lands with the same ids), the bindings survive whole
    and the clone runs immediately; without it, the bindings go blank and the
    compiler's "this node needs a zone" tells the operator, per node, what to
    pick on the new box.
    """
    out = json.loads(json.dumps(store))     # never mutate the live store
    keys = ("camera",) if keep_zones else (
        "zone", "line", "line_a", "line_b", "start", "end", "camera")
    for w in out.get("workflows") or []:
        for node in (w.get("graph") or {}).get("nodes") or []:
            data = node.get("data")
            if not isinstance(data, dict):
                continue
            for key in keys:
                if key in data:
                    data[key] = ""
            if not keep_zones:
                # The geometry a Zone node carried for export/import
                # round-trips is a copy of a zone that does not exist here.
                data.pop("zoneGeometry", None)
    return out


def _unbind_cameras(layout):
    """A dashboard layout with every widget's camera blanked (blank = the
    system's first camera). A camera id names a camera on the box the layout
    was built on; on any other box the tile shows nothing, for ever, while
    looking configured (UAT-102). Never mutates the caller's layout."""
    out = json.loads(json.dumps(layout))
    for page in out.get("pages") or []:
        for w in (page.get("widgets") if isinstance(page, dict) else None) or []:
            if isinstance(w, dict) and w.get("camera"):
                w["camera"] = ""
    for w in out.get("widgets") or []:                      # unpaged layout
        if isinstance(w, dict) and w.get("camera"):
            w["camera"] = ""
    return out


# ---------------------------------------------------------------------------
# Customer templates -- "Save as template"
# ---------------------------------------------------------------------------
#
# A customer's own template is a SNAPSHOT of a system (snapshot_system above),
# filed in the one folder templates_dir()/my-templates/<slug>.json. It is a
# document and nothing else: no package, no master, no port, no unit -- the
# registry only scans systems/ for folders holding a system.py, and nothing
# reads this folder but this module, so a template can never be started,
# counted against MAX_SYSTEMS or seen by the supervisor.
#
# It reaches the create wizard under the key "user:<slug>". A built-in format
# key is [a-z][a-z0-9_]* (provision._FORMAT_KEY_RE), so the colon makes the two
# namespaces disjoint by construction -- no customer template can shadow a
# built-in, and the create route tells them apart by that prefix alone.
#
# Storage and validation reuse the saved-template library above (SEG_RE paths,
# containment check, validate_template through every section's own validator),
# so a hand-edited file in this folder is as untrusted as an uploaded one.

USER_CATEGORY = "my-templates"
USER_KEY_PREFIX = "user:"
#: Documents, a handful of them. Past this it is a filing cabinet, and every
#: one is read on each wizard open.
MAX_USER_TEMPLATES = 20
USER_NAME_MAX = 60
_USER_LOCK = threading.Lock()


def user_key(slug):
    return USER_KEY_PREFIX + slug


def parse_user_key(key):
    """The slug inside a "user:<slug>" key, or None when `key` is not one (a
    built-in key, blank). A malformed slug is a ValueError, never a path."""
    key = str(key or "")
    if not key.startswith(USER_KEY_PREFIX):
        return None
    slug = key[len(USER_KEY_PREFIX):]
    if not SEG_RE.match(slug):
        raise ValueError("that is not a saved template")
    return slug


def _user_name(name):
    if re.search(r"[\x00-\x1f\x7f]", str(name or "")):
        raise ValueError("a template name is plain text")
    text = " ".join(str(name or "").split())[:USER_NAME_MAX]
    if not text:
        raise ValueError("give the template a name")
    return text


def _user_files():
    folder = _safe_path(USER_CATEGORY)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [n[:-5] for n in names if n.endswith(".json") and SEG_RE.match(n[:-5])]


def list_user_templates():
    """Every saved customer template, newest first, each with its validated
    document under "doc" (the listing needs classes and a preview summary)."""
    out = []
    for slug in _user_files():
        path = _safe_path(USER_CATEGORY, slug)
        try:
            doc = validate_template(_read(path))
        except (OSError, ValueError):
            continue                    # one broken file must not hide the rest
        out.append({"slug": slug, "key": user_key(slug), "name": doc["name"],
                    "description": doc["description"], "owner": doc["owner"],
                    "created": doc["created"],
                    "modified": int(os.path.getmtime(path)), "doc": doc})
    out.sort(key=lambda t: t["modified"], reverse=True)
    return out


def load_user_template(slug):
    """The validated document behind "user:<slug>", or ValueError."""
    return load_template(USER_CATEGORY, slug)


def _free_user_slug(name):
    taken = set(_user_files())
    base = slugify(name)
    if base not in taken:
        return base
    for n in range(2, 100):
        candidate = f"{base[:36]}_{n}"
        if candidate not in taken:
            return candidate
    raise ValueError("too many templates already named like that")


def save_user_template(systems_dir, config_dir, slug, name, description="",
                       owner=""):
    """Snapshot system `slug` into a new customer template. Returns the listing
    row (without the document).

    WHAT IS SAVED is snapshot_system's answer: the workflow (camera bindings
    blanked, secrets scrubbed), the zones (polygons and ids, no camera), the
    dashboard layout (camera ids blanked), the dataset table DEFINITIONS, the
    class filter, model name, capture switches and status-box rows. WHAT IS
    NOT: cameras and their RTSP addresses/credentials, accounts, bot tokens,
    SMTP and MQTT passwords, hand-made outputs, ports, hosts, pids, recorded
    data, and any runtime state.
    """
    name = _user_name(name)
    description = " ".join(str(description or "").split())[:400]
    with _USER_LOCK:
        if len(_user_files()) >= MAX_USER_TEMPLATES:
            raise ValueError(f"you can keep at most {MAX_USER_TEMPLATES} "
                             "templates - delete one first")
        if any(t["name"].lower() == name.lower() for t in list_user_templates()):
            raise ValueError(f"you already have a template named {name!r}")
        doc = snapshot_system(systems_dir, config_dir, slug, name, description,
                              category=USER_CATEGORY, owner=owner)
        # snapshot_system answers a VALIDATED doc, so this measures what will
        # be written; a build too big to be a template is refused, not cut.
        if len(json.dumps(doc)) > MAX_TEMPLATE_BYTES:
            raise ValueError("that system is too large to save as a template")
        tslug = _free_user_slug(name)
        save_template(USER_CATEGORY, tslug, doc)
    return {"slug": tslug, "key": user_key(tslug), "name": name,
            "description": description}


def rename_user_template(slug, name, description=None):
    name = _user_name(name)
    with _USER_LOCK:
        doc = load_user_template(slug)
        if any(t["name"].lower() == name.lower() and t["slug"] != slug
               for t in list_user_templates()):
            raise ValueError(f"you already have a template named {name!r}")
        doc["name"] = name
        if description is not None:
            doc["description"] = " ".join(str(description).split())[:400]
        save_template(USER_CATEGORY, slug, doc)
    return {"slug": slug, "key": user_key(slug), "name": name,
            "description": doc["description"]}


def delete_user_template(slug):
    if not SEG_RE.match(str(slug or "")):
        raise ValueError("that is not a saved template")
    with _USER_LOCK:
        return delete_template(USER_CATEGORY, slug)


# ---------------------------------------------------------------------------
# Apply a template to a freshly created system
# ---------------------------------------------------------------------------

def apply_template(systems_dir, config_dir, slug, doc):
    """Write a validated template onto a new system. Returns warnings.

    Best effort by section: a template whose workflow will not compile (because
    its Zone nodes still need binding -- the normal case) must still deliver
    its dashboard and its class list. Nothing here raises for content reasons;
    the operator gets a list of what did not apply.
    """
    doc = validate_template(doc)
    warnings = []

    # Tables FIRST: the workflow that follows references them by name, and
    # the recompile below refuses a card reading a table that does not exist.
    if doc.get("tables"):
        try:
            from core.services import datasets as datasets_service  # noqa: PLC0415
            # install_tables, not apply_declared: apply_declared never CREATES
            # a table (its no-create rule guards against tables invented by
            # wiring a node), and creating these is the whole point here.
            # _root_for, like every other write in this function: a created
            # system lives under the STORE root, and writing into the repo's
            # systems/ dir failed with "No such file or directory" the moment
            # the two roots differed.
            datasets_service.install_tables(
                _root_for(systems_dir, slug, config_dir), slug, doc["tables"])
        except (OSError, ValueError) as exc:
            warnings.append(f"the data tables could not be created: {exc}")

    # Zones BEFORE the workflow: the graph's Zone nodes reference them by id,
    # and the recompile below switches any chain whose zone is missing off.
    if doc.get("zones"):
        try:
            dc.set_zones(_root_for(systems_dir, slug, config_dir), slug,
                         doc["zones"])
        except (OSError, ValueError) as exc:
            warnings.append(f"the zones could not be created: {exc}")

    if doc.get("workflows"):
        try:
            workflow_service.save_store(slug, doc["workflows"])
        except OSError as exc:
            warnings.append(f"the workflow could not be written: {exc}")

    if doc.get("dashboard"):
        try:
            builder_service.save(config_dir, slug, doc["dashboard"])
        except (OSError, ValueError) as exc:
            warnings.append(f"the dashboard layout could not be written: {exc}")

    cfg = doc.get("config") or {}
    classes = cfg.get("detector_classes")
    if classes:
        try:
            dc.set_detector_params(_root_for(systems_dir, slug, config_dir),
                                   slug, classes=classes)
        except (OSError, ValueError) as exc:
            warnings.append(f"the class filter could not be applied: {exc}")
    if isinstance(cfg.get("capture"), dict):
        try:
            dc.set_capture(_root_for(systems_dir, slug, config_dir), slug,
                           snap=bool(cfg["capture"].get("snap")),
                           record=bool(cfg["capture"].get("record")))
        except (OSError, ValueError) as exc:
            warnings.append(f"the capture switches could not be applied: {exc}")
    if isinstance(cfg.get("hud"), dict):
        try:
            dc.set_hud(_root_for(systems_dir, slug, config_dir), slug,
                       cfg["hud"])
        except (OSError, ValueError) as exc:
            warnings.append(f"the status-box settings could not be applied: {exc}")

    warnings += recompile(systems_dir, config_dir, slug)
    return warnings


def apply_format_workflow(systems_dir, config_dir, slug, fmt, key=""):
    """Apply a built-in format's published workflow to a just-created system.

    In core/services, NOT core/dev: authoring a format is developer work, but
    STARTING from one is what every admin does, and a published format now
    carries the whole build (the Built-in templates folder publishes the
    workbench's snapshot). On a customer install -- which has no core/dev at
    all -- this is the only path that delivers it.
    """
    fmt = fmt or {}
    if not any(fmt.get(k) for k in ("workflows", "dashboard", "tables",
                                    "settings")):
        return []
    doc = {"app": TEMPLATE_APP, "version": TEMPLATE_VERSION,
           "name": fmt.get("label") or key or slug}
    # A BUILT-IN format lands on any customer's box, not on the box it was
    # authored on (UAT-102): People Counting seeded "Live video" tiles for the
    # author's ip_camera_192_168_1_67/_65 and zones drawn on the author's
    # picture onto a system with no camera at all. So the site goes, here,
    # whatever the published record carries -- a template update from the
    # authoring box cannot bring it back. Zones are NOT landed and the
    # workflow's zone bindings go blank (the compiler then names, per node,
    # what to pick: the wizard's "zone bindings are deliberately blank"), and
    # every widget's camera is blank = the system's first camera. A SAVED
    # template (apply_template direct) still carries its zones by design.
    if fmt.get("workflows"):
        doc["workflows"] = _unbind_places(fmt["workflows"], keep_zones=False)
    if fmt.get("tables"):
        doc["tables"] = fmt["tables"]
    # The dashboard ARRANGEMENT -- pages, placements, sizes -- not only the
    # widgets. It is validated on landing like any uploaded template.
    if isinstance(fmt.get("dashboard"), dict):
        doc["dashboard"] = _unbind_cameras(fmt["dashboard"])
    if isinstance(fmt.get("settings"), dict):
        doc["config"] = {k: v for k, v in fmt["settings"].items()
                         if k in ("capture", "hud")}
    try:
        warnings = apply_template(systems_dir, config_dir, slug, doc)
    except (ValueError, OSError) as exc:
        return [f"the format's workflow could not be applied: {exc}"]
    # The detector's confidence threshold travels in the format's settings
    # (not part of the validated template doc, which has no field for it): the
    # author tuned it for what the build detects, and a system created from
    # the format should start there rather than on the box default.
    conf = (fmt.get("settings") or {}).get("confidence") \
        if isinstance(fmt.get("settings"), dict) else None
    if conf is not None:
        try:
            dc.set_detector_params(_root_for(systems_dir, slug, config_dir),
                                   slug, confidence=conf)
        except (OSError, ValueError) as exc:
            warnings.append(f"the confidence threshold could not be applied: {exc}")
    return warnings


def recompile(systems_dir, config_dir, slug):
    """Compile the system's workflow and apply the artifacts, the same way the
    Workflow page's save does. Compile errors are REPORTED, not raised: a
    template whose zone nodes are waiting to be bound is the expected state
    right after creation, and the system must still open."""
    store = workflow_service.load_store(slug)
    if not store:
        return []
    root = _root_for(systems_dir, slug, config_dir)
    try:
        zones = _zone_list(root, slug, config_dir)
    except Exception:                                       # noqa: BLE001
        zones = []
    # The dataset tables MUST ride along, exactly as the Workflow page's own
    # save passes them (core/routes/workflow.py). Without them every Query
    # and Data Table node fails validation -- "nothing is wired into this
    # Query" -- and one such error stops apply_artifacts for the whole
    # canvas, which is how a template with a dataset landed as an empty
    # dashboard while its graph sat on the Workflow page looking complete.
    try:
        from core.services import datasets as datasets_service  # noqa: PLC0415
        tables = datasets_service.definitions(root, slug)
    except Exception:                                       # noqa: BLE001
        tables = {}
    try:
        compiled = workflow_service.compile_store(store, zones, tables=tables)
    except Exception as exc:                                # noqa: BLE001
        return [f"the workflow could not be compiled: {exc}"]
    if compiled.get("errors"):
        first = compiled["errors"][0]
        detail = first.get("message") if isinstance(first, dict) else str(first)
        return [f"the workflow needs attention before it runs: {detail}"]
    try:
        workflow_service.apply_artifacts(root, config_dir, slug, compiled)
    except Exception as exc:                                # noqa: BLE001
        return [f"the compiled workflow could not be applied: {exc}"]
    # Warnings matter as much as errors here. A template's rules arrive with
    # their zones and cameras stripped, so parts of it are routinely switched
    # off on landing -- reporting only hard errors would show a clean result
    # for a workflow that is not actually running.
    out = []
    for item in (compiled.get("warnings") or [])[:5]:
        detail = item.get("message") if isinstance(item, dict) else str(item)
        out.append(f"the workflow needs attention: {detail}")
    return out


# ---------------------------------------------------------------------------
# Whole-system export / import
# ---------------------------------------------------------------------------

def export_system(systems_dir, config_dir, slug):
    """Everything needed to rebuild this system on another box.

    Unlike a template this DOES carry the site -- cameras and zones -- because
    the point is to move a system, not to start one like it. What never travels:
      * ports and hosts, which belong to the box (reassigned on import);
      * secrets (SMTP passwords, bot tokens), which must not sit in a file
        people mail to each other;
      * compiled wf_* functions/outputs/widgets, which are rebuilt from the
        workflow on arrival -- carrying them would install a stale copy.
    """
    root = _root_for(systems_dir, slug, config_dir)
    merged = dc._merged(dc._pkg_dir(root, slug))
    det = merged.get("detector") or {}
    app = merged.get("app") or {}
    doc = {
        "app": EXPORT_APP, "version": EXPORT_VERSION,
        "exported_at": int(time.time()),
        "meta": {"slug": slug,
                 "name": str(app.get("title") or slug),
                 "template": str(app.get("template") or "")},
        "cameras": dc.cameras(root, slug),
        "zones": _zone_list(root, slug, config_dir),
        "detector": {"classes": list(det.get("classes") or []),
                     "confidence": det.get("confidence")},
        "functions": [f for f in (merged.get("functions") or [])
                      if not str((f or {}).get("id") or "").startswith(("wf_", "z_"))],
        "outputs": _strip_secrets([o for o in (merged.get("outputs") or [])
                                   if not str((o or {}).get("id") or "").startswith("wf_")]),
    }
    store = workflow_service.load_store(slug)
    if store:
        # A node saved before MQTT Connections still holds its own broker
        # login in its data: never into a file people mail to each other.
        doc["workflows"] = _scrub_node_secrets(store)
    layout = builder_service.load(config_dir, slug)
    if layout:
        doc["dashboard"] = layout
    # The MQTT Connections the nodes name (Yaya, review of a34da37, M3):
    # without them an imported node points at a Connection that does not
    # exist. Everything but the password, which the importing box asks for.
    from core.services import mqtt_clients as mqtt_clients_service  # noqa: PLC0415
    conns = mqtt_clients_service.export_rows(root, slug)
    if conns:
        doc["mqtt_clients"] = conns
    return doc


def _scrub_node_secrets(store):
    """A copy of a workflow store with every secret-named string in node
    data blanked (core/services/redact.py names them)."""
    import copy as _copy                                       # noqa: PLC0415
    from core.services import redact                           # noqa: PLC0415
    out = _copy.deepcopy(store)
    for w in out.get("workflows") or []:
        for n in ((w.get("graph") or {}).get("nodes") or []):
            d = n.get("data")
            if isinstance(d, dict):
                for k in [k for k, v in d.items()
                          if redact.is_secret_key(k) and isinstance(v, str)]:
                    d[k] = ""
    return out


#: Output fields that are credentials. Dropped on export; the target box's own
#: settings supply them again when the workflow is recompiled there.
_SECRET_KEYS = ("password", "app_password", "smtp_pass", "bot_token", "token",
                "api_key", "secret")


def _strip_secrets(items):
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append({k: v for k, v in item.items()
                    if not any(s in str(k).lower() for s in _SECRET_KEYS)})
    return out


def _free_slug(systems_dir, name, config_dir=None):
    """A slug no system on this box is using yet.

    Importing a system that came FROM this box (or from an identical one) is
    the normal case -- a backup restored beside the original, a second line set
    up like the first -- so a name collision must not be an error. The slug is
    the folder and config key, and it is invisible to the operator; the NAME
    they see is whatever they asked for, and two systems may share it.

    Renaming a system does NOT free its slug: the slug is identity (stat keys,
    stored layouts, workflow files, existing URLs all point at it) and moving
    it would break those for a cosmetic gain. So this deconflicts here rather
    than relying on the operator having renamed anything.
    """
    base = provision_service._slugify(name) or "imported_system"
    try:
        taken = set(provision_service.system_slugs(systems_dir, config_dir))
    except Exception:                                       # noqa: BLE001
        taken = set()
    # Registered systems are not the whole story: a half-removed package, or a
    # folder left behind by an earlier failure, still owns its directory and
    # would make write_package refuse. Anything ON DISK counts as taken.
    for root in (str(systems_dir), str(config.generated_systems_dir())):
        try:
            taken.update(os.listdir(root))
        except OSError:
            continue
    if base not in taken:
        return base
    for n in range(2, 100):
        candidate = f"{base}_{n}"[:40].strip("_")
        if candidate not in taken:
            return candidate
    raise ValueError(f"too many systems already named like {base!r}")


def validate_export(doc):
    """Shape-check an uploaded export. The CONTENT is not trusted here -- each
    section is validated by its owning writer at import time, which is the only
    place that knows what this box can accept."""
    if not isinstance(doc, dict):
        raise ValueError("that file is not a system export")
    if str(doc.get("app") or "") != EXPORT_APP:
        raise ValueError("that file is not an IRIV system export")
    try:
        version = int(doc.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version > EXPORT_VERSION:
        raise ValueError(f"that export was made by a newer version (v{version}); "
                         f"this box understands v{EXPORT_VERSION}")
    return doc


def import_system(systems_dir, config_dir, doc, name=None, owner="",
                  db_path=None):
    """Create a system from an export. Returns (created, warnings), where
    `created` is create_vision_system's dict (slug, name, url, port, ...).

    The app CODE is never imported -- create_vision_system scaffolds it fresh
    from the reference package. Only documents cross the boundary, and every
    one of them goes through the same validator the API would have used.

    `db_path` is an optional SQLite export (services/dbexport) to load into the
    new system. It is SEPARATE from the JSON export on purpose, and always
    will be: the JSON is a design -- cameras, zones, workflow, dashboards --
    small enough to mail and safe to hand around, while the data is the
    recorded history and can be gigabytes. Bundling them would make every
    export of a design carry a year of events, so the two travel together only
    when somebody says they should.
    """
    doc = validate_export(doc)
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    wanted = str(name or meta.get("name") or meta.get("slug") or "Imported system")
    # The template the export names, where this box still has it; else the
    # box's default (the blank canvas where published). An export made from a
    # template this box never had -- or one dropped since -- must still
    # import: the design travels in the document, the template only seeds.
    formats = provision_service.builtin_formats(config_dir)
    want_tpl = str(meta.get("template") or "")
    if want_tpl not in formats:
        want_tpl = provision_service.default_template(config_dir)
    created = provision_service.create_vision_system(
        systems_dir, wanted, config_dir, slug=_free_slug(systems_dir, wanted, config_dir),
        template=want_tpl, owner=owner)
    slug = created["slug"]
    root = _root_for(systems_dir, slug, config_dir)
    warnings = []

    # Cameras BEFORE zones: a zone names the camera it was drawn on, and
    # set_zones refuses one that points at a camera this system does not have.
    cameras = [c for c in (doc.get("cameras") or []) if isinstance(c, dict)]
    if cameras:
        try:
            dc.set_cameras(root, slug, cameras, config_dir)
        except ValueError as exc:
            # The usual cause is the box's camera budget: another system is
            # already running six devices. Land them switched OFF rather than
            # losing them -- the settings are what the operator wanted kept.
            warnings.append(f"{exc} The cameras were added switched off.")
            try:
                dc.set_cameras(root, slug,
                               [dict(c, enabled=False) for c in cameras], config_dir)
            except ValueError as exc2:
                warnings.append(f"the cameras could not be added at all: {exc2}")

    zones = [z for z in (doc.get("zones") or []) if isinstance(z, dict)]
    if zones:
        try:
            dc.set_zones(root, slug, zones)
        except ValueError as exc:
            warnings.append(f"the zones could not be applied: {exc}")

    det = doc.get("detector") if isinstance(doc.get("detector"), dict) else {}
    if det.get("classes"):
        try:
            dc.set_detector_params(root, slug, classes=list(det["classes"]))
        except (OSError, ValueError) as exc:
            warnings.append(f"the class filter could not be applied: {exc}")

    # The MQTT Connections FIRST: the functions, outputs and nodes below name
    # them by id, and the ids are re-made here (deduplicated by host, port and
    # user name), so every reference is pointed at the new one.
    from core.services import mqtt_clients as mqtt_clients_service  # noqa: PLC0415
    conn_map = {}
    if doc.get("mqtt_clients"):
        conn_map, conn_warn = mqtt_clients_service.import_rows(
            root, slug, doc["mqtt_clients"])
        warnings += conn_warn

    # Hand-made functions and outputs, one at a time: one bad item must not
    # cost the operator the other nineteen.
    kept_fns, kept_outs = [], []
    for item in mqtt_clients_service.remap(list(doc.get("functions") or []), conn_map):
        try:
            kept_fns.append(functions_service.validate_function(item))
        except (ValueError, TypeError, AttributeError):
            warnings.append(f"a saved function was dropped: "
                            f"{(item or {}).get('id') or 'unnamed'}")
    for item in mqtt_clients_service.remap(list(doc.get("outputs") or []), conn_map):
        try:
            kept_outs.append(dc._validate_output(item))
        except (ValueError, TypeError, AttributeError):
            warnings.append(f"a saved output was dropped: "
                            f"{(item or {}).get('id') or 'unnamed'}")
    if kept_fns:
        try:
            functions_service.set_functions(root, slug, kept_fns)
        except (OSError, ValueError) as exc:
            warnings.append(f"the saved functions could not be written: {exc}")
    if kept_outs:
        try:
            dc.set_outputs(root, slug, kept_outs)
        except (OSError, ValueError) as exc:
            warnings.append(f"the saved outputs could not be written: {exc}")

    if doc.get("workflows"):
        try:
            workflow_service.save_store(slug, mqtt_clients_service.remap(
                _clean_store(doc["workflows"]), conn_map))
        except (OSError, ValueError) as exc:
            warnings.append(f"the workflow could not be written: {exc}")
    if doc.get("dashboard"):
        try:
            builder_service.save(config_dir, slug, _clean_layout(doc["dashboard"]))
        except (OSError, ValueError) as exc:
            warnings.append(f"the dashboard layout could not be written: {exc}")

    warnings += recompile(systems_dir, config_dir, slug)

    # The DATA, last: everything above decides what the tables and the cards
    # ARE, and a row belongs to a table. Importing rows first would land them
    # against definitions that do not exist yet.
    if db_path:
        from core.services import dbimport as dbimport_service
        try:
            # "append", never "replace": the system was created seconds ago
            # and has nothing to replace. Naming the mode anyway, because a
            # default that silently deletes is a default nobody should have to
            # look up.
            summary = dbimport_service.import_file(
                db_path, slug, config_dir, root, mode="append")
            created["database"] = summary
            warnings += summary.get("warnings") or []
            if not summary.get("ok", True) and summary.get("error"):
                warnings.append(f"the database was not imported: "
                                f"{summary['error']}")
        except dbimport_service.NotAnExport as exc:
            # The SYSTEM still imported. Losing the whole thing because the
            # second file was wrong would be the worst possible trade.
            warnings.append(f"the database file was not imported: {exc}")
        except Exception as exc:                            # noqa: BLE001
            warnings.append(f"the database could not be imported: {exc}")
    return created, warnings
