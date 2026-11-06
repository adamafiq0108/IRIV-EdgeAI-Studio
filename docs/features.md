# Feature report - what this template ships, and how to use it

Written for a developer picking the template up for the first time. Each
section says what the feature is, where the code lives, how to use it, and what
it deliberately refuses to do.

Companion docs: [`contract-v1.md`](contract-v1.md) (device contract),
[`conventions.md`](conventions.md) (rules), [`setup.md`](setup.md) (install),
[`database-schema.md`](database-schema.md) (schema), [`configuration.md`](configuration.md)
(`.env`, hotspot, hostname).

---

## 0. Shape

```
hub (5100)  ── card per system, links out, proxies nothing
  └ master per system (5110, 5111, …)  ── polls devices, serves the JSON API + SPA
      └ agent per device (5101)        ── Contract v1 over an adapter
          └ detection app              ── capture → detect → decide → store
```

Backend: FastAPI, pure JSON. Frontend: React 18 + Vite, built into
`core/static/app/`. State: PostgreSQL for folders/metadata/audit only - the
master boots and polls without it.

---

## 1. The reference system (`systems/demo/`)

One folder holds a whole system. Copy it to start.

| File | What it is |
|---|---|
| `system.py` | master-side SPEC: hub card, nav, analytics widgets |
| `adapter.py` | device-side Contract adapter (read + v1.2 control + v1.3 telemetry) |
| `config.toml` | the app's own config (`config.local.toml` overrides, gitignored) |
| `app/run.py` | the detection loop + MJPEG server |
| `app/detector.py` | Detector interface, registry (`mock`, `hailo`, `yolo`, `onnx`) |
| `app/camera.py` | capture, JPEG encode, annotate, synthetic fallback |
| `app/store.py` | `events.jsonl` + snapshots + the durable cursor |
| `app/runtime.py` | `state.json`, start/stop as a process |
| `app/hailo.py` | Hailo-8 / 8L / 10H support |
| `app/buses.py` | I2C, GPIO, 1-Wire, serial, Modbus |
| `app/iriv.py` | IRIV Edge AI (CM5) board support |

**Use it**

```bash
cd systems/demo && python -m app.run --once     # records one event, no deps
python -m agent.agent --adapter systems.demo.adapter:DemoAdapter --port 5101 \
       --serial-fallback devbox0001
python tools/contract_check.py http://127.0.0.1:5101 --token <token>
```

Verified: **57/57 checks CONFORMING** with the app running.

**Refuses to**: import the dashboard, open a second camera reader, keep the
cursor in memory, or fabricate a device serial.

Skills: `build-detection-app`, `ai-tasks-and-models`, `new-slave-adapter`.

---

## 2. AI tasks and backends

Task (what it is for) and backend (what runs it) are chosen separately:

```toml
[detector]
task = "detection"     # detection classification segmentation pose face depth ocr tracking
kind = "hailo"         # mock | hailo | yolo | onnx
model = "yolov8n"
confidence = 0.45
zone = [220, 140, 420, 340]
```

Detections have one fixed shape (`[{label, confidence, box}]`, plus
`track_id` when tracked) because the dashboard renders events generically.
Backends are imported lazily, so a laptop clone never installs HailoRT or
torch.

Skill: `ai-tasks-and-models`.

---

## 3. Hailo accelerators (8 / 8L / 10H)

`app/hailo.py`: architecture detection (`hailortcli` → `lspci` → sysfs), HEF
resolution from `models/<arch>/<name>.hef`, a `VDevice.create_infer_model`
detector assuming NMS-on-chip, letterboxing, box rescaling to frame pixels,
temperature probe for telemetry.

**A HEF compiled for one architecture will not load on another** - that is why
the arch is detected and the path includes it.

Skill: `hailo-accelerator`.

---

## 4. Bring your own model (no code)

Page: **AI Models** (`/models`, admin).

1. Upload weights (`.hef`, `.onnx`, `.tflite`, `.pt`) - 512 MB cap.
2. Fill the manifest form (task, arch, input size, postprocess, confidence,
   classes). The form *is* `<config_dir>/models/<name>.json`, shown as JSON.
3. Paste labels (line order = class index).
4. **Deploy** to a device: the master sends the manifest plus a pull URL; the
   device fetches the weights itself.

Files: `core/services/models.py`, `core/routes/models.py`,
`frontend/src/pages/Models.jsx`. A device without a `control.detection` domain
refuses - correctly.

Skill: `ai-tasks-and-models`.

---

## 5. Dashboard builder (drag and drop)

Page: **Builder** (`/builder`, admin).

Drag tiles/charts/tables onto the canvas, bind each to a live data key, watch a
preview rendered from real fleet data, then:

