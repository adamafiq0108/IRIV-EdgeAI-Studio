# Setup - hub, masters and slaves

How to stand up the hub, a master per system, and pair Raspberry Pi 5 slaves to them.

Read [contract-v1.md](contract-v1.md) first if you are writing the adapter; this document only
installs and wires what already exists.

## The shape

**Each system runs its own master.** Handrail detection and hand-zone detection is two masters,
not one: separate processes, separate ports, separate slave registries, separate pages. A
**hub** runs in front of them and draws one card per system.

The hub **links**; it does not proxy. It asks each master `/api/health` and renders a card that
points at that master's URL. Consequences worth knowing before you deploy:

- A master going down greys its own card. The hub and the other masters do not notice.
- Clicking a card leaves the hub's origin. Each master is its own site.
- There is no shared session between them yet. The mockup's single master password is one login
  per master here, not one login for all of them.

## What works today, and what doesn't

Be clear on this before you start, or step 5 will look broken:

- The **agent** is complete. It serves Contract v1 and `contract_check.py` passes against it.
- The **hub** discovers system packages, probes each master, and renders cards.
- The **master** loads its system, serves Main and the nav, and answers `/api/health`.
- The **sync worker** polls every configured slave once per `[sync].interval_s`, and **address
  discovery** re-finds a device that moved (§5.1).
- There is **no enrollment discovery**. A device becomes known to its master by an operator
  pairing it and recording it in `slaves.toml` - discovery finds a device already listed there,
  it never adds one.

So pairing is done with `curl` (§5) and verified with `contract_check.py` (§6), not from the
dashboard.

---

## 1. Prerequisites

Everything runs FastAPI on uvicorn - hub, master and agent alike. `requirements.txt` is the whole
stack: `fastapi`, `uvicorn`, and `jinja2` (hub and master only; the agent renders no templates).

Every machine:

- Raspberry Pi OS Bookworm (64-bit) or any Linux with **Python 3.11+**. 3.11 is the floor twice
  over: `core/config.py` uses standard-library `tomllib`, and the routes use `str | None`
  annotations that FastAPI resolves at import.
- `python3-venv`, `git`, `curl`.

```bash
sudo apt update && sudo apt install -y python3-venv git curl
```

A master does not need to be a Pi. It needs to reach its own devices on TCP 5101; the devices
never need to reach it.

| Process | Port | Serves |
|---|---|---|
| hub | 5100 (the software port, changeable) | the system cards, one per system |
| master (one per system) | 5110, 5111, … | that system's dashboard |
| agent (one per device) | 5101 | Contract v1 |

The hub and the masters can share one machine (different ports) or sit on separate ones. Nothing
in the product cares - the hub only knows the URLs in `config/systems.toml`.

**The software port.** 5100 is the default, not a constant. Master (or a developer) changes it
under Settings -> Network -> Software port: Test says whether a port is free, Apply saves it and
shows the new address, and the software restarts on request (or at the next boot). The setting is
`[hub].port` in `config/systems.local.toml`; the hub binds it at start, the masters' way back to
the hub follows it, and the front panel prints `<box name>.local:<port>`. The systems keep their
own ports. For the IT admin: whatever port is chosen is the one to allow from the operators' network
to the box, alongside the systems' ports (sec 8).

**The box name.** Each box answers on the LAN as `<name>.local` (mDNS, avahi). A new install names
the box `IRIV-EdgeAI-Studio` (or `IRIV_DEFAULT_HOSTNAME` from `.env`, see
[configuration.md](configuration.md)). On a network with more than one, the second to start answers
as `IRIV-EdgeAI-Studio-2.local`, the third `-3.local`, in whatever order they boot. The front panel
prints the name the box actually answers to. Master (or a developer) renames a box
under Settings -> Network -> Box name; addresses do not change, but a browser that opened the page
as `<old name>.local` is moved to the new name, and bookmarks using the old name stop working.
Renaming needs the installer's hostname helper on the box; an over-the-air update does not rename
anything and does not add the helper. For the IT admin: `.local` names need mDNS (UDP 5353) between
the operators' machines and the box (sec 8); if the site's DHCP server registers client names in
DNS, the box registers its new name at its next lease renewal.

---

## 2. Device identity

