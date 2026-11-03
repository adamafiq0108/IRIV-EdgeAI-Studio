# The video gateway serves nothing to the LAN but WebRTC media (Pipi H1).
#
# WHAT WENT WRONG. MediaMTX v1.20.1 turns MoQ on by default, on every
# interface (:8892 TCP/UDP, :8893 UDP), with a player page and a self-signed
# certificate a browser takes without a prompt; its default auth lets user
# "any" publish and read every path from any address. Our mediamtx.yml named
# rtmp/hls/srt off and never heard of MoQ, so on .164 any host on the plant
# LAN could open https://<box>:8892/<slug>__<camera>/ and watch, no login --
# and for a passthrough camera that read made the gateway open the camera's
# main stream with its stored password.
#
# WHAT IS PINNED (the yml PARSED, not grepped):
#   1. every listener the product does not use is OFF by name: moq, playback,
#      rtmp, hls, srt, metrics, pprof; the three it uses (rtsp, webrtc, api)
#      are on and bound to 127.0.0.1.
#   2. authInternalUsers: every grant is loopback-only (127.0.0.1/32, ::1/128),
#      read included, and publish/read/api are granted (the product needs
#      them from loopback). No user with an empty ips list (= any address).
#   3. read can be loopback-only because nothing reads from off the box: the
#      master's WHEP proxy talks to 127.0.0.1, the page posts to that proxy
#      and never to :8889, the app publishes to 127.0.0.1:8554, the API
#      callers use 127.0.0.1:9997.
#   4. every key is one validated against the pinned MediaMTX (v1.20.1):
#      an unknown key makes MediaMTX exit, which on a box is no video.
#   5. the installer pins the tarball sha256 per architecture and checks it
#      before installing (Pipi M2), and writes the yml root:root 0644.
#   6. an update delivers the yml (root_items.json "upgrade" table).
#
# The yml was also run through the real v1.20.1 binary (2026-10-11, docker):
# it starts with no warning, listens on 127.0.0.1:8554/8889/9997 and UDP
# :8189 only, and refuses publish, RTSP read, WHEP read and the API from a
# non-loopback address with 401 while the product's loopback calls pass.
#
#     python tests/smoke_mediamtxauth.py
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8", newline="").read()


# -- a YAML subset parser: what mediamtx.yml uses, nothing more ---------------
# PyYAML is not a dependency of this project, so the smoke carries its own
# reader for block mappings, block sequences (of scalars or of mappings),
# flow lists of scalars and YAML 1.1 booleans (MediaMTX reads yes/no). A
# construct it does not know is an error, never a silent guess; with PyYAML
# installed the two readers must agree.

class YamlError(ValueError):
    pass


def _strip_comment(line):
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(text):
    t = text.strip()
    if t == "" or t in ("~", "null"):
        return None
    if t[0] in "'\"":
        if len(t) < 2 or t[-1] != t[0]:
            raise YamlError(f"unterminated string: {t!r}")
        return t[1:-1]
    if t.startswith("["):
        if not t.endswith("]"):
            raise YamlError(f"unterminated flow list: {t!r}")
        inner = t[1:-1].strip()
        return [] if not inner else [_scalar(p) for p in inner.split(",")]
    if t[0] in "{&*!|>":
        raise YamlError(f"construct not supported here: {t!r}")
    low = t.lower()
    if low in ("yes", "true", "on"):
        return True
    if low in ("no", "false", "off"):
        return False
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    return t


def parse_yaml(text):
    if "\t" in text:
        raise YamlError("a tab in the indentation")
    lines = []
    for raw in text.splitlines():
        s = _strip_comment(raw)
        if s.strip():
            lines.append([len(s) - len(s.lstrip(" ")), s.strip()])

    def block(i, indent):
        if lines[i][1].startswith("- ") or lines[i][1] == "-":
            return seq(i, indent)
        return mapping(i, indent)

    def mapping(i, indent):
        out = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("-"):
            m = re.fullmatch(r"([A-Za-z0-9_]+):(?:\s+(.*))?", lines[i][1])
            if not m:
                raise YamlError(f"not a 'key: value' line: {lines[i][1]!r}")
            key, val = m.group(1), m.group(2)
            if key in out:
                raise YamlError(f"duplicate key {key}")
            i += 1
            if val is None and i < len(lines) and lines[i][0] > indent:
                out[key], i = block(i, lines[i][0])
            else:
                out[key] = _scalar(val or "")
        if i < len(lines) and lines[i][0] > indent:
            raise YamlError(f"unexpected indentation at {lines[i][1]!r}")
        return out, i

    def seq(i, indent):
        out = []
        while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("-"):
            rest = lines[i][1][1:].strip()
            if re.match(r"[A-Za-z0-9_]+:(\s|$)", rest):
                # "- key: v" opens a mapping whose keys sit two columns in.
                lines[i] = [indent + 2, rest]
                item, i = mapping(i, indent + 2)
            else:
                item, i = _scalar(rest), i + 1
            out.append(item)
        return out, i

    doc, i = mapping(0, lines[0][0] if lines else 0) if lines else ({}, 0)
    if i != len(lines):
        raise YamlError(f"trailing text at {lines[i][1]!r}")
    return doc


