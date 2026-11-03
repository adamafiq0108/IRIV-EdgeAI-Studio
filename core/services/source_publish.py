# -*- coding: utf-8 -*-
"""
services/source_publish.py -- push the built-in templates to the source repo.

The developer box is where templates are authored: a system filed under the
hub's "Built-in templates" folder is snapshotted into config/formats.toml, and
that file is what every box's create wizard offers. But a file on one SD card
is a single copy of the product's templates. This module is the last step of
authoring: refresh every snapshot from its system, then commit the file to the
source repository, on the branch customer releases are built from. The next
release carries the templates; every box that updates offers them.

Deliberately NOT the template store (services/store.py). The store is for
templates a customer fetches on demand -- free or licensed, signed, updated
apart from the product. Built-ins are part of the product, so they travel with
it, through the same release the code does, and need no signing of their own:
the release bundle is signed whole.

The token is the developer's, with Contents: write on the source repo and on
the release repo (where the copy for boxes goes -- see DEFAULT_MIRROR), kept
in config/source_publish.local.toml (0600, gitignored with every other
.local). Read-only elsewhere in the product, write here: this is the one
place the product writes to GitHub, and only the developer tier can reach it.

The copy for boxes is SIGNED with the store key (config/store_signing.pem,
owner's hub only; the public half ships in every box), and a box installs
nothing unsigned. A repository write is not a signature -- config/update.toml
says the repo split is not an access boundary -- so a leaked publish token
can put a file in the repo but cannot make a box accept it.
"""

import base64
import json
import time
import urllib.error
import urllib.request

from core import config
from core.services import ghnet
from core.services import provision as provision_service
from core.services import store as store_service
from core.services import system_groups as groups_service
from core.services import templates as templates_service

#: Where the token lives. Its own file, not update.local.toml: the update
#: token is READ-only and every box has one; this one WRITES and only the
#: developer hub has it. One file per secret is what keeps "rotate the
#: publish token" from touching the box's ability to update.
TOKEN_FILE = "source_publish"

DEFAULT_SOURCE = {"owner": "adamafiq0108", "repo": "irivai-systor",
                  "branch": "cytron-dashboard", "path": "config/formats.toml"}

#: The second copy, for boxes. The source repo is private to the product and a
#: customer box holds no token for it -- by design, since that token would
#: read the product's code. The RELEASE repo is the one every box already
#: reads with its update token, so the same file goes there too, and
#: services/template_updates.py fetches it from there between releases.
#: Owner and repo follow update.toml [release] unless [templates] says
#: otherwise, so a fork that moved its releases moved its templates with them.
DEFAULT_MIRROR = {"branch": "main", "path": "templates/formats.toml"}


def _target(section, defaults, config_dir=None):
    raw = (config.load("update", config_dir) or {}).get(section) or {}
    out = dict(defaults)
    for key in out:
        val = str(raw.get(key) or "").strip()
        if val:
            out[key] = val
    return out


def source(config_dir=None):
    """The repo, branch and path templates publish to. update.toml [source]
    overrides the defaults; a key left out keeps its default."""
    return _target("source", DEFAULT_SOURCE, config_dir)


def mirror(config_dir=None):
    """The repo, branch and path boxes READ the templates from. update.toml
    [templates] overrides; owner and repo default to the release repo."""
    from core.services import update as update_service      # noqa: PLC0415
    rel = update_service.settings(config_dir)
    defaults = {"owner": rel["owner"], "repo": rel["repo"], **DEFAULT_MIRROR}
    return _target("templates", defaults, config_dir)


def token(config_dir=None):
    return str((config.load_local(TOKEN_FILE, config_dir) or {}).get("token") or "")


def set_token(value, config_dir=None):
    local = dict(config.load_local(TOKEN_FILE, config_dir) or {})
    value = str(value or "").strip()
    if value:
        local["token"] = value
    else:
        local.pop("token", None)
    config.save_local(TOKEN_FILE, local, config_dir)


def status(config_dir=None):
    """What the panel shows beside the button: is a token set, where does it
    publish, and what did the last publish do. Never the token itself."""
    local = config.load_local(TOKEN_FILE, config_dir) or {}
    return {"token": bool(local.get("token")),
            "source": source(config_dir),
            "mirror": mirror(config_dir),
            # Whether the copy for boxes CAN be written from here: it is
            # signed with the store key, and only the owner's hub holds it.
            "signing": store_service.can_issue(config_dir),
            "last": local.get("last") or None}


