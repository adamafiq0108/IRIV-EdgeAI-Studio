"""Recorded footage a system can run detection against instead of a camera.

A clip or a still image, uploaded once and then opened exactly the way a
camera is (detector_control.open_capture_spec, app/camera.py). That is the
whole design: the source string carries `file:<name>` and everything
downstream -- the zones, the functions, the tracker, the outputs -- cannot
tell the difference and does not need to.

Why it earns its place rather than being a test fixture:

  * a camera that is unplugged, broken or not yet installed still leaves a
    system that has to be built and demonstrated;
  * counting logic is far easier to trust when the same footage can be run
    twice and produce the same number, which no live camera can offer;
  * a template shipped with a sample clip shows what it does before anybody
    mounts hardware.

Files live under the system's own data directory, so removing a system takes
its footage with it, and a name is never allowed to escape that directory --
see media_path, which is the only place a stored name becomes a filesystem
path.
"""
import os
import re

#: What a browser can upload. Video first, then stills. Deliberately short:
#: every entry is a container OpenCV's FFmpeg backend opens without a codec
#: pack, so an accepted upload is one that will actually play.
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
EXTS = VIDEO_EXTS + IMAGE_EXTS

#: Cap on one upload. Generous for a clip somebody films to test a count,
#: small enough that the disk a system's events live on cannot be filled by
#: one careless drag-and-drop.
MAX_BYTES = 512 * 1024 * 1024

#: Folder inside the system's data directory.
DIRNAME = "media"

#: A stored name: what survives sanitising, and the only shape media_path
#: will resolve. No separators, no dots leading anywhere.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def media_dir(data_dir, create=False):
    path = os.path.join(str(data_dir or ""), DIRNAME)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def safe_name(filename):
    """A browser's filename reduced to something storable, or None.

    Everything about the original except its stem and extension is discarded.
    A name is not a path here and never becomes one by accident: the
    basename is taken first, then anything that is not a plain character is
    replaced, so "../../etc/passwd" and "C:\\evil.mp4" both come out as
    ordinary names in the media folder or not at all.
    """
    base = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in EXTS:
        return None
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:60]
    if not stem:
        stem = "clip"
    name = f"{stem}{ext}"
    return name if NAME_RE.match(name) else None


def media_path(data_dir, name):
    """The absolute path of a stored file, or None if the name is not one.

    The containment check is done on the RESOLVED path rather than on the
    name alone. Validating the name is the first line, but a symlink or an
    exotic separator makes a name that passes look innocent and land
    somewhere else; comparing real paths is what actually holds.
    """
    if not NAME_RE.match(str(name or "")):
        return None
    base = os.path.realpath(media_dir(data_dir))
    full = os.path.realpath(os.path.join(base, str(name)))
    if full != base and not full.startswith(base + os.sep):
        return None
    return full


def is_video(name):
    return str(name or "").lower().endswith(VIDEO_EXTS)


def listing(data_dir):
    """Every stored file, newest first: [{name, bytes, kind, source}]."""
    folder = media_dir(data_dir)
    out = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return out
    for name in entries:
        if not NAME_RE.match(name):
            continue                    # not ours: never offer it as a source
        path = os.path.join(folder, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        out.append({"name": name, "bytes": stat.st_size,
                    "modified": stat.st_mtime,
                    "kind": "video" if is_video(name) else "image",
                    # What goes in the camera's `source`. Relative on
                    # purpose: a system exported as a template still points
                    # at its own footage on the next box.
                    "source": f"file:{name}"})
    out.sort(key=lambda m: m["modified"], reverse=True)
    return out


def save(data_dir, filename, blob):
    """Store an upload. Returns its listing entry, or raises ValueError."""
    name = safe_name(filename)
    if not name:
        raise ValueError(
            "that file type cannot be played - upload "
            + ", ".join(e.lstrip(".") for e in VIDEO_EXTS) + " video or "
            + ", ".join(e.lstrip(".") for e in IMAGE_EXTS) + " images")
    if not blob:
        raise ValueError("that file is empty")
    if len(blob) > MAX_BYTES:
        raise ValueError(f"that file is larger than "
                         f"{MAX_BYTES // (1024 * 1024)} MB")
    folder = media_dir(data_dir, create=True)
    # A repeat upload of the same name replaces it rather than piling up
    # clip_1, clip_2: re-uploading is what somebody does after re-filming,
    # and the camera source already points at the old name.
    path = os.path.join(folder, name)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, path)               # never a half-written file to open
    stat = os.stat(path)
    return {"name": name, "bytes": stat.st_size, "modified": stat.st_mtime,
            "kind": "video" if is_video(name) else "image",
            "source": f"file:{name}"}


def delete(data_dir, name):
    path = media_path(data_dir, name)
    if not path or not os.path.isfile(path):
        return False
    os.remove(path)
    return True