Every device is keyed by its CPU serial. Check it before anything else - if this command prints
nothing, the agent will refuse to start, and it is right to.

```bash
cat /sys/firmware/devicetree/base/serial-number
```

Your shell shows the raw devicetree string, which is **null-terminated**. The trailing `\x00` is
invisible in a terminal and compares unequal to the same serial read anywhere else. To see what
the agent will actually use - stripped and lowercased:

```bash
tr -d '\0' < /sys/firmware/devicetree/base/serial-number | tr 'A-Z' 'a-z'; echo
```

That is the `host_serial`. On a Pi 5 it is 16 hex characters. `agent/identity.py` does this strip
for you; falls back to `/proc/cpuinfo` `Serial:` on non-devicetree hosts.

Never paste this value into config. The agent reads it at runtime; that is the point.

---

## 3. Hub and masters

Both come from the same install. The hub and every master run the same code from the same
directory; what differs is the process arguments.

```bash
git clone <this-repo> /opt/cytron-dashboard
cd /opt/cytron-dashboard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Deployment values go in gitignored `*.local.toml` files - never in the committed TOML:

```bash
cat > config/storage.local.toml <<'EOF'
[postgres]
password = "<real-password>"
EOF
chmod 600 config/storage.local.toml
```

Local keys win per key and sub-tables merge field by field, so the block above overrides the
password and leaves `host`, `port`, `dbname` and `user` alone.

### Config

`config/systems.toml` says where the packages are, where the hub answers, and where each
system's master answers. Real hosts belong in `systems.local.toml`:

```toml
dir = "/opt/my-deployment/systems"

[hub]
url = "http://192.0.2.5:5100"

[masters.conveyor]
url = "http://192.0.2.5:5110"

[masters.packing]
url = "http://192.0.2.5:5111"
```

- `dir` - a folder per system, each with a `system.py` defining `SPEC` (see the
  `new-system-package` skill). `enabled = [...]` is optional; omit it to take everything found.
- `[hub].url` - the masters' "‹ Systems" back link. Omit it and they render no back link.
- `[masters.<slug>].url` - where that system's master answers. **A system with no block still gets
  a card**; it just says *"no master configured"* and does not link. That is deliberate: a system
  you forgot to wire and a system whose master is down are different problems, and the operator
  has to be able to tell them apart.

### Run the hub

```bash
.venv/bin/python hub_server.py --port 5100

# development, with reload:
.venv/bin/uvicorn hub_server:create_app --factory --port 5100 --reload
```

`--factory` rather than a module-level `app`: discovery imports every system package, so a bad
package would otherwise explode on `import hub_server`.

`[HUB] N system(s) discovered, M with a master configured` means the registry found your packages.
A system that fails to load raises on boot rather than vanishing from the hub - a dashboard that
looks fine but is quietly missing a machine is the worst failure mode here.

### Run one master per system

```bash
.venv/bin/python master_server.py --system conveyor  --port 5110
.venv/bin/python master_server.py --system packing --port 5111
```

The slug must match the package folder name and the `[masters.<slug>]` key. A wrong slug fails
loudly at boot with the list of what it did find.

Open `http://<hub>:5100` - one card per system, each linking to its master.

### As services

One unit for the hub, one **per system**. The masters differ only in `--system` and `--port`, so
a systemd template unit keeps it to one file:

```ini
# /etc/systemd/system/cytron-hub.service
[Unit]
Description=Cytron Dashboard hub
After=network-online.target

[Service]
Type=simple
User=cytron
WorkingDirectory=/opt/cytron-dashboard
ExecStart=/opt/cytron-dashboard/.venv/bin/python hub_server.py --port 5100
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/cytron-master@.service
# Instance name is the system slug: cytron-master@conveyor
[Unit]
Description=Cytron Dashboard master (%i)
After=network-online.target

[Service]
Type=simple
User=cytron
WorkingDirectory=/opt/cytron-dashboard
EnvironmentFile=/etc/cytron/%i.env      # sets PORT=5110
ExecStart=/opt/cytron-dashboard/.venv/bin/python master_server.py --system %i --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
echo 'PORT=5110' | sudo tee /etc/cytron/conveyor.env
echo 'PORT=5111' | sudo tee /etc/cytron/packing.env
sudo systemctl enable --now cytron-hub cytron-master@conveyor cytron-master@packing
```