def _remember(config_dir, **fields):
    local = dict(config.load_local(TOKEN_FILE, config_dir) or {})
    local["last"] = {"at": int(time.time()), **fields}
    config.save_local(TOKEN_FILE, local, config_dir)


def _gh(src, path, tok, method="GET", body=None):
    url = f"https://api.github.com/repos/{src['owner']}/{src['repo']}/{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"User-Agent": "irivai-source-publish",
                 "Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json"})
    with ghnet.opener().open(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def refresh_snapshots(systems_dir, config_dir, running_model=None):
    """Re-snapshot every system filed under "Built-in templates".

    Publishing commits what is in formats.toml, and what is in formats.toml is
    the snapshot taken when the system was DRAGGED into the folder -- not the
    workflow saved an hour later. Refreshing first is what makes "edit the
    template system, then press Publish" the whole procedure, with no drag
    out and back in between.

    Returns [(slug, key)] refreshed; a system that fails to snapshot is
    reported in `errors` rather than stopping the others.
    """
    filed = groups_service.assignments(config_dir)
    done, errors = [], []
    names = {}
    masters = (config.systems(config_dir) or {}).get("masters") or {}
    for slug, gid in filed.items():
        if gid != groups_service.BUILTIN_GROUP:
            continue
        key = provision_service.format_key(slug)
        entry = masters.get(slug) or {}
        existing = provision_service.builtin_formats(config_dir).get(key) or {}
        # The card's name: what the hub shows for the system, else what the
        # template was already called, else the slug. A system never renamed
        # on the hub has no `name` of its own, and "object_detection" is not
        # a label anyone would put on a card.
        name = (str(entry.get("name") or "").strip()
                or str(existing.get("label") or "").strip()
                or slug.replace("_", " ").title())
        names[key] = name
        try:
            doc = templates_service.snapshot_system(
                systems_dir, config_dir, slug, name=name, description="",
                category=templates_service.UNCATEGORIZED)
            provision_service.promote_template(
                key, doc, config_dir, category=existing.get("category"),
                model=(running_model(slug) if running_model else None))
            provision_service.set_format_draft(key, False, config_dir)
            provision_service.set_format_workbench(key, slug, config_dir)
            done.append((slug, key))
        except (OSError, ValueError) as exc:
            errors.append({"slug": slug, "error": str(exc)})
    return done, errors


def _commit(target, blob, message, tok):
    """Put `blob` at the target's path, or leave it when the branch already
    holds exactly these bytes -- GitHub would make an empty commit otherwise,
    and "published" would mean nothing happened.

    Returns {unchanged, commit, url}; raises ValueError in the words of the
    person who has to fix it.
    """
    sha = None
    try:
        meta = _gh(target, f"contents/{target['path']}?ref={target['branch']}",
                   tok)
        sha = meta.get("sha")
        if meta.get("encoding") == "base64" and meta.get("content"):
            if base64.b64decode(meta["content"]) == blob:
                return {"unchanged": True, "commit": "", "url": ""}
    except urllib.error.HTTPError as exc:
        # 404 here is "no such file yet" -- the first publish to a path. A
        # token that cannot see the repo also answers 404, and that one
        # surfaces on the PUT below, where _explain says so.
        if exc.code != 404:
            raise ValueError(_explain(exc, target))
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError(f"could not reach GitHub: {exc}")

    body = {"message": message, "branch": target["branch"],
            "content": base64.b64encode(blob).decode("ascii")}
    if sha:
        body["sha"] = sha
    try:
        out = _gh(target, f"contents/{target['path']}", tok, method="PUT",
                  body=body)
    except urllib.error.HTTPError as exc:
        raise ValueError(_explain(exc, target))
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError(f"could not reach GitHub: {exc}")
    commit = (out.get("commit") or {})
    return {"unchanged": False, "commit": str(commit.get("sha") or "")[:12],
            "url": str(commit.get("html_url") or "")}


def publish(systems_dir, config_dir, running_model=None):
    """Refresh, then commit config/formats.toml to the source repo, and the
    same bytes to the release repo for boxes to fetch.

    Returns {ok, refreshed, errors, commit, url, unchanged, mirror_commit,
    mirror_unchanged, mirror_error}. `unchanged` and `commit` describe the
    SOURCE commit -- the publish, which the next release is built from; the
    mirror_* fields describe the copy for boxes, separately, because the two
    succeed and fail separately. The copy is what makes the templates
    reachable BEFORE that release, and it is signed with the store key: on a
    hub without the key it is not written at all and mirror_error says so,
    since an unsigned copy would only be refused later, by every box, with
    nothing telling the operator whom to ask. A mirror that fails for any
    reason is reported beside a successful publish rather than failing it.

    This is the one place the product writes to GitHub, and it runs only when
    the button is pressed: nothing here is on a timer.
    """
    tok = token(config_dir)
    if not tok:
        raise ValueError("no publish token - paste a token with write access "
                         "to the source repository first")
    src = source(config_dir)
    refreshed, errors = refresh_snapshots(systems_dir, config_dir, running_model)
    if not refreshed and not provision_service.builtin_formats(config_dir):
        raise ValueError("nothing to publish - file a system under "
                         "\"Built-in templates\" first")

    path = config.root(config_dir) / "formats.toml"
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read {path.name}: {exc}")
    # LF on the wire whatever the box wrote: the file is committed beside code
    # that is LF, and a CRLF/LF flip is a diff of every line saying nothing.
    blob = blob.replace(b"\r\n", b"\n")

    keys = sorted(k for _slug, k in refreshed) or sorted(
        provision_service.builtin_formats(config_dir))
    message = ("templates: publish built-in templates from the developer hub\n\n"
               + "\n".join(f"- {k}" for k in keys)
               + "\n\nWritten by the hub's Publish templates button "
                 "(core/services/source_publish.py).")
    try:
        main = _commit(src, blob, message, tok)
    except ValueError as exc:
        _remember(config_dir, ok=False, error=str(exc))
        raise

    # The copy for boxes. Same bytes, same message: a box that fetches this
    # sees exactly what the next release will carry. Sealed with the store
    # key; the file goes first and the signature after it, so a publish that
    # dies between the two leaves a file whose signature does not match --
    # which every box refuses -- rather than one that half-verifies.
    mir = mirror(config_dir)
    copy = {"unchanged": False, "commit": "", "url": ""}
    mirror_error = ""
    if not store_service.can_issue(config_dir):
        mirror_error = ("this hub has no signing key (config/store_signing.pem), "
                        "so the copy for boxes was not written - boxes only "
                        "install signed templates")
    else:
        try:
            sig = store_service.sign_bytes(blob, config_dir).encode("ascii")
            copy = _commit(mir, blob, message, tok)
            sealed = _commit(dict(mir, path=mir["path"] + ".sig"), sig,
                             message, tok)
            # ed25519 is deterministic: the same bytes sign to the same
            # signature, so "unchanged" holds for both or neither.
            copy["unchanged"] = copy["unchanged"] and sealed["unchanged"]
            copy["commit"] = copy["commit"] or sealed["commit"]
        except (ValueError, store_service.StoreError) as exc:
            copy = {"unchanged": False, "commit": "", "url": ""}
            mirror_error = str(exc)

    result = {"ok": True, "refreshed": refreshed, "errors": errors,
              "unchanged": main["unchanged"],
              "commit": main["commit"], "url": main["url"],
              "mirror_commit": copy["commit"],
              "mirror_unchanged": copy["unchanged"],
              "mirror_error": mirror_error}
    # Preview media goes beside the signed copy (template_media_sync.py).
    from core.services import template_media_sync           # noqa: PLC0415
    result["media"] = (template_media_sync.publish(mir, tok, config_dir, message)
                       if not mirror_error else {"error": mirror_error})
    _remember(config_dir, ok=True, unchanged=main["unchanged"],
              commit=main["commit"], url=main["url"], count=len(refreshed),
              mirror_commit=copy["commit"], mirror_unchanged=copy["unchanged"],
              mirror_error=mirror_error)
    return result


def _explain(exc, src):
    """A GitHub error in the words of the person who has to fix it."""
    where = f"{src['owner']}/{src['repo']}@{src['branch']}"
    if exc.code == 401:
        return "GitHub refused the token - it has expired or was revoked"
    if exc.code in (403, 404):
        return (f"the token cannot write to {where} - it needs Contents: "
                "read and write on that repository (a 404 is how GitHub "
                "says 'not yours' for a private repo)")
    if exc.code == 409:
        return f"{where} moved while publishing - press Publish again"
    if exc.code == 422:
        return f"GitHub rejected the write to {where} (is the branch name right?)"
    return f"GitHub answered {exc.code} publishing to {where}"
