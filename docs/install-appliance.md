# Installing onto another IRIV box

How to turn a second IRIV Edge AI board (CM5 + Hailo) into a working appliance,
and how to move a system you have already built onto it.

This is the **appliance** install: `scripts/install.sh`. It copies the product
without the owner's tooling (`core/dev/`, `scripts/dev/`, the frontend source)
and builds the frontend in customer mode, so the developer panels are absent
from the bundle rather than hidden in it. For a developer workstation use
`scripts/setup_pi.sh` on a full checkout instead - there is no flag here that
turns the tooling back on.

Living document. It is updated as the installer changes; the installer is the
authority, and `sudo ./scripts/install.sh --help` always prints the current
flags.

---

## 0. What you need

| | |
|---|---|
| Board | IRIV Edge AI (Raspberry Pi CM5) with a Hailo accelerator (8, 8L or 10H) |
| OS | Raspberry Pi OS 64-bit, **Python 3.11+** (`tomllib` and `str \| None` annotations are both hard floors) |
| Storage | An NVMe drive, mounted. The eMMC wears out under the event database and snapshot traffic |
| Network | LAN, and internet for the install (apt, npm, the model zoo) |
| Access | `sudo` on the box, and SSH from your machine |

Have ready:

- the repository (this checkout), on a USB stick or reachable by `git clone`
- the **serial** of the new box if you will install pro templates - a license key is issued per box
- an OTA update token, if this box should receive over-the-air updates

---

## 1. Prepare the box

### 1.1 Base packages

```bash
sudo apt update
sudo apt install -y python3-venv git curl rsync
```

`install.sh` installs Node.js itself if it is missing, so you do not need it in
advance.

### 1.2 Hailo runtime

Install Raspberry Pi's Hailo stack - this brings the kernel driver, `hailortcli`
and the Python bindings:

```bash
sudo apt install -y hailo-all
sudo reboot
```

After the reboot, confirm the accelerator answers:

```bash
ls /dev/hailo*
hailortcli fw-control identify      # prints the device architecture
```

**`install.sh` builds a plain virtualenv, so the apt-installed bindings are not
importable from it.** Pre-create the venv with `--system-site-packages` before
running the installer - it only builds one when none is there, so it reuses
this:

```bash
VER=$(cat ~/irivai-src/VERSION)
sudo install -d -m 755 /opt/irivai-releases/v${VER}
sudo python3 -m venv --system-site-packages /opt/irivai-releases/v${VER}/.venv
sudo /opt/irivai-releases/v${VER}/.venv/bin/python -c "import hailo_platform; print(hailo_platform.__version__)"
sudo /opt/irivai-releases/v${VER}/.venv/bin/python -c "import hailo; print('TAPPAS ok')"
```

Both imports must succeed: `hailo_platform` is what `app/hailo.py` uses, and
the TAPPAS `hailo` module is what `app/gst_detector.py` imports.

> **Do not reach for `wheels/hailort-5.3.0-cp313-…whl` without checking.** That
> wheel is for boxes whose HailoRT really is 5.3. A current Raspberry Pi OS
> image ships **HailoRT 4.23.0** with a matching `hailort-pcie-driver`, and a
> 5.3 wheel on a 4.23 driver is a version mismatch, not an upgrade. Read
> `hailortcli fw-control identify` first and let the system packages win.

`install.sh` also gives the service account a home directory (HailoRT writes a
cache and a log there and complains on every device open without one) and adds
it to the hardware groups. Both matter, and both used to be missing:

```bash
sudo install -d -m 755 -o cytron -g cytron /home/cytron
for g in video render gpio i2c spi dialout plugdev; do sudo usermod -aG $g cytron; done
```

> A `--system` account starts in **no** supplementary groups, and this failure
> is quiet in the worst way. `/dev/video*` is `root:video`, so every open is
> EACCES - and `list_cameras()` keeps a node it cannot query rather than hiding
> it (an unanswerable node might be the camera currently streaming). The result
> is a camera picker offering **all 19** `/dev/video*` nodes on a CM5 while no
> real camera can be opened at all. `id cytron` should list `video`, `gpio`,
> `i2c`, `spi`, `dialout`, `render`.