The hub does not need to start before the masters, or after them. It probes on every page load,
so it picks up whatever is running at the time.

---

## 4. Slave (Raspberry Pi 5)

The agent is its **own process**, not part of the detection app. That separation is load-bearing:
detection apps get killed by updates, operators and crashes, and the agent has to stay up through
it so the master can report "detection stopped" instead of the device just disappearing.

```bash
git clone <this-repo> /opt/cytron-agent
cd /opt/cytron-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

State directory for the pairing file (holds a bearer token - keep it off world-readable paths):

```bash
sudo mkdir -p /var/lib/cytron-agent
sudo chown cytron:cytron /var/lib/cytron-agent
sudo chmod 700 /var/lib/cytron-agent
```

The agent needs an adapter - a read-only window onto whatever your detection app already records.
Write one with the `new-slave-adapter` skill, or start against the mock (§7). Launch:

```bash
.venv/bin/python -m agent.agent \
  --adapter deployment.adapters.conveyor:ConveyorAdapter \
  --port 5101 \
  --state /var/lib/cytron-agent/pairing.json \
  --unlock
```

`--unlock` opens the pairing window for 300 seconds at startup. Use it on **first install only**
(§5). The startup banner prints the host serial, capabilities and pairing state - check the serial
matches §2.

### Agent as a service

Drop `--unlock` from the unit. A device that reopens its pairing window on every reboot can be
seized by any host on the LAN.

```ini
# /etc/systemd/system/cytron-agent.service
[Unit]
Description=Cytron system agent (Contract v1)
After=network-online.target

[Service]
Type=simple
User=cytron
WorkingDirectory=/opt/cytron-agent
Environment=CYTRON_AGENT_STATE=/var/lib/cytron-agent/pairing.json
ExecStart=/opt/cytron-agent/.venv/bin/python -m agent.agent \
  --adapter deployment.adapters.conveyor:ConveyorAdapter --port 5101
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now cytron-agent
```

The agent restarting does not lose pairing - the token is on disk. It also must not lose cursor
position: your adapter derives cursors from durable storage, never from a counter in memory.

---

## 5. Pairing

The agent accepts a pair request **only** while unpaired or inside an operator-opened unlock
window. Otherwise any host on the LAN could seize a deployed device and take its video and event
history.

Generate a token per device on the master, and treat it as the credential it is:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Check the device is reachable and unpaired first:

```bash
curl -s http://<pi-ip>:5101/api/health
curl -s http://<pi-ip>:5101/api/agent/info
```

`/api/health` is unauthenticated on purpose: the master must be able to tell "device down" from
"token wrong". If `paired` is `false` in `/api/agent/info`, you can pair. If it is `true`, an
operator must reopen the window on the device - see below.

Pair:

```bash
curl -s -X POST http://<pi-ip>:5101/api/agent/pair \
  -H 'Content-Type: application/json' \
  -d '{"master_serial":"<master-serial>","token":"<token>"}'
