# IRIV Edge AI Studio

A template for building a fleet dashboard over edge AI systems.

Each **system** runs its own **master**, which polls that system's edge devices - discovery,
summary rollup, one email digest, proxied live video, proxied footage, per-device management. A
**hub** sits in front and draws a card per system. Every device runs an **agent** that serves a
small HTTP contract over whatever the detection app already records.

A system that implements [the contract](docs/contract-v1.md) gets the whole dashboard with **no
dashboard code written for it**: hub card, Main, Video Stream, Footage, Monitoring, Settings,
Email, Manage. Only analytics widgets are per-system, and those are a declaration, not a page.

FastAPI + uvicorn on the backend (a pure JSON API), React 18 + Vite SPA on the front, PostgreSQL
for the little state that must survive a restart.

## Run it - no hardware needed

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..

python tools/mock_slave.py --port 5101 --token mock-token &   # a fake edge device
python master_server.py --system demo --port 5110 &
python hub_server.py --port 5100
```

Open <http://127.0.0.1:5100>. The card links to the demo master. No Pi, no camera and no
Postgres required - the master degrades cleanly without a database (folders and audit go quiet;
polling, auth and control keep working).

Prove the device half conforms:

```bash
python tools/contract_check.py http://127.0.0.1:5101 --token mock-token
```

Exit 0 is the definition of done for any device that wants onto the dashboard.

## Processes

```
hub_server.py    --port 5100                      one hub, draws a card per system
master_server.py --system demo    --port 5110     one master per system
master_server.py --system packing --port 5111     two systems is two masters
agent                             --port 5101     one per edge device
```

Masters share nothing: own port, own device registry, own pages. The hub links out, never
proxies - a master that goes down greys its own card and touches nothing else.

## Layout

```
core/       the generic dashboard
  routes/     HTTP only - parse, call a service, return JSON
  services/   all behaviour, framework-free
  repos/      the only SQL in the codebase
  db/         connection + forward-only migrations
frontend/   React SPA (Vite) - pages, components, api client, theme.css
agent/      runs on every edge device, port 5101 (adapters, pairing, control)
systems/    system packages - one folder per system; `demo` ships as the reference
config/     brand / storage / auth / systems / slaves / monitoring - TOML, never code
tools/      mock_slave.py (reference device), contract_check.py (the arbiter)
docs/       contract-v1.md, setup.md, conventions.md, master-db.md
scripts/    setup_pi.sh, run_all.sh - install as systemd units
.claude/    skills: task-scoped guides for working in this repo
```

## Product vs deployment

This repo is the **product**. It never knows a customer exists - no customer names, hosts,
subnets, serial schemes or branding. A **deployment** is its own private repo holding system
packages, `*.local.toml` config, secrets and systemd units, and points `[systems].dir` at its own
packages folder.

That split is why a second customer costs config instead of a fork. It is enforced mechanically
- see [`docs/conventions.md`](docs/conventions.md) §1.

## Installing onto an IRIV Edge AI board

The whole job, start to finish, is: get the code onto the board, run one script, check four
things, lock the board down. Two ways to do the first step.

### Step 1a - push it from the bench (preferred for a customer board)

Run this **on your machine**, from a checkout of this repo. The board never sees a GitHub token,
which is the point: a token on an appliance you are handing over is a credential you no longer
control.

```bash
git fetch origin cytron-dashboard
git archive --format=tar origin/cytron-dashboard \
  | ssh pi@<board-ip> 'rm -rf /tmp/iriv-src && mkdir -p /tmp/iriv-src && tar x -C /tmp/iriv-src'
```

### Step 1b - pull it on the board

Needs a token, because the repo is private: make a fine-grained one, read-only, this repo only,
and delete it when the batch of boards is done.

```bash
read -rsp 'GitHub token: ' T && echo
mkdir -p /tmp/iriv-src && curl -fsSL -H "Authorization: Bearer $T" \
  https://api.github.com/repos/adamafiq0108/irivai-systor/tarball/cytron-dashboard \
  | tar xz -C /tmp/iriv-src --strip-components=1
```

While curl runs, that token is visible in `ps` on the board. Fine on your bench, not fine on a
customer's network.

`cytron-dashboard` is the branch a customer board runs. Swap it for `cytron_iriv_dev` to build
yourself a development board instead.

### Step 2 - run the installer

```bash
ssh pi@<board-ip> 'cd /tmp/iriv-src && sudo bash scripts/install.sh'
```

Defaults suit a customer board: installs to `/opt/irivai-systor`, runs as the `cytron` service
account (created if absent), hub on 5100, first master on 5110, blue/green release layout, and a
local PostgreSQL for the rows your Data Tables record.

Two flags for deployments that do it differently:

```bash
--db-host db.example.lan   # several boards sharing one server; set its password
                           # in config/storage.local.toml afterwards