YML_TEXT = read("scripts", "mediamtx", "mediamtx.yml")
Y = parse_yaml(YML_TEXT)
try:
    import yaml
except ImportError:
    yaml = None
if yaml is not None:
    check("the subset reader and PyYAML read the same document",
          yaml.safe_load(YML_TEXT) == Y)

# The reader itself refuses what it does not understand.
for text, why in (("a:\n\tb: 1\n", "tab"), ("a: {b: 1}\n", "flow map"), ("a: 1\na: 2\n", "dup"),
                  ("a: 'x\n", "unterminated")):
    try:
        parse_yaml(text)
        refused = False
    except YamlError:
        refused = True
    check(f"reader refuses a {why}", refused)

# -- 1. listeners -----------------------------------------------------------------
for key in ("moq", "playback", "rtmp", "hls", "srt", "metrics", "pprof"):
    check(f"1. {key}: explicitly off (a default can change under us; v1.20.1 turned MoQ on)",
          key in Y and Y[key] is False, Y.get(key, "<missing>"))
for key, addr in (("rtsp", "rtspAddress"), ("webrtc", "webrtcAddress"), ("api", "apiAddress")):
    check(f"1. {key}: on, {addr} on 127.0.0.1", Y.get(key) is True
          and str(Y.get(addr, "")).startswith("127.0.0.1:"), (Y.get(key), Y.get(addr)))
check("1. RTSP publish over TCP only (no UDP RTP/RTCP listeners 8000/8001)",
      Y.get("rtspTransports") == ["tcp"] and Y.get("rtspEncryption") == "no", Y.get("rtspTransports"))
check("1. WebRTC over UDP 8189 only, no TCP media listener, no STUN",
      Y.get("webrtcLocalUDPAddress") == ":8189" and Y.get("webrtcLocalTCPAddress") == ""
      and Y.get("webrtcICEServers2") == [], Y.get("webrtcLocalTCPAddress"))

# -- 2. who may do what -----------------------------------------------------------------
LOOPBACK = {"127.0.0.1/32", "::1/128", "127.0.0.1", "::1"}
ACTIONS = ("publish", "read", "playback", "api", "metrics", "pprof")
users = Y.get("authInternalUsers")
check("2. authMethod internal, with our own authInternalUsers (not MediaMTX's default)",
      Y.get("authMethod") == "internal" and isinstance(users, list) and users, users)
users = users if isinstance(users, list) else []
granted = {}
for u in users:
    ips = u.get("ips")
    check(f"2. user {u.get('user')!r}: ips set, every one loopback",
          isinstance(ips, list) and ips and set(ips) <= LOOPBACK, ips)
    check(f"2. user {u.get('user')!r}: no password in the file", not u.get("pass"))
    for p in u.get("permissions") or []:
        check(f"2. {p.get('action')!r}: a known action, on every path",
              p.get("action") in ACTIONS and not p.get("path"), p)
        granted.setdefault(p.get("action"), set()).update(ips or ["<any>"])
check("2. publish, read and api granted (the product needs all three, from loopback)",
      {"publish", "read", "api"} <= set(granted), granted)
check("2. read is loopback-only: the WHEP proxy is the only reader",
      granted.get("read", {"<any>"}) <= LOOPBACK, granted.get("read"))
check("2. publish, api, metrics, pprof, playback loopback-only",
      all(granted.get(a, set()) <= LOOPBACK for a in ACTIONS), granted)
check("2. nothing trusted to forward a client address (X-Forwarded-For)",
      not any(Y.get(k) for k in Y if k.endswith("TrustedProxies")))

# -- 3. nobody reads from off the box ---------------------------------------------------------
from core.services import streaming                                    # noqa: E402