```

`{"ok": true, "host_serial": "..."}` means paired, and that serial is the device's identity from
now on. A `409` means already paired with no window open.

`master_serial` identifies a master; it authenticates nothing (it is meant to be broadcast over
mDNS). The token is what authenticates. Store the `{host_serial: token}` mapping on the master -
the sync worker will need it, and the device will not give it back.

**Pair a device to its own system's master only.** A conveyor Pi pairs to the conveyor master; a
hand-zone Pi pairs to the hand-zone master. Nothing enforces this yet - the agent accepts the
first pair request it gets in the window, whoever sends it - so a device paired to the wrong
master would report events into the wrong system's dashboard. Check `system_slug` in
`/api/agent/info` matches the master you are pairing it to.

## 5.1 Addresses, and how the master keeps up with them

Record the device in `config/slaves.toml`, keyed by the `host_serial` the pair call returned, and
its token in the gitignored `slaves.local.toml`:

```toml
# config/slaves.toml
[slaves.10000000abcd1234]
system_slug = "conveyor"
label = "Line 3 stairwell"
url = "http://192.0.2.20:5101"     # optional; discovery fills it in
```

```toml
# config/slaves.local.toml - chmod 600
[slaves.10000000abcd1234]
token = "<token issued at pairing>"
```

`system_slug` is what makes one file serve every master: each loads only the entries matching its
own `--system`. It must match `system_slug` in that device's `/api/agent/info`, which comes from
the adapter the agent was started with - that choice, in the systemd unit, is what makes a Pi a
conveyor device.

**The url is a cache, not an identity.** A DHCP lease change moves the device and a master reading
a stale url reports a healthy machine as down. So each cycle, before polling, the master sweeps
its subnet for any slave it has no address for or could not reach last cycle, asks each agent that
answers `/api/agent/info` (unauthenticated by contract), keeps those whose `system_slug` is its own,
and matches `host_serial` against the entries above. A hit rewrites the url and persists it to
`slaves.local.toml`, so a restart polls straight away instead of sweeping again.

Slug answers *"is this device even my system?"*. Serial answers *"is this the device I already
know?"*. Both, in that order - neither substitutes for the other.

A reachable device is never swept for, and an unlisted serial is never adopted no matter what it
reports. Configure it in `[discovery]`:

```toml
[discovery]
enabled = true
port = 5101
subnets = []        # empty = the /24 of every interface this master is on
timeout_s = 0.4
mdns = true         # also probe what mDNS (and, link-local, the ARP table) suggests
```

**Two boxes on one cable (link-local).** A wired port that hears no DHCP server falls back to a
169.254.x.y address. Each box picks its own at random across 169.254.0.0/16, so no /24 sweep finds
the other one (a full /16 sweep is about seven minutes). So every hub advertises itself over mDNS
as `_irivai._tcp` (port = the hub's port; TXT `version`, `role`, `serial`, `hub_port`,
`agent_port`), and every sweep - this one and the Settings page's Scan - also probes the addresses
`avahi-browse` reports, plus, when the box itself is link-local, the 169.254 peers already in its
ARP table. Those addresses are checked exactly like swept ones (`system_slug`, then `host_serial`):
an advertisement is a hint, never an identity. Needs `avahi-daemon` and `avahi-utils`; without
`avahi-utils` the sweep runs alone. `[mdns] advertise = false` in `config/network.local.toml`
stops this box advertising.

To check by hand on a box: `avahi-browse -rpt _irivai._tcp` lists every box that advertises, with
its address and serial.

Set `subnets` explicitly when the master is multi-homed (it would otherwise sweep whichever side
holds the default route) or when the devices are on a different subnet than the master.

**Before you rely on this, reserve the addresses.** A DHCP reservation per Pi, pinned by MAC on the
router, costs nothing and means the url never goes stale in the first place. Discovery is the
recovery when that isn't in place or doesn't hold; it is not a reason to skip it.

**The trust it assumes:** a sweep takes an address from whoever answers. Serials are not secrets -
`/api/agent/info` hands them out unauthenticated, by design - so a rogue agent on this LAN claiming
a known `host_serial` could attract that device's token. That is the same LAN trust §8 already
requires, but it is more surface than a static url. `enabled = false` keeps addresses
operator-only.

### Re-pairing a deployed device

Requires physical/SSH access, by design:

```bash
sudo systemctl stop cytron-agent
sudo -u cytron /opt/cytron-agent/.venv/bin/python -m agent.agent \
  --adapter <module>:<Class> --state /var/lib/cytron-agent/pairing.json --unlock
