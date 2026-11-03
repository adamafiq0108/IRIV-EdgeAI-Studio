#!/usr/bin/env python3
"""Pack, sign and publish a platform RELEASE -- the OWNER's tool.

A release is what an appliance updates itself to. It is published to a private
repo, one per channel (config/update.toml [release] `repo` for customer
releases, `dev_repo` for developer ones), as three assets on one GitHub
Release:

    bundle.tar.gz    the product, with the customer frontend already built
    manifest.json    the only signed artifact: version, bundle sha256 + size
    manifest.sig     detached ed25519 over manifest.json's exact bytes

One signature covers the whole thing, because the manifest names the bundle's
hash and an appliance refuses a bundle whose hash does not match. That makes
GitHub a dumb transport rather than something a box has to trust.

A fourth asset rides on the same release for a box with no internet or no
token: irivai-update-v<version>.tar.gz, the OFFLINE UPDATE FILE, which is
just those three files in one archive (`offline`). The Update card's "Update
manually" takes it, checks the same signature and hash, and installs it
through the same applier (core/services/offline_file.py). Boxes checking
online never look at it: they fetch the three assets by name.

Two rules worth stating out loud:

    The OTA key is NOT the store key. An update replaces the code that
    verifies everything else on the box, including store templates, so it
    gets a key of its own with its own blast radius. `keygen` makes it.

    The bundle ships the BUILT frontend and every wheel it needs. An
    appliance has no npm, no compiler, and often no route to PyPI -- a
    release that expects to build anything on arrival is a release that
    installs on the workshop bench and nowhere else.

Subcommands:
    keygen                    make the OTA signing keypair (once)
    lock                      freeze requirements.txt -> requirements.lock
    wheelhouse                fetch aarch64 wheels, pinned by
                              requirements.constraints.txt, and write the lock
    pack                      build bundle.tar.gz from this checkout
    manifest                  write manifest.json for a bundle
    sign                      sign (or re-sign) a manifest
    verify                    check a manifest + bundle the way a box would
    offline                   pack the three into the offline update file
    publish                   create the GitHub Release and upload the assets

Typical run (CI does exactly this):
    npm --prefix frontend ci && npm --prefix frontend run build:customer
    python scripts/dev/publish_release.py lock
    python scripts/dev/publish_release.py wheelhouse
    python scripts/dev/publish_release.py pack
    python scripts/dev/publish_release.py manifest
    python scripts/dev/publish_release.py sign --key-env OTA_SIGNING_KEY
    python scripts/dev/publish_release.py verify
    python scripts/dev/publish_release.py offline
    python scripts/dev/publish_release.py publish --token-env RELEASE_REPO_TOKEN
    (a dev release: --channel dev on every step, --token-env RELEASE_DEV_REPO_TOKEN)
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.error
import urllib.request

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey)
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature
except ImportError:                                         # pragma: no cover
    print("this tool needs the 'cryptography' package: pip install cryptography",
          file=sys.stderr)
    raise SystemExit(2)

APP = "irivai-platform-release"

#: The developer channel's manifest app name. The channels are published to
#: separate repos, and this string is still the guard that bites at install
#: time: the applier refuses a manifest whose app is not the one its job asked
#: for, and a customer box's job always asks for APP. A developer bundle that
#: somehow reached one is rejected before a byte of it is unpacked.
APP_DEV = "irivai-platform-release-dev"

#: Tag marker for a developer release: v2.1.3-dev. Kept in step with
#: core/services/update.py DEV_SUFFIX; a release whose tag and whose channel
#: disagree is refused below rather than published for the wrong boxes.
DEV_SUFFIX = "-dev"

SCHEMA = 1
ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "dist" / "release"

#: The owner's OTA key. Outside every repo, and NOT store_signing.key.
DEFAULT_KEY_PATH = pathlib.Path.home() / ".iriv" / "ota_signing.key"

BUNDLE = "bundle.tar.gz"
MANIFEST = "manifest.json"
SIG = "manifest.sig"


def offline_name(ver=None):
    """The offline update file for `ver` (the VERSION file by default). A
    dev version keeps its suffix: irivai-update-v1.0.16-dev.tar.gz. Same rule
    as core/services/offline_file.offline_file_name."""
    return f"irivai-update-v{str(ver or version()).strip().lstrip('vV')}.tar.gz"

#: What a release carries. Directories are taken whole, minus the excludes.
INCLUDE = (
    "hub_server.py", "master_server.py", "run.sh", "VERSION",
    "requirements.txt", "requirements.lock",
    "core", "agent", "tools", "scripts", "systems", "config", "wheels",
)

#: What never leaves the workshop.
#:
#: config/deploy.toml decides whether a box is a customer appliance or the
#: owner's dev box, so shipping one could flip a customer install into dev
#: mode from a release -- the applier drops it a second time on unpack.
#: core/dev and scripts/dev are the owner's tooling; a customer box has no
#: endpoints behind them. frontend/ sources are 300 MB of node_modules bait:
#: the BUILT bundle under core/static/app is what a box serves.
EXCLUDE_EXACT = {
    "config/deploy.toml", "config/store_signing.pem",
    "core/dev", "scripts/dev",
    # The scripts that BUILD a box. scripts/install.sh keeps them off a
    # fresh install; without them here an update would hand them straight
    # back, and an update is the one thing every box in the field accepts.
    # Kept in step with install.sh's BENCH_ONLY by tests/smoke_benchonly.py.
    "scripts/install.sh", "scripts/setup_pi.sh", "scripts/run_all.sh",
    "scripts/install_service.sh", "scripts/setup_nvme.sh",
    "scripts/mediamtx/install.sh",
    # A provisioning secret (install.sh --config-json), never the product's:
    # if one is sitting in the checkout being tagged, it must not ride along
    # into a release everyone with the read token can download.
    "config.json",
}
EXCLUDE_PARTS = {".git", ".github", ".claude", "node_modules", "__pycache__",
                 ".venv", "var", "logs", "tests", "docs", "dist"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".local.toml")

#: A system package ships its CODE. Its recordings, its models and its own
#: local config belong to the box it runs on.
SYSTEM_SKIP = {"data", "models", "media", "history"}

#: Owner-only frontend chunks. A customer bundle must not contain them; the
#: build drops them (vite.config.js __DEV_TOOLS__), and pack checks rather
#: than trusts.
OWNER_CHUNKS = ("TemplatesPanel", "LicensesPanel", "StorePanel")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _json_bytes(doc):
    """LF-only JSON bytes -- the exact bytes that get signed.

    Written as BYTES everywhere downstream, never through text mode: on
    Windows text mode turns \n into \r\n, git normalises the blob back to \n,
    and the signature ends up covering bytes nobody ever serves.
    """
    return json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version():
    v = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not v:
        raise SystemExit("VERSION is empty")
    return v


def _load_private(args):
    """The key from a file, or from an environment variable (CI).

    CI holds it as a base64 raw ed25519 seed in a secret; a workshop holds a
    PEM. Both land as the same key object.
    """
    if getattr(args, "key_env", None):
        raw = os.environ.get(args.key_env, "").strip()
        if not raw:
            raise SystemExit(f"${args.key_env} is empty")
        try:
            return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))
        except ValueError as exc:
            raise SystemExit(f"${args.key_env} is not a base64 ed25519 key: {exc}")
    path = pathlib.Path(getattr(args, "key_file", None) or DEFAULT_KEY_PATH)
    if not path.exists():
        raise SystemExit(f"no signing key at {path} -- run keygen, or pass "
                         f"--key-env for CI")
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{path} is not an ed25519 private key")
    return key


def _pubkey_b64(private):
    return base64.b64encode(private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw)).decode("ascii")


def channel_of(args):
    """Which channel is being built, and the check that the VERSION agrees.

    Refused rather than corrected: a bundle whose tag says customer and whose
    contents are the developer build is the one mistake this whole split
    exists to prevent, and guessing which half the operator meant is not this
    tool's call to make.
    """
    ch = str(getattr(args, "channel", "") or "stable").strip().lower()
    if ch not in ("stable", "dev"):
        raise SystemExit(f"unknown channel {ch!r} -- use stable or dev")
    is_dev_version = version().lower().endswith(DEV_SUFFIX)
    if ch == "dev" and not is_dev_version:
        raise SystemExit(f"--channel dev needs a VERSION ending in "
                         f"{DEV_SUFFIX} (VERSION is {version()!r})")
    if ch == "stable" and is_dev_version:
        raise SystemExit(f"VERSION {version()!r} ends in {DEV_SUFFIX}, which "
                         f"marks a developer release -- pass --channel dev")
    return ch


def app_name(channel):
    return APP_DEV if channel == "dev" else APP


def _excluded(rel, channel="stable"):
    rel = rel.replace(os.sep, "/")
    # The developer build is exactly the customer build plus the owner's own
    # tooling, so the two directories a customer bundle must never carry are
    # the two a developer bundle exists to deliver.
    if channel == "dev" and rel in ("core/dev", "scripts/dev"):
        return False
    if rel in EXCLUDE_EXACT:
        return True
    parts = rel.split("/")
    if any(p in EXCLUDE_PARTS for p in parts):
        return True
    if rel.endswith(EXCLUDE_SUFFIX):
        return True
    # systems/<slug>/data|models|... -- code only.
    if len(parts) >= 3 and parts[0] == "systems" and parts[2] in SYSTEM_SKIP:
        return True
    return False


# ---------------------------------------------------------------------------
# keygen
# ---------------------------------------------------------------------------

def cmd_keygen(args):
    path = pathlib.Path(args.key_file or DEFAULT_KEY_PATH)
    if path.exists() and not args.force:
        raise SystemExit(f"{path} already exists -- pass --force to overwrite "
                         "(every appliance in the field verifies against the "
                         "public half of THIS key)")
    path.parent.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    path.write_bytes(private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    raw = private.private_bytes(serialization.Encoding.Raw,
                                serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    print(f"private key  -> {path}   (never commit it)")
    print(f"CI secret    -> {base64.b64encode(raw).decode('ascii')}")
    print("               store as the OTA_SIGNING_KEY Actions secret")
    print(f"public  key  -> {_pubkey_b64(private)}")
    print("               paste into core/services/update.py PUBKEY_B64 and "
          "config/update.toml [signing].pubkey")


# ---------------------------------------------------------------------------
# lock + wheelhouse
# ---------------------------------------------------------------------------

def cmd_lock(args):
    """Freeze requirements.txt into requirements.lock, for a developer running
    the pipeline BY HAND on their own machine.

    This is NOT part of the release pipeline any more: pip-compile resolves
    against PyPI with no notion of which platform a wheel exists for, so it
    happily pinned contourpy==1.3.3 the week that version shipped an x86_64
    wheel and nothing for aarch64 -- a lock file that is exact and wrong.
    `wheelhouse` now resolves directly against the board's platform and
    writes the lock from what it actually found, which cannot drift from
    reality the same way. Kept here only as a quick local pin for someone
    testing on their own machine's own architecture.
    """
    out = ROOT / "requirements.lock"
    cmd = [sys.executable, "-m", "piptools", "compile", "--quiet",
           "--output-file", str(out), str(ROOT / "requirements.txt")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # pip-tools is the nice path; without it, freeze what is installed --
        # which is what CI's fresh, pinned environment holds anyway.
        print("pip-compile unavailable, falling back to pip freeze",
              file=sys.stderr)
        r = subprocess.run([sys.executable, "-m", "pip", "freeze",
                            "--exclude-editable"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("could not produce a lock file: " + r.stderr)
        out.write_bytes(r.stdout.replace("\r\n", "\n").encode("utf-8"))
    print(f"wrote {out}")


def _wheel_name_version(filename):
    """(distribution, version) from a wheel filename, PEP 427-correctly --
    the version segment can itself contain digits-and-dots that a naive split
    on '-' would mis-slice once a build tag is present."""
    try:
        from packaging.utils import parse_wheel_filename
        name, version, _build, _tags = parse_wheel_filename(filename)
        return name, str(version)
    except Exception:                                       # noqa: BLE001
        stem = filename[:-4] if filename.endswith(".whl") else filename
        parts = stem.split("-")
        return parts[0], parts[1] if len(parts) > 1 else ""


def cmd_wheelhouse(args):
    """Download every wheel the board's platform actually has, resolved
    DIRECTLY against requirements.txt -- not against a pre-baked lock.

    pip's resolver, given --platform/--python-version/--abi, only ever
    considers a candidate that ships a matching wheel; asked to satisfy
    requirements.txt's `>=` ranges it will pick the newest version that has
    one, version by version, for every package. That is the property a
    plain pip-compile lock does not have: pip-compile resolves against
    PyPI's newest release full stop, with no notion of which platforms it
    was built for, and pinned contourpy==1.3.3 the week that release shipped
    an x86_64 wheel and no aarch64 one at all.

    Binary only: a source distribution would mean compiling on a CM5, at
    install time, on a box that is meant to be detecting. If some package's
    entire release history lacks an aarch64 wheel, this stops here rather
    than shipping a bundle that fails half-way through an update in the
    field.

    requirements.lock is then WRITTEN from what actually landed in wheels/,
    so the two can never disagree -- the applier installs `--no-index` from
    exactly this lock against exactly this wheelhouse.

    The versions are held by requirements.constraints.txt (pip -c): the
    stack the release was tested on, not whatever PyPI holds on tag day
    (v1.0.38: an unpinned resolve had already moved starlette and pydantic
    past what any box had run). A constraints file adds no package, so
    requirements.txt still decides WHAT is installed. Missing file = refusal;
    `--constraints ""` resolves unpinned, on purpose only.
    """
    cons = None
    if args.constraints:
        cons = (ROOT / args.constraints).resolve()
        if not cons.is_file():
            raise SystemExit(f"no constraints file at {cons} -- the bundle's "
                             "wheels would follow PyPI on the day; pass "
                             "--constraints \"\" to do that on purpose")
    wheels = ROOT / "wheels"
    wheels.mkdir(exist_ok=True)
    for old in wheels.glob("*.whl"):
        old.unlink()
    reqs = ROOT / "requirements.txt"
    cmd = [sys.executable, "-m", "pip", "download",
           "--dest", str(wheels), "--requirement", str(reqs),
           "--only-binary", ":all:",
           "--platform", args.platform, "--python-version", args.python,
           "--implementation", "cp", "--abi", args.abi]
    if cons:
        cmd += ["--constraint", str(cons)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("wheel download failed -- every dependency needs an "
                         f"{args.platform} wheel:\n" + (r.stderr or "")[-2000:])

    pins = {}
    for whl in sorted(wheels.glob("*.whl")):
        name, version = _wheel_name_version(whl.name)
        pins[name] = version
    lock = ROOT / "requirements.lock"
    lock.write_text(
        "\n".join(f"{name}=={version}" for name, version in sorted(pins.items()))
        + "\n", encoding="utf-8")
    print(f"wheelhouse ready: {len(pins)} wheels, wrote {lock}")
    if cons:
        # A package requirements.txt newly pulls in has no pin and floats.
        # Not a refusal (pip already resolved it), but said out loud.
        held = set()
        for line in cons.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "==" in line:
                held.add(line.split("==", 1)[0].strip().lower().replace("_", "-"))
        loose = sorted(n for n in pins if n.lower().replace("_", "-") not in held)
        print(f"pinned by {cons.name}: {len(pins) - len(loose)} of {len(pins)}")
        if loose:
            print("::warning::not in the constraints file, resolved to the "
                  "newest wheel: " + ", ".join(loose))


# ---------------------------------------------------------------------------
# pack
# ---------------------------------------------------------------------------

def _check_frontend(channel="stable"):
    app = ROOT / "core" / "static" / "app"
    want = "build" if channel == "dev" else "build:customer"
    if not (app / "index.html").is_file():
        raise SystemExit("core/static/app/index.html is missing -- build the "
                         "frontend first:\n"
                         "  npm --prefix frontend ci\n"
                         f"  npm --prefix frontend run {want}")
    found = []
    for path in (app / "assets").glob("*.js"):
        for chunk in OWNER_CHUNKS:
            if chunk in path.name:
                found.append(path.name)
    # Checked in BOTH directions. A customer bundle carrying owner panels ships
    # the workshop to a customer; a developer bundle built with build:customer
    # would quietly replace a dev box's own tooling with a stripped frontend,
    # which reads as "the update broke my Templates page".
    if channel == "dev":
        if not found:
            raise SystemExit("this is a CUSTOMER build, not the developer one "
                             "-- run `npm --prefix frontend run build`")
    elif found:
        raise SystemExit("this is an OWNER build, not a customer build "
                         f"({', '.join(found)}) -- run build:customer")


def cmd_pack(args):
    channel = channel_of(args)
    _check_frontend(channel)
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / BUNDLE
    total = 0
    count = 0

    def add(tar, path, rel):
        nonlocal total, count
        # Regular files only. A symlink would be packed as a link entry, and
        # an appliance on Python 3.11 refuses link entries outright (they are
        # the classic way out of an extraction directory) -- so a stray
        # symlink in the tree would make a bundle that verifies and will not
        # unpack. A dangling one would not even pack.
        if os.path.islink(path) or not os.path.isfile(path):
            return
        info = tar.gettarinfo(str(path), arcname=rel)
        # Deterministic metadata: same input, same bytes, same hash. Ownership
        # and timestamps from a build machine mean nothing on an appliance.
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mtime = int(args.mtime)
        info.mode = 0o755 if (info.mode & 0o100) else 0o644
        with open(path, "rb") as f:
            tar.addfile(info, f)
        total += info.size
        count += 1

    # gzip's own header carries a timestamp, so "w:gz" alone would give a
    # different sha256 for an identical tree on every build. mtime=0 makes the
    # bundle reproducible: two builds of one commit are the same bytes, which
    # is what lets anyone check that a published release is the source.
    import gzip
    with open(dest, "wb") as raw, \
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
            tarfile.open(fileobj=gz, mode="w") as tar:
        for name in INCLUDE:
            src = ROOT / name
            if not src.exists():
                continue
            if src.is_file():
                if not _excluded(name, channel):
                    add(tar, src, name)
                continue
            for dirpath, dirnames, filenames in os.walk(src):
                rel_dir = os.path.relpath(dirpath, ROOT).replace(os.sep, "/")
                dirnames[:] = [d for d in sorted(dirnames)
                               if not _excluded(f"{rel_dir}/{d}", channel)]
                for fn in sorted(filenames):
                    rel = f"{rel_dir}/{fn}" if rel_dir != "." else fn
                    if _excluded(rel, channel):
                        continue
                    add(tar, pathlib.Path(dirpath) / fn, rel)

    print(f"{dest}  [{channel}]  ({count} files, {total >> 20} MB unpacked, "
          f"{dest.stat().st_size >> 20} MB packed)")
    (OUT / "unpacked_size").write_text(str(total), encoding="utf-8")


# ---------------------------------------------------------------------------
# manifest + sign + verify
# ---------------------------------------------------------------------------

#: The list of root-owned pieces (scripts/root_steps.py, upgrade_steps.sh).
ROOT_ITEMS = "scripts/root_items.json"
#: The "upgrade" table's keys that name a release file (root_steps.UPGRADE_FILES).
UPGRADE_FILES = ("linklocal", "mediamtx")


def root_pins(bundle):
    """sha256 of root_items.json and of every file it names, read from the
    BUNDLE itself: the bytes a box unpacks are the bytes pinned. A box's root
    step installs nothing that is not pinned here, so a missing file stops
    the release rather than a box."""
    with tarfile.open(bundle, "r:gz") as tar:
        def read(rel):
            try:
                return tar.extractfile(tar.getmember(rel)).read()
            except (KeyError, AttributeError):
                raise SystemExit(f"{rel} is not in {bundle.name} -- the root "
                                 f"step could never install it")
        items = json.loads(read(ROOT_ITEMS))
        rels = [ROOT_ITEMS] + [e["src"] for e in items["helpers"] + items["nft"]]
        # The one-time steps' files (root_steps.upgrade: the link-local
        # drop-in, the gateway's mediamtx.yml), pinned the same way. Same
        # keys as root_steps.UPGRADE_FILES (tests/smoke_upgradeauto.py).
        up = items.get("upgrade") or {}
        rels += [up[key] for key in UPGRADE_FILES if up.get(key)]
        return {rel: hashlib.sha256(read(rel)).hexdigest() for rel in rels}


def cmd_manifest(args):
    bundle = OUT / BUNDLE
    if not bundle.exists():
        raise SystemExit(f"no {bundle} -- run `pack` first")
    try:
        unpacked = int((OUT / "unpacked_size").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        unpacked = bundle.stat().st_size * 4
    doc = {
        "app": app_name(channel_of(args)),
        "schema": SCHEMA,
        "version": version(),
        "created": int(args.created),
        "bundle": {"name": BUNDLE,
                   "sha256": _sha256(bundle),
                   "size": bundle.stat().st_size},
        "unpacked_size": unpacked,
        # What the board must already have. The applier checks this BEFORE it
        # downloads anything: a driver mismatch caught in preflight is a
        # message, the same mismatch caught after the swap is an outage.
        "requires": {"hailort_driver": args.hailort or ""},
        "notes_short": (args.notes or "")[:500],
        # Signed with the rest: what root may install from the unpacked tree.
        "root": root_pins(bundle),
    }
    (OUT / MANIFEST).write_bytes(_json_bytes(doc))
    print(f"{OUT / MANIFEST}  version {doc['version']}")


def cmd_sign(args):
    private = _load_private(args)
    path = OUT / MANIFEST
    data = path.read_bytes()
    sig = base64.b64encode(private.sign(data)).decode("ascii")
    (OUT / SIG).write_bytes((sig + "\n").encode("ascii"))
    print(f"{OUT / SIG}")
    print(f"public key: {_pubkey_b64(private)}")


def _config_pubkey():
    cfg = ROOT / "config" / "update.toml"
    for line in cfg.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("pubkey"):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def cmd_verify(args):
    """Check the release exactly as an appliance would: signature over the
    manifest bytes, then the bundle's hash against the manifest."""
    pub = args.pubkey or _config_pubkey()
    if not pub:
        raise SystemExit("no public key: pass --pubkey or set it in "
                         "config/update.toml")
    data = (OUT / MANIFEST).read_bytes()
    sig = base64.b64decode((OUT / SIG).read_text(encoding="utf-8").strip())
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(sig, data)
    except InvalidSignature:
        raise SystemExit("SIGNATURE DOES NOT VERIFY")
    doc = json.loads(data)
    want = app_name(channel_of(args))
    if doc.get("app") != want:
        raise SystemExit(f"this manifest says app={doc.get('app')!r}, not "
                         f"{want!r} -- it was built for the other channel")
    got = _sha256(OUT / BUNDLE)
    if got != doc["bundle"]["sha256"]:
        raise SystemExit(f"bundle hash mismatch: {got}")
    if doc.get("root") != root_pins(OUT / BUNDLE):
        raise SystemExit("the manifest's root pins do not match the bundle")
    print(f"OK -- {doc['version']} verifies")