--no-database              # no row store at all: tables can be designed, no rows stored
```

### Step 3 - check it actually worked

Do not trust the closing banner alone. Four things, on the board:

```bash
systemctl is-active irivai-hub irivai-master@demo     # active, active
curl -s -o /dev/null -w '%{http_code}\n' localhost:5100   # 200
ls -l /opt/irivai-systor                              # a SYMLINK into /opt/irivai-releases
ls /opt/irivai-systor/scripts/install.sh 2>&1         # must say No such file
```

The last one is not paranoia: the installer is supposed to keep itself off the board, and a
board that still has it can be used to stand this software up on hardware you did not sell.

### Step 4 - the sign-in it arrives with

The board ships with the platform administrator account already made, so a
customer can open the dashboard and get in without phoning anyone:

| | |
|---|---|
| Username | `iriv` |
| Password | `iriv1234` |
| Role | Master - owns everything on the box |

**This is the same on every board and it is printed here, so treat it as a
starting credential rather than a secret.** The first sign-in lands on setup,
which is where the customer gives the account their own name and password; the
board is only as safe as that step being taken. Change it before a board is
used for anything real.

Locked out later? Hold the **USER button on the front panel for five seconds**.
The screen shows a six-digit code that the Forgot page accepts along with a new
master password -- no email needed, which is the point for a board with no
address on file. The installer switches the panel on for exactly this reason.

Re-running the installer never replaces a password the owner has already
chosen; it only fills in the default when there is none.

**The access point.** A new board also broadcasts its own Wi-Fi access point,
named `iriv-<serial>` (shown on the front panel). The default access point
password is `iriv1234`, the same on every board, and it can be changed on the
Network page (Settings -> Network -> Access-point mode). Anyone in Wi-Fi range
who knows it can reach the sign-in page, which is why the dashboard password
above must be changed; the access point cannot reach the wired LAN. **Reset
Wi-Fi** on the Network page brings back the `iriv-<serial>` name and the
password `iriv1234`. The installer and updates keep a password that was set.

### Step 5 - lock the board down

This is the Raspberry Pi OS login, not the dashboard one above. The installer **reports** on
these and does not fix them, because changing a password out from under whoever is installing is
how you lock yourself out of a board at the far end of an SSH session.

```bash
sudo passwd pi                                        # the factory password is public knowledge
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/99-no-passwords.conf
sudo systemctl restart ssh
```

Install an `authorized_keys` you can log in with **before** the second command, or you will be
locked out.

Takes 10-20 minutes on a cold board: it installs the system packages, builds the frontend with
the developer panels left out, makes the virtualenv, writes the systemd units, installs MediaMTX
for WebRTC, prepares the store, stamps the board's identity from its CM5 serial and its secure
element, and writes the sudoers rules the platform needs. Everything it does is checked before it
writes, so it can be run again.

Add `--config-json config.json` if the board should come up already holding the update token.
That file is never in the repo - keep it on the bench machine and pass it in.

**Two things to have ready before a board ships.** An NVMe, because without one the store falls
back to the system disk and a recorder filling the disk it boots from is how a box bricks itself.
And a provisioned secure element, if the board is to take a hardware-bound licence -- the chip
ships blank and `scripts/dev/provision_atecc.py` gives it a key, once, permanently.

**What it leaves behind, and what it does not.** The board gets the product: the hub, the
masters, the built frontend, the update applier and the recovery service. It does **not** get
`install.sh` or the other scripts that build a board - those stay on your bench checkout. A
board in the field is updated over the air or sent back here; it is never rebuilt where it
stands, and a customer holding the installer is a customer who can stand this software up on
hardware we did not sell them. That is a locked door, not the lock: what actually stops a copy
running is the licence bound to the board's secure element.

**Re-installing a board.** Run the same command again. Local settings (`config/*.local.toml` -
accounts, licence, update token, device identity), each system's recordings and models, and the
store on the NVMe all survive, because the installer never copies over them. One caveat worth
knowing: the install lives in a directory named after `VERSION`, so re-installing a *different*
version makes a new directory and the local settings are not carried into it. Only an
over-the-air update migrates them. Re-install the same version, or update from the dashboard.

## Turning the software off, and on again

A board is a Raspberry Pi with our software on it. The software can be switched off and the
board goes back to being an ordinary Pi -- nothing is uninstalled, no data is touched, and the
dashboard simply stops answering.

**Off**, and staying off across reboots:

```bash
sudo systemctl disable --now irivai-hub.service irivai-master@demo.service irivai-mediamtx.service
```

Name each system's master you have -- `irivai-master@demo` here; a board with a `parking` system
also needs `irivai-master@parking`. After this nothing of ours is running and port 5100 refuses
connections.

**On again:**

```bash
sudo systemctl enable --now irivai-hub.service irivai-master@demo.service irivai-mediamtx.service
```

The hub brings up every system flagged Autostart by itself, so enabling the hub alone is usually
enough; the master unit is listed so it also comes back after a reboot.

`irivai-recover` is deliberately left alone by both commands. It is a boot-time repair that does
nothing at all unless an update was interrupted, and switching off the thing that puts a
half-updated board back together is not part of switching off the software.

## The two Ethernet ports

They are not the same hardware. `eth0` is the Raspberry Pi's own port.
`eth1` is a Realtek RTL8153 on USB, and it arrives from the factory with two
rough edges that the installer files off:

**The jack goes dark on a gigabit link.** The chip drives one LED per speed,
and the two LEDs the jack wires up are the 10M and 100M ones -- so at gigabit
it lights the LED that is not connected to anything, and the port looks dead
while the link is fine. The installer sets every LED to light at any speed.

**Its address is not burnt in.** The adapter's address EEPROM is blank, so the
kernel invents a MAC on every boot, DHCP sees a new machine and hands out a new
lease. A board sitting at `192.168.1.173` turned up at `192.168.1.10` with
nobody touching its configuration -- its cable had been moved from one port to
the other. The installer derives an address from the board serial instead, so
the board keeps one lease.

Both are done by a udev rule, at every boot, so nothing has to be re-run. Check
it on a board with:

```bash
udevadm test --action=add /sys/class/net/eth1 2>&1 | grep usbnet-setup
```

### A board installed before this shipped

An over-the-air update will NOT fix it. The helper and the rule live outside
every release directory, in `/usr/local/lib/irivai` and `/etc/udev/rules.d`,
which is deliberate: the account that applies updates can write a release tree,
so anything it could drop into a root-run helper would be a way to become root.
Those files change only when somebody runs the installer.

So either re-run `install.sh`, or copy the two files across by hand from a
checkout:

```bash
scp scripts/usbnet_setup.py scripts/udev/84-irivai-usbnet.rules pi@<board>:/tmp/
ssh pi@<board> '
  sudo install -m 755 -o root -g root /tmp/usbnet_setup.py \
          /usr/local/lib/irivai/usbnet-setup.py
  sudo install -m 644 /tmp/84-irivai-usbnet.rules \
          /etc/udev/rules.d/84-irivai-usbnet.rules
  sudo udevadm control --reload'
```

The LEDs come right at the next replug or reboot. The address changes at the
next reboot, and only then -- setting it needs the interface down, and bouncing
a live `eth1` would drop the session doing the bouncing. Expect the board to
move address **once** on that reboot, and to stay put afterwards; find it again
at `iriv.local:5100`.

## Adding your own system

1. **Device side** - write an adapter over what your app already records, subclassing
   `agent.adapters.base.Adapter`. Two required methods. Run `tools/contract_check.py` until it
   passes unedited.
2. **Master side** - copy `systems/demo/system.py`, change the slug, declare your analytics
   widgets.
3. **Config** - add the slug to `[systems].enabled` and give it a `[masters.<slug>]` URL.
4. Start that system's master. The card, the nav and the analytics appear. `hub_server.py` and
   `master_server.py` are never edited.

Full walkthrough: [`docs/setup.md`](docs/setup.md).

## Working in this repo with Claude Code

`.claude/skills/` holds task-scoped guides - the folder map, adding a page or an endpoint, the
contract adapter recipe, MQTT, charts, migrations, config, deployment, and recovering code you
deleted. They load on demand: say what you want to do and the matching skill opens.

## Docs

| Doc | What it settles |
|---|---|
| [`docs/contract-v1.md`](docs/contract-v1.md) | the device HTTP contract (v1 read, v1.2 control, v1.3 telemetry) |
| [`docs/conventions.md`](docs/conventions.md) | layering, ceilings, naming, security - the rules the skills enforce |
| [`docs/setup.md`](docs/setup.md) | install, pairing, systemd, firewall, verification |
| [`docs/master-db.md`](docs/master-db.md) | schema and the DB-down degradation contract |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Short version: one concern per PR, respect the layering
and the line ceilings, and `tools/contract_check.py` decides contract questions - not the docs.

## License

Proprietary - see [`LICENSE`](LICENSE). The software is licensed per device, for IRIV Edge AI
hardware supplied by Cytron. Copying it, running it on other hardware, modifying it, or
bypassing its licence and device checks ends the warranty and the support that goes with it.
Open-source components keep their own licences.