check("3. the master's WHEP proxy and API calls go to 127.0.0.1",
      streaming.GATEWAY_HOST == "127.0.0.1"
      and streaming.whep_url("s", "c").startswith("http://127.0.0.1:8889/"))
player = read("frontend", "src", "components", "WebRTCVideo.jsx")
check("3. the page posts its offer to the master's proxy, never to the gateway",
      "/api/detector/webrtc/whep" in player and not re.search(r":(8889|8554|9997|8892)\b", player))
run_src = read("systems", "demo", "app", "run.py")
check("3. the app publishes to 127.0.0.1:8554",
      'f"rtsp://127.0.0.1:8554/' in run_src
      and not re.search(r"rtsp://(?!127\.0\.0\.1)[^\"'/]*:8554", run_src))
for rel in (("systems", "demo", "app", "passthrough.py"), ("systems", "demo", "app", "view_gate.py"),
            ("tools", "profile_cpu.py")):
    src = read(*rel)
    check(f"3. {'/'.join(rel)} calls the API on 127.0.0.1:9997",
          "http://127.0.0.1:9997/" in src and not re.search(r"https?://(?!127\.0\.0\.1)[^\"'/]*:9997", src))

# -- 4. only keys the pinned MediaMTX knows ------------------------------------------------------
# Every top-level key below exists in v1.20.1's own mediamtx.yml and the file
# was started by that binary with no warning. A new key means: check it
# against the pinned version first (an unknown key is a gateway that exits).
V1201_CHECKED = {
    "logLevel", "logDestinations", "authMethod", "authInternalUsers", "readTimeout",
    "writeTimeout", "writeQueueSize", "udpMaxPayloadSize", "rtsp", "rtspTransports",
    "rtspAddress", "rtspEncryption", "webrtc", "webrtcAddress", "webrtcEncryption",
    "webrtcAllowOrigins", "webrtcLocalUDPAddress", "webrtcLocalTCPAddress",
    "webrtcIPsFromInterfaces", "webrtcAdditionalHosts", "webrtcICEServers2", "rtmp", "hls",
    "srt", "moq", "playback", "api", "apiAddress", "metrics", "pprof", "paths"}
check("4. every key was validated against MediaMTX v1.20.1", set(Y) <= V1201_CHECKED,
      set(Y) - V1201_CHECKED)
INSTALLER = read("scripts", "mediamtx", "install.sh")
check("4. ...which is the version the installer pins",
      re.search(r"^PINNED_VER=v1\.20\.1$", INSTALLER, re.M) is not None)
check("4. no deprecated key (v1.20.1 warns on webrtcAllowOrigin)", "webrtcAllowOrigin" not in Y)

# -- 5. the installer ---------------------------------------------------------------------------------
pins = dict(re.findall(r"^SHA256_(arm64|armv7|amd64)=([0-9a-f]+)$", INSTALLER, re.M))
check("5. a 64-hex sha256 pinned for arm64, armv7 and amd64",
      set(pins) == {"arm64", "armv7", "amd64"} and all(len(v) == 64 for v in pins.values()), pins)
check("5. the CM5's pin is the v1.20.1 linux_arm64 release hash",
      pins.get("arm64") == "d1689f0bfefb1864e5ed3dcc8495eb2d7ec0a654f90bf3cd48980cb3bd08718a")
code = "\n".join(ln for ln in INSTALLER.splitlines() if not ln.lstrip().startswith("#"))
i_check, i_install = code.find("sha256sum -c"), code.find("install -m755")
check("5. the download is checked before it is installed, and a mismatch exits",
      0 < i_check < i_install and "NOT installed" in code[i_check:i_install]
      and "exit 1" in code[i_check:i_install])
check("5. another version needs MEDIAMTX_SHA256, else refused (fails closed)",
      'SHA="${MEDIAMTX_SHA256:-}"' in code and "no sha256 pinned" in code)
check("5. the yml is installed root:root 0644",
      'install -m 644 -o root -g root "$HERE/mediamtx.yml" /etc/mediamtx/mediamtx.yml' in code)
check("5. a gateway listening off loopback (but UDP 8189) is stopped by the installer",
      "ss -Hlntup" in code and "8189" in code and "systemctl stop irivai-mediamtx" in code)

# -- 6. an update delivers it ------------------------------------------------------------------------
ITEMS = json.loads(read("scripts", "root_items.json"))
check("6. the update's root step installs this very file",
      (ITEMS.get("upgrade") or {}).get("mediamtx") == "scripts/mediamtx/mediamtx.yml")

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