# pair within 300s, then Ctrl-C and:
sudo systemctl start cytron-agent
```

---

## 6. Verify

The harness is the arbiter, not the docs. An install is done when this passes **unedited**:

```bash
cd /opt/cytron-dashboard
.venv/bin/python tools/contract_check.py http://<pi-ip>:5101 --token <token>
```

Exit 0 and `CONFORMING - N/N checks passed.` Without `--token` it checks only the unauthenticated
endpoints.

Common failures on a fresh Pi:

| Symptom | Cause |
|---|---|
| `clock skew under 60s` fails | Pi 5 has no RTC. A boot without NTP timestamps events in 1970 and would poison the digest. `timedatectl status` - fix NTP before anything else. |
| `host_serial has no trailing NUL` fails | Devicetree string not stripped (§2). |
| `since=<cursor> is exclusive` fails | Adapter returns the cursor's own event again. Master would double-count forever. |
| `cursor tests runnable` skips | Device has zero events. Seed real data first - cursors cannot be verified without it. |
| `garbage cursor -> 4xx` fails | Adapter raises something other than `ValueError` on a bad cursor. Master's health logic keys off status codes. |

---

## 7. No hardware

Everything above runs with no Pi and no camera:

```bash
python tools/mock_slave.py --port 5101 --token mock-token
python tools/contract_check.py http://127.0.0.1:5101 --token mock-token
python tools/mock_slave.py --skew 3600     # rehearse the RTC failure above
```

`mock_slave.py` is also the reference implementation - when this document or the contract is
ambiguous, read it.

The hub and a master run on a laptop just as well. A fresh clone ships a `demo` package and a
loopback `[masters.demo]` block, so this works with no config at all:

```bash
python master_server.py --system demo --port 5110
python hub_server.py --port 5100          # card links to the master above
```

Stop the master and reload the hub: the card stays, and says *"master unreachable"*. That is the
whole hub/master contract, visible in two commands.

---

## 8. Firewall

```bash
# on each Pi: only its own system's master needs the agent
sudo ufw allow from <master-ip> to any port 5101 proto tcp
```

The agent serves video and event history to anything holding the token. Do not expose 5101 to a
network wider than its master.

The hub needs to reach every master's port (5110, 5111, …) to probe `/api/health`, and operators
need to reach the hub **and** every master directly - cards link out, so a master that only the
hub can reach gives the operator a card they cannot click through.

**mDNS (UDP 5353, multicast 224.0.0.251)** must pass between boxes on the same network for
`<hostname>.local` names and box-to-box discovery to work. It is link-scoped and never crosses a
router; a firewall that drops it leaves the plain /24 sweep and IP addresses working. Note that
a `ufw` rule pinned to a master's address (above) does not match on a direct cable, where the
master's address is a random 169.254.x.y: mDNS will find the device and the sweep will then be
refused at 5101. That is the rule doing its job - do not widen it to the whole 169.254.0.0/16
without a security review; give the cable fixed addresses on both ends instead.

The hub's port is 5100 unless the Software port setting says otherwise (sec 1). A firewall rule
written for 5100 has to follow a change there; the Network page shows the port in force.

**Reverse-proxy mode ("Use proxy (port 80)" on the Software port card).** Operators then
open the box as `http://<box>/` (the hub) and `http://<box>/s/<system>/` (each system),
with no port in any address. What a site's IT admin needs to know:

| Traffic | Port | Needed in proxy mode |
|---|---|---|
| Dashboard, sign-in, every system's pages, API, MJPEG video, downloads | TCP 80 | Yes - the one port browsers use |
| Live video over WebRTC (the media itself, ICE) | UDP 8189 | Yes, for WebRTC; without it the page falls back to MJPEG over port 80 |
| The hub's own port and each system's port (5100, 5110, ...) | TCP | No for browsers. They still answer (old bookmarks are redirected to the port 80 address), so a firewall MAY close them to the LAN |
| Device agent | TCP 5101 | Unchanged: only from the master that owns it |
| mDNS | UDP 5353 | Unchanged; the box advertises port 80 and `access=proxy` |

Port 80 is an nftables redirect to the hub's own port (table `inet irivai_web`), written by
the root helper `/usr/local/lib/irivai/web-redirect.py`; the software itself never runs as
root. A box installed before this release needs the one-time `sudo bash
scripts/upgrade_steps.sh` before the choice can be applied (the card's Test says so).
Another web server already on port 80 makes the box refuse the choice rather than steal it.
HTTPS is not terminated by the box: put a TLS reverse proxy (or the remote-access tunnel) in
front and forward to port 80; `X-Forwarded-Proto: https` from it is honoured in the links.

Two things for the firewall plan. **The old ports stay open** in proxy mode: the hub's port
and every system's port (5100, 5110, ...) still listen on all interfaces, so switching to the
proxy does not by itself shrink what the box exposes on the LAN; close them at the firewall if
the site wants only port 80. And **port 80 travels further** than 5100 does: it is the port
most firewalls, guest networks and router port-forwards already let through, so a box in
proxy mode is more likely to be reachable from places it was not before - check any
port-forward on the site router before choosing it. Only traffic addressed to the box itself
is redirected; a hotspot client opening another site on port 80 is passed on untouched.
A remote-access tunnel started from a system still targets that system's own port, never 80.
