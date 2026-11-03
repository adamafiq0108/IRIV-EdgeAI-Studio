#!/usr/bin/env python3
"""Author and sign store templates -- the OWNER's tool, never run on a
customer box.

The store is two GitHub repos (free is public, pro is private) holding one
folder per template:

    templates/<key>/manifest.json    the only signed artifact
    templates/<key>/manifest.sig     detached ed25519 over manifest.json bytes
    templates/<key>/system.json      a system export (irivai-system-export)
    templates/<key>/code/            optional store_ext python package

HEF weights do not live in the git tree -- they are release assets on the tag
``<key>-v<version>`` (no slashes: a slash in a tag makes the download URL
ambiguous), or absolute URLs into the Hailo model zoo. Either way the manifest
records url + sha256 + size per arch, and the one signature over the manifest
covers everything transitively.

Subcommands:
    keygen                       make the signing keypair (once)
    build   <export.json> <key>  write templates/<key>/ from a system export
    sign    <template-dir>       sign (or re-sign) a template's manifest
    index   <repo-dir>           regenerate index.json from the folders
    verify  <template-dir>       check signature + file hashes like a box would

Publishing = build, sign, index, git push, and for local HEFs:
    gh release create <key>-v<n> path/to/*.hef --repo <owner>/<repo>
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.request

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey)
    from cryptography.hazmat.primitives import serialization
except ImportError:                                         # pragma: no cover
    print("this tool needs the 'cryptography' package: pip install cryptography",
          file=sys.stderr)
    raise SystemExit(2)

APP = "irivai-store-template"
VERSION = 1
INDEX_APP = "irivai-store-index"
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ARCHS = ("hailo8", "hailo8l", "hailo10h")
TIERS = ("free", "pro")

#: Where the private key lives on the owner's machine. Deliberately outside
#: any repo -- the key must never be committed anywhere.
DEFAULT_KEY_PATH = pathlib.Path.home() / ".iriv" / "store_signing.key"


def _json_bytes(doc):
    """LF-only JSON bytes -- the exact bytes that get signed and hashed."""
    return json.dumps(doc, indent=2).encode("utf-8")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_private(path):
    data = pathlib.Path(path).read_bytes()
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{path} is not an ed25519 private key")
    return key


def _pubkey_b64(private):
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------------------
# keygen
# ---------------------------------------------------------------------------

def cmd_keygen(args):
    path = pathlib.Path(args.key_file)
    if path.exists() and not args.force:
        raise SystemExit(f"{path} already exists -- pass --force to overwrite "
                         "(existing signatures would stop verifying against a "
                         "new public key)")
    path.parent.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    path.write_bytes(pem)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(f"private key -> {path}   (keep it here; never commit it)")
    print(f"public  key -> {_pubkey_b64(private)}")
    print("paste the public key into core/services/store.py PUBKEY_B64 and "
          "config/store.toml [signing].pubkey")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _parse_kv(pairs, what):
    out = {}
    for item in pairs or []:
        arch, _, value = item.partition("=")
        if arch not in ARCHS or not value:
            raise SystemExit(f"--{what} wants ARCH=VALUE with ARCH one of "
                             f"{', '.join(ARCHS)} (got {item!r})")
        out[arch] = value
    return out


def _hash_url(url):
    """sha256 + size of a remote HEF. Downloaded in full on purpose: a signed
    manifest with an unchecked hash would defeat the whole chain."""
    h = hashlib.sha256()
    size = 0
    print(f"  hashing {url} ...")
    with urllib.request.urlopen(url, timeout=120) as resp:
        for chunk in iter(lambda: resp.read(1 << 16), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def cmd_build(args):
    key = args.key
    if not KEY_RE.match(key):
        raise SystemExit("template key must be lowercase letters/digits/"
                         "underscores, starting with a letter")
    export = json.loads(pathlib.Path(args.export).read_text(encoding="utf-8"))
    if export.get("app") != "irivai-system-export":
        raise SystemExit(f"{args.export} is not a system export "
                         "(download it from the hub: system menu > Export)")

    repo_dir = pathlib.Path(args.repo_dir)
    tdir = repo_dir / "templates" / key
    tdir.mkdir(parents=True, exist_ok=True)

    # system.json travels verbatim -- the box re-validates every section on
    # import anyway, and rewriting it here would only add a way to corrupt it.
    # Written as BYTES everywhere in this tool: text mode on Windows turns \n
    # into \r\n, git normalizes the blob back to \n, and the signature ends up
    # covering bytes GitHub never serves.
    (tdir / "system.json").write_bytes(_json_bytes(export))

    files = {"system.json": {"sha256": _sha256(tdir / "system.json"),
                             "size": (tdir / "system.json").stat().st_size}}

    code_meta = None
    if args.code:
        src = pathlib.Path(args.code)
        if not (src / "__init__.py").is_file():
            raise SystemExit(f"{src} has no __init__.py -- template code must "
                             "be a package exporting FUNCTIONS/OUTPUTS dicts")
        dest = tdir / "code"
        dest.mkdir(exist_ok=True)
        for f in sorted(src.rglob("*.py")):
            rel = f.relative_to(src)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f.read_bytes())
            posix = "code/" + rel.as_posix()
            files[posix] = {"sha256": _sha256(target),
                            "size": target.stat().st_size}
        code_meta = {"entrypoint": "store_ext",
                     "functions": args.code_function or [],
                     "outputs": args.code_output or []}

    if args.labels:
        lab = pathlib.Path(args.labels)
        (tdir / "model.labels").write_bytes(lab.read_bytes())
        files["model.labels"] = {"sha256": _sha256(tdir / "model.labels"),
                                 "size": (tdir / "model.labels").stat().st_size}

    # Hardware table: local .hef files become release assets on <key>-v<n>;
    # --hef-url points at an already-hosted file (Hailo zoo). Both end up as
    # url + sha256 + size so the installer treats them identically.
    hardware = {}
    tag = f"{key}-v{args.template_version}"
    for arch, path in _parse_kv(args.hef, "hef").items():
        p = pathlib.Path(path)
        if not p.is_file():
            raise SystemExit(f"no such file: {p}")
        hardware[arch] = {
            "file": p.name, "sha256": _sha256(p), "size": p.stat().st_size,
            "url": (f"https://github.com/{args.repo}/releases/download/"
                    f"{tag}/{p.name}")}
        print(f"  {arch}: upload with  gh release create {tag} "
              f"\"{p}\" --repo {args.repo}")
    for arch, url in _parse_kv(args.hef_url, "hef-url").items():
        if arch in hardware:
            raise SystemExit(f"{arch} given twice (--hef and --hef-url)")
        digest, size = _hash_url(url)
        hardware[arch] = {"file": url.rsplit("/", 1)[-1], "sha256": digest,
                          "size": size, "url": url}
    if not hardware:
        raise SystemExit("a template needs at least one --hef or --hef-url")

    meta = export.get("meta") or {}
    manifest = {
        "app": APP, "version": VERSION,
        "key": key,
        "name": args.name or meta.get("name") or key,
        "template_version": args.template_version,
        "tier": args.tier,
        "entitlements": [],
        "min_export_version": int(export.get("version") or 1),
        "description": args.description or "",
        "author": args.author,
        "files": files,
        "model": {"name": args.model_name or key,
                  "task": "detection",
                  "labels_file": "model.labels" if args.labels else "",
                  "confidence": args.confidence},
        "hardware": hardware,
    }
    if code_meta:
        manifest["code"] = code_meta
    (tdir / "manifest.json").write_bytes(_json_bytes(manifest))
    print(f"built {tdir}  (now: scripts/dev/publish_template.py sign {tdir})")


# ---------------------------------------------------------------------------
# sign / verify
# ---------------------------------------------------------------------------

def cmd_sign(args):
    tdir = pathlib.Path(args.template_dir)
    manifest = tdir / "manifest.json"
    if not manifest.is_file():
        raise SystemExit(f"{tdir} has no manifest.json")
    private = _load_private(args.key_file)
    # The signature covers the FILE BYTES, not a re-serialization -- json key
    # order or whitespace differences can never break verification.
    sig = private.sign(manifest.read_bytes())
    (tdir / "manifest.sig").write_bytes(base64.b64encode(sig))
    print(f"signed {manifest}  (pubkey {_pubkey_b64(private)[:12]}...)")


def _verify_dir(tdir, pubkey_b64):
    manifest_bytes = (tdir / "manifest.json").read_bytes()
    sig = base64.b64decode((tdir / "manifest.sig").read_text().strip())
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64))
    pub.verify(sig, manifest_bytes)                 # raises InvalidSignature
    manifest = json.loads(manifest_bytes)
    for rel, want in (manifest.get("files") or {}).items():
        got = _sha256(tdir / rel)
        if got != want["sha256"]:
            raise SystemExit(f"HASH MISMATCH {rel}: manifest {want['sha256'][:12]} "
                             f"!= file {got[:12]}")
    return manifest


def cmd_verify(args):
    if args.pubkey:
        pub = args.pubkey
    else:
        pub = _pubkey_b64(_load_private(args.key_file))
    manifest = _verify_dir(pathlib.Path(args.template_dir), pub)
    print(f"OK  {manifest['key']} v{manifest['template_version']} "
          f"tier={manifest['tier']} archs={sorted(manifest['hardware'])}")


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

def cmd_index(args):
    repo_dir = pathlib.Path(args.repo_dir)
    entries = []
    for mf in sorted(repo_dir.glob("templates/*/manifest.json")):
        m = json.loads(mf.read_text(encoding="utf-8"))
        if not (mf.parent / "manifest.sig").is_file():
            raise SystemExit(f"{mf.parent} is unsigned -- sign before indexing")
        entries.append({
            "key": m["key"], "name": m["name"],
            "version": m["template_version"], "tier": m["tier"],
            "summary": (m.get("description") or "")[:200],
            "archs": sorted(m.get("hardware") or {}),
            "has_code": bool(m.get("code")),
            "entitlements": m.get("entitlements") or [],
            "path": f"templates/{m['key']}",
        })
    doc = {"app": INDEX_APP, "version": 1, "templates": entries}
    (repo_dir / "index.json").write_bytes(_json_bytes(doc))
    # Belt and braces: stop git from ever rewriting line endings in a store
    # checkout -- a CRLF/LF flip is invisible in a diff and kills signatures.
    attrs = repo_dir / ".gitattributes"
    if not attrs.exists():
        attrs.write_bytes(b"* -text\n")
    print(f"index.json: {len(entries)} template(s)")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--key-file", default=str(DEFAULT_KEY_PATH),
                    help=f"signing key path (default {DEFAULT_KEY_PATH})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keygen", help="make the signing keypair")
    k.add_argument("--force", action="store_true")
    k.set_defaults(fn=cmd_keygen)

    b = sub.add_parser("build", help="write templates/<key>/ from an export")
    b.add_argument("export", help="system export json (hub > system > Export)")
    b.add_argument("key", help="permanent template id, e.g. ppe_line")
    b.add_argument("--repo-dir", default=".",
                   help="checkout of the store repo (default: cwd)")
    b.add_argument("--repo", default="adamafiq0108/irivai-store",
                   help="owner/name used for release-asset URLs")
    b.add_argument("--name", help="display name (default: export's name)")
    b.add_argument("--description", default="")
    b.add_argument("--author", default="IRIV")
    b.add_argument("--tier", choices=TIERS, default="free")
    b.add_argument("--template-version", type=int, default=1)
    b.add_argument("--hef", action="append", metavar="ARCH=PATH",
                   help="local .hef, becomes a release asset")
    b.add_argument("--hef-url", action="append", metavar="ARCH=URL",
                   help="already-hosted .hef (Hailo zoo); hashed by download")
    b.add_argument("--model-name", help="model id on the box (default: key)")
    b.add_argument("--labels", help="labels file, one class per line")
    b.add_argument("--confidence", type=float, default=0.35)
    b.add_argument("--code", help="folder with __init__.py exporting "
                                  "FUNCTIONS/OUTPUTS")
    b.add_argument("--code-function", action="append",
                   help="handler name in FUNCTIONS (repeatable)")
    b.add_argument("--code-output", action="append",
                   help="handler name in OUTPUTS (repeatable)")
    b.set_defaults(fn=cmd_build)

    s = sub.add_parser("sign", help="sign a template's manifest")
    s.add_argument("template_dir")
    s.set_defaults(fn=cmd_sign)

    v = sub.add_parser("verify", help="check a template like a box would")
    v.add_argument("template_dir")
    v.add_argument("--pubkey", help="base64 public key (default: derive from "
                                    "the private key file)")
    v.set_defaults(fn=cmd_verify)

    i = sub.add_parser("index", help="regenerate index.json")
    i.add_argument("repo_dir", nargs="?", default=".")
    i.set_defaults(fn=cmd_index)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