- **Save** - the layout renders on Analytics immediately.
- **Publish → download** - a `.zip` with `system.py` + `layout.json` + README.
- **Publish → install** - writes the package into `[systems].dir` (never
  overwrites without asking).

**A layout is data. Nothing generated is executed as code** - the published
`system.py` loads `layout.json` and calls the same shared renderer.

Files: `core/services/builder.py`, `core/services/publish.py`,
`core/routes/builder.py`, `frontend/src/pages/Builder.jsx`.

Skill: `widget-builder`.

---

## 6. Remote access - domain and tunnel

Page: **Remote** (`/remote`, admin).

- **Domain**: store the public URL, and check whether DNS resolves to this host
  (`resolves`, `addresses`, `matches_host`). TLS terminates in a proxy in
  front; the master never manages certificates.
- **Tunnel**: supervise `cloudflared` or `ngrok`, capture the announced URL and
  the last log lines. The master never installs the binary, never uses
  `shell=True`, and every start/stop is audited.

A tunnel publishes the master to the internet; the confirm dialog says so.

Files: `core/services/remote.py`, `core/routes/remote.py`,
`frontend/src/pages/Remote.jsx`. Skill: `remote-access`.

---

## 7. Logs and error handling

Page: **Logs** (`/logs`, admin).

- A bounded in-memory ring (2000 records) fed by every logger in the process,
  installed before anything logs.
- Secret-shaped assignments are redacted (`token=`, `password=`, `bearer=`, …).
- Global handlers turn any unhandled exception into one recorded line plus
  `{"ok": false, "error": "internal error - see Logs"}` - the message stays
  server-side.
- Filter by level/logger/substring, expand tracebacks, live tail, clear.
- Client side: a toast provider wraps the shell; `toast.run(promise, …)` gives
  every page failure reporting for one line of code.

Kept in memory on purpose: it works with the database down, and cannot fill a
Pi's disk. Durable history is the audit log.

Files: `core/services/logs.py`, `core/routes/logs.py`,
`frontend/src/components/ui/Toast.jsx`, `frontend/src/pages/Logs.jsx`.
Skill: `logs-and-errors`.

---

## 8. Hardware I/O

`app/buses.py` - I2C scan/read/write (`i2cdetect` or smbus2, reserved
addresses skipped), GPIO set/get, 1-Wire, serial enumeration, Modbus RTU/TCP.
Everything degrades to an empty answer off-device.

`app/iriv.py` - IRIV Edge AI (CM5): 2× isolated DI, 2× isolated DO, isolated
RS485 (Modbus), ATECC608B serial, CM5 serial, SSD1315 OLED, buzzer/button/LEDs,
plus a Hailo probe. **Pin numbers are configuration**, never constants - an
unconfigured helper answers "not configured" rather than driving a relay.

Skills: `bus-io`, `iriv-edge-ai-board`, `mqtt-link`.

---

## 9. Identity and stamping

Three separate questions: which device (CM5 serial, clonable with the image),
which board (ATECC608B serial, not clonable), and may this install run
(activation key derived from the ATECC serial). Re-stamping the same hardware
is a no-op; different hardware refuses loudly.

Skill: `device-identity-stamping`.

---

## 10. Auth

One credential store for the hub and every master on a host; a host-scoped
signed-cookie session. `admin` from `auth.local.toml`, operators from
`users.local.toml` with a per-system allowlist. Session 401s carry
`X-Session-Required` so the SPA can tell "expired" from "wrong password".
Destructive and configuration actions are admin-only and audited.

Skill: `auth-and-users`.

---

## 11. Contributor tooling

- `tools/mock_slave.py` - a conforming device backed by nothing.
- `tools/contract_check.py` - the arbiter. An adapter is done when this passes
  **unedited**.
- `.github/workflows/ci.yml` - conventions audit (layering, slug branching,
  line ceilings), mock-device conformance, demo app + adapter conformance,
  frontend build.
