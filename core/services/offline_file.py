# -*- coding: utf-8 -*-
"""
offline_file.py -- what an offline update file is, and how one is checked.

    irivai-update-v<version>.tar.gz
        bundle.tar.gz    the signed release bundle, byte for byte
        manifest.json    the signed manifest (version, bundle sha256 + size)
        manifest.sig     ed25519 over manifest.json, the OTA key

Pure: paths and a key in, the manifest out, no box state. The box
(core/services/update_offline.py) and the laptop tool
(scripts/dev/fetch_offline.py) check a file with this same code. Nothing in
the file is trusted before the signature is: the archive must hold exactly
those three regular files (no links, no devices, nothing else), member NAMES
are compared and never used as paths, sizes are capped, the signature is
checked BEFORE the manifest is parsed, then its `app`, then the bundle is
copied out while it is hashed against the signed sha256 and size.
"""

import base64
import gzip
import hashlib
import json
import os
import tarfile
import zlib

#: The three members, by name; anything else in the file refuses it.
BUNDLE = "bundle.tar.gz"
MANIFEST = "manifest.json"
SIG = "manifest.sig"
NAMES = (BUNDLE, MANIFEST, SIG)

#: Release bundles are ~60 MB with their wheelhouse; 512 MB is far past any
#: real one and still bounded.
MAX_UPLOAD = 512 << 20
MAX_MANIFEST = 1 << 20
MAX_SIG = 4096
CHUNK = 1 << 20

#: Room for the tar headers and the end-of-archive blocks, on top of the
#: three parts' caps: what the whole archive may decompress to.
HEADROOM = 64 << 10

#: Header types tarfile would read whole into memory BEFORE the loop below
#: sees a name or a size: pax (x, g, Solaris X), GNU long name/link (L, K)
#: and GNU sparse (S). None of them is in a real file (three short names), so
#: they are refused outright rather than budgeted.
_EXTENSION_TYPES = frozenset((tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                              tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
                              tarfile.GNUTYPE_SPARSE))


class OfflineError(ValueError):
    """A refused file, in plain words for the person who uploaded it."""


def _too_big():
    return OfflineError("This file is larger inside than an update can be, so it "
                        "was not accepted.")


class _Capped:
    """The decompressed stream, with a ceiling (Pipi F1, 2026-10-05).

    tarfile reads a pax or GNU long-name header's whole declared size into
    memory before the caller gets a look at the member, and zeros compress
    about 1000:1, so the upload cap bounded nothing: a 300 KB file claiming
    one 300 MB header cost 556 MB before "not an update file". This sits
    between the GzipFile and tarfile: no single read may be larger than what
    is left of the budget, and no read or seek may pass it. seek/tell go
    through to the GzipFile so extractfile's random access still works.
    """

    def __init__(self, fobj, budget):
        self.fobj, self.budget = fobj, budget

    def read(self, n=-1):
        left = self.budget - self.fobj.tell()
        if n is None or n < 0 or n > left:
            raise _too_big()
        return self.fobj.read(n)

    def seek(self, pos, whence=0):
        if whence == 1:
            pos += self.fobj.tell()
        if whence not in (0, 1) or pos > self.budget:
            raise _too_big()
        return self.fobj.seek(pos)

    def tell(self):
        return self.fobj.tell()


class _PlainInfo(tarfile.TarInfo):
    """A TarInfo that refuses the extension headers before their body is read."""

    def _proc_member(self, tf):
        if self.type in _EXTENSION_TYPES:
            raise OfflineError("This file holds more than an update, so it was "
                               "not accepted.")
        return super()._proc_member(tf)


def offline_file_name(version):
    """What release.yml calls the file for `version` (dev versions keep
    their -dev: irivai-update-v1.0.16-dev.tar.gz)."""
    return f"irivai-update-v{str(version).strip().lstrip('vV')}.tar.gz"


def verify_manifest(data, sig_b64, pubkey_b64, expect_app):
    """Signature first, JSON second, app third -- the applier's order."""
    from cryptography.exceptions import InvalidSignature       # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PublicKey)
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(
            base64.b64decode(sig_b64.strip()), data)
    except (InvalidSignature, ValueError):
        raise OfflineError("This file is not signed by IRIV, so it was not "
                           "accepted. Download the update file again.")
    try:
        man = json.loads(data)
    except ValueError:
        raise OfflineError("This update file is damaged. Download it again.")
    app = str(man.get("app") or "")
    if app != expect_app:
        if app.endswith("-dev"):
            raise OfflineError("This update file is for developer boxes; this "
                               "is a customer box.")
        if app.startswith("irivai-platform-release"):
            raise OfflineError("This update file is for customer boxes; this "
                               "is a developer box.")
        raise OfflineError("This is not an IRIV platform update file.")
    return man


def verify_file(path, dest, pubkey_b64, expect_app, max_bundle=MAX_UPLOAD):
    """Check the offline file at `path`; on success leave its three parts in
    `dest` and return the manifest. Raises OfflineError, leaving `dest` to
    the caller to remove."""
    os.makedirs(dest, exist_ok=True)
    caps = {MANIFEST: MAX_MANIFEST, SIG: MAX_SIG, BUNDLE: max_bundle}
    gz = gzip.GzipFile(path)
    try:
        tf = tarfile.open(fileobj=_Capped(gz, sum(caps.values()) + HEADROOM),
                          mode="r:", tarinfo=_PlainInfo)
    except (tarfile.TarError, OSError, EOFError, zlib.error):
        gz.close()
        raise OfflineError("This is not an update file. Choose the file named "
                           "irivai-update-v<version>.tar.gz.")
    except BaseException:
        gz.close()
        raise
    try:
        found = {}
        for m in tf:
            if m.name not in caps or m.name in found:
                raise OfflineError("This file holds more than an update, so it "
                                   "was not accepted.")
            if not m.isreg():
                raise OfflineError("This file contains links or devices, so it "
                                   "was not accepted.")
            if m.size > caps[m.name]:
                raise OfflineError("A part of this file is larger than an update "
                                   "can be, so it was not accepted.")
            found[m.name] = m
        if set(found) != set(NAMES):
            raise OfflineError("This update file is incomplete. Download it again.")
        data = tf.extractfile(found[MANIFEST]).read(MAX_MANIFEST + 1)
        sig = tf.extractfile(found[SIG]).read(MAX_SIG + 1).decode("ascii", "replace")
        man = verify_manifest(data, sig, pubkey_b64, expect_app)
        want = man.get("bundle") or {}
        if int(want.get("size") or -1) != found[BUNDLE].size:
            raise OfflineError("The update inside this file does not match its "
                               "signature. Download it again.")
        digest = hashlib.sha256()
        src = tf.extractfile(found[BUNDLE])
        with open(os.path.join(dest, BUNDLE), "wb") as out:
            for block in iter(lambda: src.read(CHUNK), b""):
                digest.update(block)
                out.write(block)
        if digest.hexdigest() != str(want.get("sha256") or ""):
            raise OfflineError("The update inside this file does not match its "
                               "signature. Download it again.")
    except (tarfile.TarError, OSError, EOFError, zlib.error):
        raise OfflineError("This update file is damaged. Download it again.")
    finally:
        tf.close()
        gz.close()
    for name, blob in ((MANIFEST, data), (SIG, sig.encode("ascii"))):
        with open(os.path.join(dest, name), "wb") as f:
            f.write(blob)
    return man
