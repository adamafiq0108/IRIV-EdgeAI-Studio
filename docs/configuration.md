# Configuration

Where IRIV EdgeAI Studio keeps its settings, and how to set the ones a device
starts with.

## Three layers

| Layer | File(s) | What it is for |
| --- | --- | --- |
| Install-time defaults | `.env` (on a device: `/etc/irivai/studio.env`) | What a new or reset device starts as: its hostname, hotspot address and password, storage paths |
| Built-in defaults | `config/*.toml` | Structure and safe defaults, part of the software |
| Saved settings | `config/*.local.toml` | Everything saved on the device from its pages, plus secrets. Never committed |

**A setting saved on a page always wins.** `.env` says what a device starts as,
not what it must stay. A variable already set in the real environment (systemd,
the shell) wins over `.env` too.

## The `.env` file

Copy [`.env.example`](../.env.example) to `.env` and change what you need. Every
line is optional: a missing or blank value means the built-in default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IRIV_DEFAULT_HOSTNAME` | `IRIV-EdgeAI-Studio` | The device's name on the network (`<name>.local`). Letters, digits and `-` |
| `IRIV_AP_ADDRESS` | `192.168.1.1` | The device's own address on its hotspot |
| `IRIV_AP_PASSWORD` | `iriv1234` | The hotspot password a new or reset device broadcasts (8-63 characters) |
| `CYTRON_STORE` | automatic | Where systems, recordings and the database live. Blank = the NVMe if mounted, else the SD card |
| `CYTRON_NVME` | `/mnt/nvme` | Where the NVMe is mounted |
| `CYTRON_MODEL_DIR` | automatic | An extra folder to look for AI models in |
| `CYTRON_BUILTIN_MODEL_DIR` | `/opt/irivai-models` | The folder for the built-in models |

An invalid value (a password under 8 characters, a public IP address, a hostname
with spaces) is ignored and the default is used, so a typo never stops the
device from coming up.

If you set `CYTRON_MODEL_DIR` or `CYTRON_STORE` to a custom folder and later
remove the line, the software goes back to the default folders. Whatever is in
the custom folder is not shown until the line is back or the files are moved.

### Which file is read

The first that exists:

1. the path in `$IRIV_ENV_FILE`
2. `.env` in the software folder (a development checkout)
3. `/etc/irivai/studio.env` (an installed device)

On a device the file is kept in `/etc/irivai/`, outside the software folder, so
updates keep it. `scripts/install.sh` writes it once - from your `.env` if the
folder you install from has one, otherwise from `.env.example` - and never
overwrites it afterwards. To change a value on a device, edit
`/etc/irivai/studio.env` and restart the software (`sudo systemctl restart irivai-hub`).

The software runs only on an IRIV Edge AI device with an AI HAT. That check
cannot be switched off from `.env`.

## Hotspot (AP mode)

When the device broadcasts its own Wi-Fi hotspot, devices that join it get an
address in the same range as the hotspot address and open the dashboard there:

- default address **192.168.1.1** (clients get `192.168.1.x`)
- change it under **Network → Hotspot → Hotspot IP address**, or set the default with `IRIV_AP_ADDRESS`
- any private address ending `.1`-`.254` works (`192.168.x.x`, `10.x.x.x`, `172.16-31.x.x`)

**If a cable is on the same network range.** The hotspot and an Ethernet cable
cannot share a range: the device would answer its hotspot clients out of the
cable port and they would lose the page. When the hotspot address falls inside
the cable's network (for example the cable is on `192.168.1.x` and the hotspot
is set to `192.168.1.1`), the hotspot comes up on **10.42.0.1** instead, and the
Network page says so. Pick a different hotspot address (for example
`192.168.50.1`) to use your own. The device's single Wi-Fi radio is not a client
while it broadcasts, so only the cable can collide.

## Hostname

A new device is called **`IRIV-EdgeAI-Studio`** and is reachable as
`http://IRIV-EdgeAI-Studio.local` from most computers on the same network.

- With several devices on one network, the later ones answer as
  `IRIV-EdgeAI-Studio-2.local`, `-3` and so on. The device's front panel and the
  Network page show the name it actually got.
- Rename a device under **Network → Hostname** (capitals are kept), or set the
  default with `IRIV_DEFAULT_HOSTNAME` before installing.
- A computer that cannot use `.local` names reaches the device by its IP address,
  shown on the front panel.
