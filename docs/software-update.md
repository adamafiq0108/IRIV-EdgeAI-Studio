Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.

# Software update

A box is installed once, from the OS image. After that an admin updates it by
uploading a release file on **About -> Software update**. Nothing is downloaded
from the internet and there is no over-the-air channel.

## Building a release (on your computer)

```
cd frontend && npm run build        # builds the dashboard into core/static/app
# bump VERSION first: a box refuses a version it is already running
python scripts/make_release.py      # writes dist/irivai-<version>.tar.gz
```

The file holds the code, the built dashboard and `MANIFEST.sha256`, the hash of
every other file. It never carries a box's own settings or keys
(`config/*.local.toml`, keys, `.env`), and a box refuses a file that does.

## Installing a release (on the box)

1. About -> Software update -> **Choose update file**.
2. The box checks the file and shows its version. Nothing is installed yet.
3. **Install**. The dashboard and every system restart (about a minute). The
   page reloads by itself when the box is back.

The box's settings, accounts and recorded data are kept: each
`config/*.local.toml` and each built-in system's own `*.local.toml` is copied
into the new version.

**Go back to <version>** puts the previous version live again.

## How it is laid out

```
/opt/irivai-systor            a symlink to the live release
/opt/irivai-releases/vX.Y.Z   one folder per installed version
/opt/irivai-releases/.previous   the name of the release that was live before
/opt/irivai-venv              the Python environment, shared by every release
```

Only two releases are kept, the live one and the previous one. After every
switch `scripts/apply_release.py` removes the rest, so a box updated for years
does not fill its drive. The switch is one rename over the link, so a power cut
leaves the box on the old release or the new one, never neither.

The upload runs as the service user; switching the link needs root, so
`scripts/apply_release.py` is installed root-owned at
`/usr/local/lib/irivai/apply-release.py` with a sudoers rule
(`/etc/sudoers.d/irivai-release`) that names it with no arguments.

## What the check proves

The manifest catches a damaged or half-sent upload and a file that is not an
IRIV release. It is **not a signature**: there is no key, so it cannot say who
made the file. The protection against a hostile file is the admin login that
gates the upload. The box also refuses a file with absolute or `..` paths,
links, device files, or any settings or key file inside.

## Making an OS image that can be updated

The box must be on the layout above. On a box with a plain `/opt/irivai-systor`
folder, move it to `/opt/irivai-releases/v<version>`, move its `.venv` to
`/opt/irivai-venv` and link it back, point `/opt/irivai-systor` at the release,
and install the helper and sudoers rule (`scripts/install.sh` does both).
A box that is not on this layout shows "not set up for software updates".

Before cloning a box into an image, remove its own data: `config/*.local.toml`,
the logs, and any backup files in the home folder.