def cmd_offline(args):
    """Pack the signed release into the ONE offline update file, then check
    that file with the box's own code (core/services/offline_file.py).

    Nothing new is signed: the file carries the bundle, manifest and
    signature as they are, so it can only ever be as trustworthy as the
    release itself. Deterministic (fixed owner, mode and mtime, gzip mtime 0)
    for the same reason the bundle is. Level 1: the bundle inside is already
    gzipped, and recompressing it hard only costs CI time.
    """
    import gzip
    import tempfile
    channel = channel_of(args)
    for name in (BUNDLE, MANIFEST, SIG):
        if not (OUT / name).exists():
            raise SystemExit(f"missing {name} -- run pack/manifest/sign first")
    doc = json.loads((OUT / MANIFEST).read_bytes())
    if doc.get("app") != app_name(channel) or doc.get("version") != version():
        raise SystemExit(f"the built manifest ({doc.get('app')}, {doc.get('version')}) "
                         f"is not this --channel {channel} release of {version()}")
    dest = OUT / offline_name()
    with open(dest, "wb") as raw, \
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0,
                          compresslevel=1) as gz, \
            tarfile.open(fileobj=gz, mode="w") as tar:
        for name in (MANIFEST, SIG, BUNDLE):
            info = tar.gettarinfo(str(OUT / name), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = int(args.mtime)
            info.mode = 0o644
            with open(OUT / name, "rb") as f:
                tar.addfile(info, f)
    sys.path.insert(0, str(ROOT))
    from core.services import offline_file                  # noqa: PLC0415
    scratch = tempfile.mkdtemp(prefix="offline-check-")
    try:
        offline_file.verify_file(str(dest), scratch, args.pubkey or _config_pubkey(),
                                 app_name(channel))
    except offline_file.OfflineError as exc:
        dest.unlink()
        raise SystemExit(f"the offline update file does not verify: {exc}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"{dest}  [{channel}]  ({dest.stat().st_size >> 20} MB, verifies)")


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

def _repo(args, channel="stable"):
    """Where a `channel` release is PUBLISHED: config/update.toml [release]
    `repo` (stable) or `dev_repo` (dev). A --repo override may not name the
    OTHER channel's repo: a dev bundle in the customer repo is readable with
    the token every customer box holds (Pipi gate 1.0.15-dev, M1), and a
    customer release in the dev repo reaches no customer box at all.

    Parsed as TOML, not scanned line by line. The scan that stood here read
    every owner/repo pair in the file and kept the last, and on 2026-09-11 a
    [source] section arrived naming the SOURCE repo for built-in templates --
    so from that day every publish aimed at irivai-systor instead of
    irivai-release. GitHub answers 404 for a repo a token cannot write, so it
    read as a token problem and was hunted as one. Sections are the whole
    point of the file; a parser that cannot see them cannot read it.
    """
    cfg = (ROOT / "config" / "update.toml").read_bytes()
    try:
        release = (tomllib.loads(cfg.decode("utf-8")).get("release") or {})
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"config/update.toml does not parse: {exc}")
    owner = str(release.get("owner") or "").strip()
    key, other = ("dev_repo", "repo") if channel == "dev" else ("repo", "dev_repo")
    repo = str(release.get(key) or "").strip()
    elsewhere = str(release.get(other) or "").strip()
    if not (owner and repo and elsewhere):
        raise SystemExit("no release repo configured: config/update.toml needs "
                         "[release] owner, repo and dev_repo")
    if repo.lower() == elsewhere.lower():
        raise SystemExit("config/update.toml names ONE repo for both channels "
                         "-- repo and dev_repo must differ")
    if args.repo:
        if args.repo.strip().lower() == f"{owner}/{elsewhere}".lower():
            raise SystemExit(f"--repo {args.repo} is the other channel's "
                             f"({other}) -- a --channel {channel} release "
                             "never goes there")
        owner, _, repo = args.repo.strip().partition("/")
    return owner, repo


def _api(url, token, data=None, method=None, ctype="application/json",
         allow_404=False):
    headers = {"Authorization": "Bearer " + token,
               "Accept": "application/vnd.github+json",
               "User-Agent": "irivai-release",
               "Content-Type": ctype}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body = r.read()
    except urllib.error.HTTPError as exc:
        # GitHub's error body names the actual reason ("Repository is empty",
        # "Resource not accessible by personal access token") -- a bare
        # HTTPError hides it, and 422 alone sent someone hunting a payload
        # bug when the repo simply had no commit to hang a release on.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:600]
        except OSError:
            pass
        if exc.code == 404 and allow_404:
            raise                          # the caller MEANS to branch on this
        # Every other 404 is a real failure wearing a misleading code. GitHub
        # answers 404, not 403, when a token cannot SEE a resource -- so a
        # fine-grained token that was never granted the release repository, or
        # granted it without Contents write, reports the repo as non-existent.
        # Re-raising bare here threw away both the body and the hint below and
        # printed a urllib stack trace instead, which is how a permissions
        # problem came to look like a bug in the publisher.
        raise SystemExit(
            f"GitHub answered {exc.code} for {method or 'GET'} {url}\n{detail}\n"
            + ("Hint: a release needs a commit to point at -- an EMPTY "
               "release repo cannot take one. Push a README to it once.\n"
               if exc.code == 422 else "")
            + ("Hint: the token must grant this REPOSITORY (a fine-grained "
               "token covering only the source repo cannot publish here) with "
               "Contents: Read and write. GitHub reports 404 rather than 403 "
               "for a resource a token cannot see, so a missing grant looks "
               "exactly like a missing repo.\n"
               if exc.code in (403, 404) else ""))
    return json.loads(body or b"{}")


def cmd_publish(args):
    token = os.environ.get(args.token_env or "", "").strip() or (args.token or "")
    if not token:
        raise SystemExit("no publish token (set --token-env or --token)")
    channel = channel_of(args)
    owner, repo = _repo(args, channel)
    tag = "v" + version()
    base = f"https://api.github.com/repos/{owner}/{repo}"

    for name in (MANIFEST, SIG, BUNDLE):
        if not (OUT / name).exists():
            raise SystemExit(f"missing {name} -- run pack/manifest/sign first")

    # The tag is what every box filters on, so it is checked against the
    # manifest that is about to be published under it: a developer bundle
    # published under a customer-looking tag would be offered to the entire
    # fleet as its next update.
    built = json.loads((OUT / MANIFEST).read_bytes()).get("app")
    if built != app_name(channel):
        raise SystemExit(f"the built manifest says app={built!r} but this is a "
                         f"--channel {channel} publish -- rebuild, do not "
                         f"republish")

    try:
        # The ONLY call that may legitimately 404: no release under this
        # tag yet, which is the normal case for a new version.
        rel = _api(f"{base}/releases/tags/{tag}", token, allow_404=True)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise SystemExit(f"GitHub answered {exc.code}: {exc.read()[:300]}")
        notes = (OUT / "notes.md")
        rel = _api(f"{base}/releases", token, method="POST", data=_json_bytes({
            "tag_name": tag, "name": tag,
            "body": notes.read_text(encoding="utf-8") if notes.exists() else "",
            "draft": False, "prerelease": bool(args.prerelease)}))
        print(f"created release {tag}")

    # ...and the NOTES are replaced too. A version re-cut after a fix carries
    # a line about that fix, and the body was only ever written when the
    # release was created, so the re-cut kept the words of the build it
    # replaced -- a release whose notes describe something else.
    notes = (OUT / "notes.md")
    body = notes.read_text(encoding="utf-8") if notes.exists() else ""
    if body and body.strip() != (rel.get("body") or "").strip():
        rel = _api(f"{base}/releases/{rel['id']}", token, method="PATCH",
                   data=_json_bytes({"body": body}))
        print(f"updated the notes on {tag}")
    # Assets are replaced, not duplicated: re-running publish after a fix must
    # leave exactly one of each, or a box picks whichever it saw first.
    existing = {a["name"]: a for a in (rel.get("assets") or [])}
    upload = rel["upload_url"].split("{", 1)[0]
    # The manifest goes LAST of the three. Until it lands there is nothing
    # signed pointing at the bundle, so a half-finished publish reads as "no
    # release yet" rather than as a release a box might try to install. The
    # offline update file (`offline`) follows, when it was built: no box
    # reads it online, so its place in the order changes nothing for them.
    names = [BUNDLE, SIG, MANIFEST]
    if (OUT / offline_name()).exists():
        names.append(offline_name())
    for name in names:
        if name in existing:
            _api(f"{base}/releases/assets/{existing[name]['id']}", token,
                 method="DELETE")
        data = (OUT / name).read_bytes()
        _api(f"{upload}?name={name}", token, data=data, method="POST",
             ctype="application/octet-stream")
        print(f"uploaded {name} ({len(data) >> 10} KB)")
    print(f"published {tag} to {owner}/{repo}")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("keygen"); p.set_defaults(fn=cmd_keygen)
    p.add_argument("--key-file"); p.add_argument("--force", action="store_true")

    p = sub.add_parser("lock"); p.set_defaults(fn=cmd_lock)

    p = sub.add_parser("wheelhouse"); p.set_defaults(fn=cmd_wheelhouse)
    # manylinux2014 (glibc 2.17) rather than a newer manylinux_2_x tag: wheels
    # built for an OLDER glibc baseline run fine on a NEWER one (Bookworm's
    # glibc is well past 2.17), and most packages -- brotli among them --
    # simply never publish under the newer, stricter tags at all. Asking for
    # 2_36 found nothing for a real dependency; this is the tag the ecosystem
    # actually uses.
    p.add_argument("--platform", default="manylinux2014_aarch64")
    p.add_argument("--python", default="3.13")
    p.add_argument("--abi", default="cp313")
    p.add_argument("--constraints", default="requirements.constraints.txt",
                   help="pip constraints file, relative to the checkout; "
                        "\"\" resolves unpinned")

    # Which channel this artefact belongs to. Passed to every step that can
    # get it wrong -- pack decides what goes IN, manifest decides what the
    # applier will accept it AS, and verify/publish check the two agree.
    def channel_flag(parser):
        parser.add_argument("--channel", default="stable",
                            choices=("stable", "dev"),
                            help="stable = the customer build; dev = the "
                                 "developer build (VERSION must end in -dev)")

    p = sub.add_parser("pack"); p.set_defaults(fn=cmd_pack)
    channel_flag(p)
    p.add_argument("--mtime", default="1700000000",
                   help="fixed timestamp for reproducible bundles")

    p = sub.add_parser("manifest"); p.set_defaults(fn=cmd_manifest)
    channel_flag(p)
    p.add_argument("--created", default=str(int(os.environ.get("SOURCE_DATE_EPOCH")
                                                or 0)))
    p.add_argument("--hailort", default="",
                   help="driver requirement, e.g. '>=5.3,<5.4'")
    p.add_argument("--notes", default="")

    p = sub.add_parser("sign"); p.set_defaults(fn=cmd_sign)
    p.add_argument("--key-file"); p.add_argument("--key-env")

    p = sub.add_parser("verify"); p.set_defaults(fn=cmd_verify)
    channel_flag(p)
    p.add_argument("--pubkey", default="")

    p = sub.add_parser("offline"); p.set_defaults(fn=cmd_offline)
    channel_flag(p)
    p.add_argument("--pubkey", default="")
    p.add_argument("--mtime", default="1700000000",
                   help="fixed timestamp for a reproducible file")

    p = sub.add_parser("publish"); p.set_defaults(fn=cmd_publish)
    channel_flag(p)
    p.add_argument("--repo", default="",
                   help="owner/repo (default: config [release] repo, or "
                        "dev_repo for --channel dev)")
    p.add_argument("--token", default=""); p.add_argument("--token-env",
                                                          default="")
    p.add_argument("--prerelease", action="store_true")

    args = ap.parse_args()
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