- `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates.
- `LICENSE` - **placeholder**: no rights granted until one is chosen.

---

## 12. Workflow builder

A node-based canvas (Workflow page) that turns camera detections, sensors and
schedules into actions, with no code.

- **Palette.** Nodes grouped by drawer: AI and analytics, logic (Condition,
  Trigger, Recorded Trigger, If / Else if), variables, integrations (Modbus,
  MQTT, HTTP), outputs (Send Command, Send Images, Send Message, Image Snapshot,
  Recording Clip, Zones), Dashboard & Analytics (Card Widget, Chart Widget) and
  Common (Comment, Group, Function, Link).
- **On this canvas.** Each drawer lists what is already on the canvas - custom
  formulas, cards, charts and the main node types. Drag one to copy it,
  double-click to jump to it.
- **Quick add.** Drag a wire out of a port and let go on empty canvas: a
  searchable list offers only the nodes that wire can legally connect to, and
  the picked node is added with the wire already on it.
- **Function.** A frame that holds a reusable piece of flow with input and
  output pins; a Call node runs it from anywhere. Nodes join a frame only when
  dropped inside it.
- **Group.** A coloured, named box for tagging nodes. It grows to fit what is
  in it and cannot shrink smaller than its nodes.
- **Wires.** Live wires animate; a wire with a problem shows a red ✕.
- **Names.** Nodes that need an identity ask for one when added
  ("Trigger Name", "Card Widget Name" ...).
- **Guides.** Built-in step-by-step guides (Basic AI System, Condition &
  Trigger, Database) and a **Nodes Guideline** page: every node's real
  properties panel as a numbered tour, with an explanation for every setting
  and every dropdown choice, including the settings a choice adds.

## 13. Modbus

- **Modbus Read** reads registers or coils from a Modbus Server (RTU over the
  RS485 port, or TCP). With **Quantity** above 1 it reads a block;
  **Output points** picks which registers get their own output point on the
  node (`[0] addr 100`, `[1] addr 101` ...), each wire carrying that register.
  The node says **no answer (last value)** when the device stops answering.
- **Scan for devices** (under Unit-Id) asks every Unit-Id 1-247 on the server
  for one register and lists the ones that answer; pick one to fill Unit-Id.
  Read-only; it takes turns with the other Modbus nodes on the same wire. A
  full scan takes about 50 seconds at 9600 baud.
- **Modbus Write** writes coils or registers (FC 5, 6, 15, 16) when its
  trigger fires or its value changes.
- **Advanced** (top right of both nodes' properties) opens the **Modbus frame
  builder** for devices that need a non-standard request: ID, FC and CRC stay
  fixed by the standard; the fields between them can be reordered, resized,
  byte-swapped and joined by vendor fields such as a password. A live byte map,
  the CRC (CRC-16/MODBUS) and the exact hex frame update as you edit, with
  warnings for non-standard layouts. On a Write the value is filled in at the
  moment of each write. Custom frames are sent on RTU (serial) servers; over
  TCP the node keeps the standard frame. A saved layout shows a
  **Custom frame** badge.

## 14. Device and network

- **Device lock.** The software runs only on an IRIV Edge AI device (CM5 or
  ATECC present) with an AI HAT. It is checked at install, at sign-in and when
  detection starts, with a plain message saying what is missing.
- **Hotspot (AP mode).** The device's own Wi-Fi hotspot, at **192.168.1.1** by
  default; the address can be changed under Network. If a cable is on the same
  network range, the hotspot uses 10.42.0.1 instead and the page says why.
- **Hostname.** A new device is `IRIV-EdgeAI-Studio.local`; renamed under
  Network.
- **`.env`.** The defaults a device starts with (hostname, hotspot address and
  password, storage paths) in one file - see [configuration.md](configuration.md).
- **Shut down / restart.** The software is stopped first, then the device
  powers off, so it shuts down quickly and cleanly.

## Endpoint map (added by this work)

| Method | Path | Who |
|---|---|---|
| GET | `/api/builder` | session |
| PUT | `/api/builder` | admin |
| POST | `/api/builder/preview` | session |
| POST | `/api/builder/publish` | admin |
| GET | `/api/builder/export` | session |
| GET | `/api/models` | session |
| POST | `/api/models` | admin |
| POST | `/api/models/upload` | admin (multipart) |
| GET | `/api/models/{name}/file` | session (devices pull) |
| DELETE | `/api/models/{name}` | admin |
| POST | `/api/models/{name}/deploy` | admin |
| GET/PUT | `/api/remote` | admin |
| POST | `/api/remote/check` | admin |
| POST | `/api/remote/tunnel/start\|stop` | admin |
| GET | `/api/logs` | admin |
| POST | `/api/logs/clear` | admin |

---

## Known limits

- The tunnel supervisor holds state in the master process: a master restart
  drops the tunnel (and the log ring).
- `control_set` on the demo adapter refuses settings writes by design - a real
  system writes `config.local.toml` and restarts its app.
- The `onnx` detector is a stub: decoding differs per export, and a wrong
  decode looks like a detector that never fires.
- Builder widgets read `stats`/`events` only; anything needing real computation
  belongs in a package `WidgetSpec`.
- Model deploy requires the device adapter to serve `control.detection`.
- Uploaded weights and `*.local.toml` are outside git - back them up
  separately.