The home directory is safe for the store: an existing, marked store root beats
a preferred empty one, so giving the account a home does not re-home the store
(§1.3).

Detection will not start if any of this is missing, and the failure reads as
"detector error" on System Control rather than as a missing package.

### 1.3 NVMe: database and snapshots

```bash
lsblk                                # find the NVMe, mount it (e.g. /mnt/nvme)
sudo ./scripts/setup_nvme.sh /mnt/nvme
```

It moves the PostgreSQL data directory onto the NVMe (cluster stopped, old copy
**kept** as a backup) and creates `/mnt/nvme/cytron` for app data. It is safe to
re-run; it skips anything already there.

The product's store root - systems, workflows, layouts, snapshots, clips - also
belongs on the NVMe. `/mnt/nvme/cytron/store` is already the **first** candidate
`core/config.py store_dir()` tries, so nothing has to be configured. What does
have to be true is that the **service account can write there**: a fresh mount
is `root:root`, the probe fails, and the store silently falls through to the
home candidate on the eMMC - the exact wear the drive was fitted to avoid.

```bash
sudo install -d -m 755 -o cytron -g cytron /mnt/nvme/cytron
sudo install -d -m 755 -o cytron -g cytron /mnt/nvme/cytron/store
```

(The `cytron` account is created by `install.sh`, but `useradd --system
--no-create-home --shell /usr/sbin/nologin cytron` first is harmless and lets
you do this before installing.)

> Do **not** rely on `CYTRON_STORE` in `/etc/environment`: systemd services do
> not read that file - it is applied by PAM to login sessions. To pin the root
> for the units it has to be `Environment=` in the unit, or
> `DefaultEnvironment=` in `/etc/systemd/system.conf`. On a normal box you do
> not need it at all; the candidate order already prefers the NVMe.

> If more than one store root ends up with content, the hub prints
> `[STORE] WARNING: more than one store root has content: …` at boot and uses
> the first. They are **not** merged. Pick one and consolidate deliberately.

### 1.4 PostgreSQL

The masters keep events, datasets, folders and the audit log in Postgres. The
schema is applied automatically at master boot
(`core/db/migrations`, forward-only, recorded in `schema_migrations`) - you only
have to create the role and the database:

```bash
sudo apt install -y postgresql
sudo -u postgres createuser cytron --pwprompt
sudo -u postgres createdb cytron_dashboard -O cytron
```

Then record the password where the product reads it -
`config/storage.local.toml`, which is never committed and never copied by the
installer:

```toml
[postgres]
password = "the-password-you-just-set"
```

Defaults for host, port, database name and user are in
[`config/storage.toml`](../config/storage.toml); override any of them in the
same `.local.toml` if your cluster differs.

A master **degrades gracefully** when the DB is down: detection keeps running
and events keep being written to the app's own log, but Data Tables, Analytics
and the audit trail stay empty. So an empty Analytics page on a fresh box is
usually this step, not a bug.

### 1.5 Video gateway (WebRTC live view)

```bash
sudo ./scripts/mediamtx/install.sh
```

Idempotent, and nothing in it is per board. The addresses a browser is
offered for the live view come from `webrtcIPsFromInterfaces` - every address
the box has, read at the start of each session - so a DHCP move or a fixed
address set on the Network page needs nothing re-run. (Installs before
2026-10-04 stamped the install-day address into `webrtcAdditionalHosts`; a
re-run of this script clears it.)

---

## 2. Get the code onto the box

`install.sh` builds the frontend, so it needs a full checkout - the frontend
source, which is exactly what it then declines to copy.

```bash
git clone <your-remote> ~/irivai-systor-src
cd ~/irivai-systor-src
git checkout cytron_iriv
```

Off a USB stick instead:

```bash
rsync -a --exclude .venv --exclude node_modules /media/usb/irivai-systor/ ~/irivai-systor-src/
```

---

## 3. Fetch the detection models

HEFs are not committed - they are large, and a HEF compiled for `hailo8` will
not load on `hailo10h`. Fetch the build for the accelerator actually plugged in:

```bash
cd ~/irivai-systor-src
python3 systems/demo/scripts/fetch_models.py --list
python3 systems/demo/scripts/fetch_models.py yolov8n
```

Architecture is auto-detected; pass `--arch hailo8l` to pre-stage models on a
laptop and copy the folder over.

Note that `--all` fetches only the **detection** models. Pose, segmentation and
depth stay `[ ]` after it and have to be named one at a time.

**Then move them to the built-in model home.** `install.sh` does not copy
`models/` into the install, and every system shares one directory so a HEF is
fetched once rather than per system. There are **two** model directories and
they mean different things:

| Directory | Holds | Shown as |
|---|---|---|
| `/opt/irivai-models/<arch>/` | catalogue models the product downloads | **Default Model** |
| `/mnt/nvme/model/` | models an operator copies onto the box by hand | **Custom Models** |

`/mnt/nvme/model` is the operator's drop folder and anything in it is listed as
a custom model, so a catalogue model put there appears twice - once where it
belongs and again as an unlabelled drop-in with no manifest. Put fetched models
in the built-in home:

```bash
sudo install -d -m 755 -o cytron -g cytron /opt/irivai-models/hailo8
sudo install -m 644 -o cytron -g cytron \
     ~/irivai-src/systems/demo/models/hailo8/*.hef /opt/irivai-models/hailo8/
```

`/opt/irivai-models` sits beside the releases, never inside one: an update
replaces the release directory and prunes old ones, so a model kept in there
would be downloaded again after every update.

> **`coco.txt` currently fails with HTTP 404.** The fetcher pulls it from
> `raw.githubusercontent.com/hailo-ai/hailo_model_zoo/master/…/coco_eighty.txt`,
> which has moved. Copy the file from a box that already has one
> (`<store>/models/coco.txt`) into `/opt/irivai-models/coco.txt` **and**
> `/opt/irivai-models/<arch>/coco.txt`. It is 80 lines, `person` first. Without
> it every detection is an integer and no zone rule can filter by "person".

---

## 4. Run the installer

```bash
cd ~/irivai-src
sudo bash scripts/install.sh
```

`bash scripts/…`, not `./scripts/…`: a tree delivered by `git archive` or a
plain `rsync` may arrive without the execute bit, and `./scripts/install.sh`
then fails with the unhelpful `command not found`. The same applies to
`scripts/mediamtx/install.sh` (`sudo sh scripts/mediamtx/install.sh`).

That is the whole install. It:

1. checks Python ≥ 3.11 and reads `config/builtin.toml` for what ships (`demo`)
2. creates the `cytron` service account
3. builds the frontend with `npm run build:customer`, then **refuses to continue**
   if a developer chunk is still in the bundle
4. copies the product into `/opt/irivai-releases/v<VERSION>/` and points
   `/opt/irivai-systor` at it as a symlink - the release layout that makes
   over-the-air updates atomic
5. builds the virtualenv and installs `requirements.txt`
6. writes `config/deploy.toml` as `mode = "customer"`, so the developer routes
   stay unmounted even if `core/dev/` ever reappears on the box
7. writes the systemd units, the sudoers drop-ins, and the boot-time recovery
   service
8. starts everything and waits for the hub to answer

Useful flags:

| Flag | Why |
|---|---|
| `--update-token <t>` | store a read-only release token so this box can fetch OTA updates. Can be pasted later in Administration → Platform Update |
| `--config-json <p>` | read `{"update_token": "…"}` from a provisioning file instead. Defaults to `config.json` beside the script; never copied into the install |
| `--hub-port` / `--master-port` | defaults 5100, and 5110 upward (one per built-in system). The hub port is written as a setting the box keeps (`config/systems.local.toml [hub].port`), so Settings -> Network -> Software port can change it later without re-installing; a box that already has one keeps it |
| `--user <name>` | service account, default `cytron` |
| `--target <dir>` | install location, default `/opt/irivai-systor` |
| `--adapter <module:Class>` | also install the device agent (port 5101) |
| `--no-service` | build and copy but write no units and start nothing - a rehearsal |
| `--no-ota` | plain directory instead of the release layout. Only for a box you will update by hand |

Re-running is safe: every step checks before it writes.

### What ends up where

```
/opt/irivai-releases/v1.0.0/     this release, complete, with its own venv
/opt/irivai-systor -> …/v1.0.0   what every unit and every path uses
/etc/irivai/<slug>.env           each master's port
/etc/irivai/update.conf          where the boot-time repair script looks
/usr/local/lib/irivai/recover.py the repair script, outside every release
/mnt/nvme/cytron/store/          systems, workflows, layouts, snapshots, clips
```

Units: `irivai-hub`, `irivai-master@<slug>`, `irivai-recover`, `irivai-apply`,
and `irivai-agent` if you passed an adapter.

---

## 5. First login

The installer sets no password - an appliance that ships with a documented one
has no authentication worth the name. Set the administrator's password once, on
the box:

```bash
sudo tee /opt/irivai-systor/config/auth.local.toml >/dev/null <<'TOML'
[master]
password = "choose-something-long"
TOML
sudo chown cytron:cytron /opt/irivai-systor/config/auth.local.toml
sudo chmod 600 /opt/irivai-systor/config/auth.local.toml
```

Then open `http://<box-ip>:5100` (or the software port the box was given) and sign in as
**admin** with that password.
The first correct login rewrites the file as a PBKDF2 hash, so the cleartext
leaves the disk without you running anything. Because no recovery email is on
file yet, the SPA routes you straight to **Account Setup** - give the account
its real name, its own password and a recovery address there.

### The box's own access point

Out of the box, the Wi-Fi radio broadcasts the box's own access point, so a
laptop or phone can reach the dashboard with no site network at all:

| | |
|---|---|
| Network name | `iriv-<serial>` (shown on the front panel's Wi-Fi page) |
| Password | `iriv1234` (or `IRIV_AP_PASSWORD` from `.env`) |
| Dashboard address on the hotspot | `http://192.168.1.1` (or `IRIV_AP_ADDRESS`; see [configuration.md](configuration.md)) |

The default access point password is iriv1234 on every box, and it is printed
in this manual, so anyone in Wi-Fi range can join it and reach the sign-in
page. The dashboard password above still protects the box, and the access point
cannot reach the wired LAN behind it. Change the password on the Network page
(Settings -> Network -> Access-point mode -> Save) if the site wants the access
point itself closed. **Reset Wi-Fi** on the same page brings back the
`iriv-<serial>` name and the default password iriv1234. Updating the software
never changes a password that was set.

---

## 6. Verify

```bash
curl -s http://127.0.0.1:5100/api/health          # {"ok":true,"role":"hub","version":"1.0.0",…}
curl -s http://127.0.0.1:5110/api/health          # the first master
systemctl status irivai-hub
journalctl -u irivai-master@demo -n 30 --no-pager
```

Then in the dashboard:

- **Administration → Board I/O** - the terminals are listed and the board is
  detected
- **System Control** - start a system; FPS climbs and the Live View pill goes
  green
- **Analytics** - a Data Table fills, which proves Postgres is wired

If the hub answers but detection will not start, the cause is almost always
§1.2 (the runtime is not visible from the venv) or §3 (no HEF for this
architecture). Both say so in the master log.

---

## 7. Moving a system across from your working box

The install gives you a bare appliance with the built-in system. To carry over
something you built - its workflow, zones, cameras, dashboards:

**The supported way** is Template Store. Publish the system as a template from
the box that has it, then install that template on the new box. It travels as a
signed bundle, which is the only path that also carries its store extension code
and survives an update.

**Copying by hand** works for a like-for-like move between two boxes on the same
version. Stop the system on both, then copy from the store root:

```bash
STORE=/mnt/nvme/cytron/store
rsync -a  $STORE/systems/<slug>/     newbox:$STORE/systems/<slug>/
rsync -a  $STORE/workflows/<slug>.json newbox:$STORE/workflows/
rsync -a  $STORE/layouts/<slug>.json   newbox:$STORE/layouts/
```

Then on the new box register it in `config/systems.local.toml` (a `[masters.<slug>]`
block with its `url`, `name` and `group`) and start it from System Control.

Two things do **not** travel and must be redone on the new box:

- **camera sources** - a camera is addressed by URL or device index, and neither
  is the same machine to machine. Re-point them in Camera Setup.
- **license keys** - a pro template's key is issued for one box's serial. Ask
  for a key against the new serial.

---

## 8. After the install

- **The OS password is yours to change.** The installer does not change,
  expire or check the `pi` account's password: the box keeps whatever OS
  password it shipped with (the Raspberry Pi default included), and the install
  summary says so in one line ("the pi account password was not changed").
  Change it whenever you want:

  ```bash
  sudo passwd pi
  ```

  The rest of the hardening is unchanged and still decides whether the install
  prints "Done": `pi ALL=(ALL) NOPASSWD: ALL` becomes the same grant **with** a
  password (so `sudo` asks for pi's current password, whatever it is; left as it
  is, and reported, when pi has no usable password at all), Node-RED's
  passwordless grant goes while Node-RED is off, sshd gets `PermitRootLogin no`,
  and `PasswordAuthentication no` once the account that ran the install has an
  `~/.ssh/authorized_keys`. Password SSH that is still on is a problem the
  installer refuses "Done" for, so add a key before installing (`ssh-copy-id`
  from your laptop) or re-run after adding one. `scripts/upgrade_steps.sh
  --harden` does the same on a box already in service and asks for no password.

  Check what sshd actually enforces (defaults included) with
  `sudo sshd -T | grep -iE '^(passwordauthentication|permitrootlogin) '`.
- **Give the box a static address**, or a DHCP reservation. Settings → Network
  does this from the dashboard (`nmcli` under the hood), including AP mode.
- **Set the update token** if you did not pass one: Administration → Platform
  Update.
- **Check retention**: Administration → Data Retention. Defaults are 12 months
  of events, 45 days of snapshots and clips.
- **The software port and the box name** (Settings → Network → Software port,
  Box name; master or a developer). Both are changed from the page with no
  terminal, and neither changes on Save: the card shows the current value and
  the saved one ("Answering on 5100. After restart: 5200"), names the address
  the dashboard will have (`http://<name>.local:<port>` and
  `http://<address>:<port>`), and offers **Restart now** or **Later**. The
  restart is a reboot of the box; one reboot applies both. If the new port is
  taken by then, or the name is refused, the box comes back on the old one and
  the card (and the admins' Inbox) says why.

  What the site's IT needs to know:
  - Nothing on the box listens on the old port after the restart. A firewall
    rule, a router port forward or a reverse proxy that names the old port has
    to be changed to the new one by whoever owns it. Each system's own port
    (5110 and up) and the device agent (5101) do not move.
  - The name is announced with mDNS (UDP 5353, multicast, same network only).
    A computer that cannot use `.local` names reaches the box by its address,
    which a rename never changes. A router that registers DHCP names in its
    own DNS learns the new name when the box next renews its lease.

  The port needs nothing on any box running this release, whenever it was
  installed. The name needs the box-name helper, which boxes installed with
  the 1.0.14 installer or later already have; an older box says "this box needs
  a one-time update by the installer" on the card, and
  `sudo bash /opt/irivai-systor/scripts/upgrade_steps.sh` (steps 5 and 6) adds it.
- **HTTPS (optional, off by default)** - Settings -> Network -> HTTPS (master or a developer).
  Switching it on makes the box's own root certificate and a certificate for the box's names and
  addresses, and the dashboard answers on `https://<name>.local:8443/` (and on
  `https://<name>.local/` once port 443 is available, below) while HTTP keeps working exactly as
  before. Nothing is generated and nothing listens until it is switched on, on a fresh install and
  on every box that updates into this. On each computer or phone that uses the dashboard:
  1. Download the root certificate from the card (`.crt`; `.cer` for phones).
  2. Compare the SHA-256 fingerprint your OS shows with the one on the card, and with the card
     opened on the box's own screen (a page loaded over plain HTTP on the network can be altered
     together with the download). Only then trust it: Windows - its dialog shows only a SHA-1
     thumbprint, so check the `.cer` with `certutil -hashfile iriv-edge-ai-root.cer SHA256` in a
     Command Prompt, then open the file, Install Certificate, Current User (Local Machine only if
     every account on the PC should trust it), Trusted Root Certification Authorities; macOS - open it into the System keychain, then Always Trust; iPhone/iPad -
     install the downloaded profile, then Settings -> General -> About -> Certificate Trust
     Settings -> full trust; Android - Settings -> Security -> Install a certificate -> CA
     certificate; Firefox (any OS) keeps its own list - Settings -> Privacy & Security ->
     Certificates -> Import, "Trust this CA to identify websites".
  3. Until a client trusts the root, its browser shows a security warning on the HTTPS address.

  What the site's IT needs to know: TCP 8443 (and 443) must be allowed from the operators'
  network to the box; the root can vouch only for web servers with local names (`.local`,
  `localhost`, `home.arpa`, `.lan`, `.internal`) and private, link-local, CGNAT and loopback
  addresses - nothing on the internet, no code signing, no e-mail - but that is ANY such name or
  address on the site's LAN, not only this box, so its key (in `config/https.local.toml` on the box)
  must be guarded like a password. The box renews its own certificate (yearly, and when its
  address or name changes) with no client touched; only a **New root** (a confirmed action on the
  card) makes every client trust the box again. "Send browsers to HTTPS" is off by default; turn
  it on only once every client trusts the root. In port mode the systems themselves stay on their
  own HTTP ports; for one HTTPS address for everything use Access -> Use proxy as well.

### Port 443 for HTTPS on a box installed earlier

HTTPS itself needs nothing from root: it answers on 8443. Port 443 is an nftables redirect made by
the same root helper as the port 80 redirect, `/usr/local/lib/irivai/web-redirect.py`, which gained
the `tls_on` / `tls_off` actions in this release. The sudoers rule (`/etc/sudoers.d/irivai-web`) is
unchanged. An over-the-air update cannot refresh a root-owned helper by itself (the update runs as
the service account); it does so only through the root step, so:

- **Box has `/usr/local/lib/irivai/root-steps.py`** (installed with 1.0.27 or later, or after the
  one-time maintenance step below): the update that brings HTTPS installs the new helper itself.
  Nothing to do.
- **Box without it**: run the one-time maintenance step below once (it installs this helper and
  everything else on the list), or, to install only this helper, from the same root-owned,
  signature-checked copy of the release (`tree/` in the steps below, never `/opt/irivai-systor`):

  ```bash
  sudo install -m 755 -o root -g root tree/scripts/web_redirect.py /usr/local/lib/irivai/web-redirect.py
  # only if /etc/sudoers.d/irivai-web does not exist (a box installed before 2026-10-07);
  # <user> is the User= of irivai-hub.service (systemctl show -p User irivai-hub)
  echo '<user> ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/web-redirect.py' \
    | sudo tee /etc/sudoers.d/irivai-web.tmp >/dev/null
  sudo visudo -cf /etc/sudoers.d/irivai-web.tmp && sudo chmod 440 /etc/sudoers.d/irivai-web.tmp \
    && sudo mv /etc/sudoers.d/irivai-web.tmp /etc/sudoers.d/irivai-web
  sudo systemctl restart irivai-hub        # or switch HTTPS off and on again on the card
  ```

  Check: `sudo nft list table inet irivai_web_tls` shows `tcp dport 443 redirect to :8443`, and
  `/etc/irivai/web-redirect-tls.nft` exists. Until then the HTTPS card says why port 443 is not
  used and HTTPS keeps answering on 8443.

### The one-time maintenance step (boxes installed before 1.0.27)

From 1.0.27 every over-the-air update installs the box's root helpers,
sudoers rules and nftables units itself (`scripts/root_steps.py`, from bytes
the release's signed manifest pins). A box installed earlier does not have
that step yet: it gets it, and everything else on the list, from
`scripts/upgrade_steps.sh` run ONCE as root, after the box is on 1.0.27 or
later. After that, never again.

Run it from a root-owned copy whose signature you checked, not from
`/opt/irivai-systor`: that tree belongs to the service account, and a root
script read from it is only as trustworthy as that account. The
offline update file of the release the box runs (`irivai-update-v<version>.tar.gz`,
on the same GitHub release) carries the bundle, the manifest and its signature:

```bash
sudo -i                                    # everything below as root
umask 077; W=$(mktemp -d /root/irivai-onetime.XXXXXX); cd "$W"
tar -xzf /root/irivai-update-v1.0.27.tar.gz manifest.json manifest.sig bundle.tar.gz

# 1. The signature, with openssl and the OTA public key. Take the key from
#    this document or the repository, NOT from the box's config/update.toml
#    (the service account can edit that file).
KEY=TjgmysV+4XU8VRGl7UqzQNaE7azT6dvuiqF7MLIUcLE=
{ echo "-----BEGIN PUBLIC KEY-----"
  { printf '\x30\x2a\x30\x05\x06\x03\x2b\x65\x70\x03\x21\x00'; echo "$KEY" | base64 -d; } | base64
  echo "-----END PUBLIC KEY-----"; } > ota.pem
base64 -d manifest.sig > manifest.sig.bin
openssl pkeyutl -verify -pubin -inkey ota.pem -rawin -in manifest.json -sigfile manifest.sig.bin
#    must print: Signature Verified Successfully

# 2. The bundle is the one the signed manifest names (the two must match).
python3 -c 'import json; print(json.load(open("manifest.json"))["bundle"]["sha256"])'
sha256sum bundle.tar.gz

# 3. The step itself, from that root-owned copy.
mkdir tree && tar -xzf bundle.tar.gz -C tree
bash tree/scripts/upgrade_steps.sh
cd / && rm -rf "$W"
```

A root-owned `git clone` of the signed release tag works the same way (`bash
<clone>/scripts/upgrade_steps.sh`). `upgrade_steps.sh` has no `--bundle`
option on purpose: a script read from the service account's tree cannot vouch
for itself, so the check has to be done with tools root already trusts
(openssl, sha256sum, tar) before any script from the release runs.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Installer stops at "the customer bundle still contains developer panels" | `npm run build:customer` did not take. Check `frontend/vite.config.js` defines `__DEV_TOOLS__` from the build mode |
| Installer stops at "config/builtin.toml names systems that are not in this checkout" | A slug listed in `config/builtin.toml` has no `systems/<slug>/system.py`. Fix the list or add the package |
| Hub answers, every card grey | The masters are not up. `journalctl -u irivai-master@<slug>` |
| Analytics and Data Tables empty | Postgres, §1.4 |
| Live View black, everything else fine | MediaMTX, §1.5 - `systemctl status irivai-mediamtx`; on a box installed before 2026-10-04, re-run the script so `webrtcAdditionalHosts` is no longer an install-day address |
| `[STORE] WARNING: more than one store root has content` | Two store roots. Consolidate; they are not merged |
| Detection will not start, "detector error" | §1.2 or §3 |

Logs are in the dashboard: **Administration → Logs**, and each master serves its
own at `/logs`. Every request is one line with its status code and a request id;
an error carries the same id, so a report of "it failed" is one search.
