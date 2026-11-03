# The WebRTC video path: naming, fallback, and the security shape.
#
# The app publishes to the gateway and the master hands browsers the stream;
# they must name the same path, and the app cannot import core/ -- so the
# rule is written twice and pinned here. A drift between them is a camera
# that publishes to one address while every viewer asks for another, which
# looks exactly like "the stream never comes up".
import inspect
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import streaming as st                     # noqa: E402

# 1. path naming is stable, flat, and sanitised
assert st.path_name("counting", "main") == "counting__main"
assert st.path_name("counting") == "counting__main"
# An id minted from an operator's camera name must not escape into a URL.
assert st.path_name("a/b", "Bay 3 · east") == "a-b__Bay-3-east", \
    st.path_name("a/b", "Bay 3 · east")
assert "/" not in st.path_name("a/b/c", "x/y")
assert st.path_name("", "") == "system__main"
print("path:", st.path_name("counting_template_editor", "cam2"))

# 2. the app's duplicated copy of that rule agrees with it
with open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
          encoding="utf-8") as f:
    run_src = f.read()
m = re.search(r"def stream_url\(camera_id\):(.+?)\n\n", run_src, re.S)
assert m, "run.py lost stream_url()"
app_rule = m.group(1)
assert "__" in app_rule, "the app must join slug and camera with __"
assert "8554" in app_rule, "the app must publish to the gateway's RTSP port"
# Same sanitiser class on both sides.
assert "a-zA-Z0-9_-" in app_rule
assert "a-zA-Z0-9_-" in inspect.getsource(st)

# ...and the same answer, on the cases that actually differ
def app_path(slug, cam):
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(cam or "main")).strip("-")
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-") or "system"
    return f"{base}__{safe or 'main'}"


for slug, cam in (("counting", "main"), ("a/b", "Bay 3 · east"),
                  ("x", ""), ("", "cam2"), ("s_1", "c-2")):
    assert app_path(slug, cam) == st.path_name(slug, cam), (slug, cam)
print("app and master agree on every naming case")

# 3. the gateway's ports are bound where the security model needs them
with open(os.path.join(ROOT, "scripts", "mediamtx", "mediamtx.yml"),
          encoding="utf-8") as f:
    yml = f.read()
assert "rtspAddress: 127.0.0.1:8554" in yml, "publish port must be loopback"
assert "webrtcAddress: 127.0.0.1:8889" in yml, \
    "signalling must be loopback -- the master proxies it so a session is required"
assert "apiAddress: 127.0.0.1:9997" in yml, "control API must be loopback"
# Media is the one port that must be reachable, and is safe to be.
assert "webrtcLocalUDPAddress: :8189" in yml
# The addresses offered to the browser are read off the interfaces per
# session; nothing stamped in at install time, which went stale on the
# customer board the first time its address moved (2026-10-04).
assert "webrtcIPsFromInterfaces: yes" in yml
assert "webrtcAdditionalHosts: []" in yml
with open(os.path.join(ROOT, "scripts", "mediamtx", "install.sh"),
          encoding="utf-8") as f:
    installer = "\n".join(ln for ln in f.read().splitlines()
                          if not ln.lstrip().startswith("#"))
assert "webrtcAdditionalHosts" not in installer and "hostname -I" not in installer, \
    "the gateway installer must not stamp an address into the config"
for off in ("rtmp: no", "hls: no", "srt: no"):
    assert off in yml, f"{off}: every extra listener is another way in"

# 4. the master's proxy is a real route and returns SDP. Read as SOURCE, not
# imported: this suite runs on dev boxes without fastapi installed.
with open(os.path.join(ROOT, "core", "routes", "models.py"),
          encoding="utf-8") as f:
    routes_src = f.read()
assert '@router.post("/api/detector/webrtc/whep"' in routes_src
whep = routes_src.split('"/api/detector/webrtc/whep"', 1)[1][:3500]
assert "application/sdp" in whep
assert "_system_context" in whep, "the proxy must be scoped to one system"
assert "asyncio.to_thread" in whep, \
    "the gateway round trip must not block the event loop"
assert 'headers["Location"] = (f"/api/detector/webrtc/session/{session}"' in whep, \
    "the answer must carry the session as the master's own address"
assert '@router.delete("/api/detector/webrtc/session/{session}"' in routes_src
sess = routes_src.split('"/api/detector/webrtc/session/{session}"', 1)[1][:1500]
assert "valid_session(session)" in sess, "a session id is checked before it is forwarded"
assert "whep_delete" in sess
# It lives behind the same middleware as every other /api route: that
# OPEN_PREFIXES does not exempt it is the security claim worth pinning.
with open(os.path.join(ROOT, "core", "routes", "auth.py"),
          encoding="utf-8") as f:
    auth_src = f.read()
m_open = re.search(r"OPEN_PREFIXES\s*=\s*\((.*?)\)", auth_src, re.S)
if m_open:
    assert "/api/detector" not in m_open.group(1), \
        "the webrtc proxy must not be reachable without a session"

# 5. mode() degrades to mjpeg rather than to nothing
# No gateway on this machine: every ask lands on the img.
_real_gateway, _real_ready = st.gateway_running, st.stream_ready
st.gateway_running = lambda: False
assert st.mode({}) == "mjpeg"
assert st.mode({"app": {"stream": "nonsense"}}) == "mjpeg"
assert st.mode({"app": {"stream": "webrtc"}}) == "mjpeg"
print("no gateway ->", st.mode({"app": {"stream": "webrtc"}}))
# WebRTC is the DEFAULT (Tok Aba, 2026-10-09): a system with no stream key
# gets it whenever the box can serve it, an explicit "mjpeg" never does.
assert st.DEFAULT_MODE == "webrtc" and st.FALLBACK_MODE == "mjpeg"
st.gateway_running = lambda: True
st.stream_ready = lambda slug, cam="": True
assert st.mode({}) == "webrtc", "no key must mean webrtc when it can be served"
assert st.mode({}, "sys", "cam1") == "webrtc"
assert st.mode({"app": {}}) == "webrtc"
assert st.mode({"app": {"stream": "mjpeg"}}) == "mjpeg", "an explicit mjpeg is respected"
assert st.mode({"app": {"stream": "MJPEG "}}) == "mjpeg"
st.stream_ready = lambda slug, cam="": False
assert st.mode({}, "sys", "cam1") == "mjpeg", "an unpublished camera degrades"
st.gateway_running, st.stream_ready = _real_gateway, _real_ready

# 6. the app treats an unpublishable box the same way -- and agrees on the
# default: the master offers what the app publishes.
assert 'stream_mode = "mjpeg"' in run_src or "stream_mode, publisher_mod" in run_src, \
    "the app must fall back to mjpeg when it cannot publish"
assert "publisher_mod.ensure_gateway()" in run_src, \
    "the app must check the gateway before building a pipeline per camera"
m_def = re.search(r'\.get\("stream"\)\s*\n?\s*or\s*"(\w+)"\)', run_src)
assert m_def, "run.py lost the [app] stream default"
assert m_def.group(1) == st.DEFAULT_MODE, \
    f"app default {m_def.group(1)!r} != master default {st.DEFAULT_MODE!r}"
print("default transport on both sides:", st.DEFAULT_MODE)

# 6b. the WHEP session is released: the offer reports the session, the
# master hands it back as its OWN address, and a DELETE route forwards it.
assert st.session_of("/counting__main/whep/3f1c2a4e-9b8d-4c1a-8a2b-1e2f3a4b5c6d") \
    == "3f1c2a4e-9b8d-4c1a-8a2b-1e2f3a4b5c6d"
assert st.session_of("http://127.0.0.1:8889/x__y/whep/abcdef12-3456") == "abcdef12-3456"
assert st.session_of("") == "" and st.session_of("/x/whep/../../etc") == ""
assert not st.valid_session("a/b") and not st.valid_session("short") \
    and st.valid_session("3f1c2a4e-9b8d-4c1a-8a2b-1e2f3a4b5c6d")
assert st.whep_delete("s", "c", "../x") is False, "a bad id never reaches the gateway"
sig = inspect.signature(st.whep_offer)
assert list(sig.parameters)[:3] == ["slug", "camera_id", "sdp_offer"]
assert "session_of(r.headers.get(\"Location\"))" in inspect.getsource(st.whep_offer)

# 7. the frontend falls back too, and asks before connecting
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "WebRTCVideo.jsx"), encoding="utf-8") as f:
    jsx = f.read()
assert "StreamImg" in jsx, "the player must keep the img as its fallback"
assert 'setMode("mjpeg")' in jsx
assert "/api/detector/webrtc" in jsx
assert "authHeaders" in jsx, "the handshake must carry this tab's session"
assert "iceServers: []" in jsx, "a LAN product must not call out to STUN"
assert 'method: "DELETE"' in jsx and 'headers.get("Location")' in jsx, \
    "the player must release its gateway session on teardown"
assert "verdict.key === camKey" in jsx, \
    "a camera switch must not run a handshake under the previous camera's verdict"

# 8. the publisher never lets a video problem cost a frame of detection
with open(os.path.join(ROOT, "systems", "demo", "app", "publisher.py"),
          encoding="utf-8") as f:
    pub = f.read()
assert "leaky=downstream" in pub, "a stalled gateway must drop frames, not block"
assert "block=false" in pub, "appsrc must never block the detection loop"
assert "config-interval=1" in pub, \
    "SPS/PPS must repeat or a late viewer waits for the next key frame"

# The MJPEG fallback must not be sticky: a component that fell back keeps
# re-asking the master and rejoins WebRTC when the publish is ready again.
# Sticky fallback held an MJPEG socket per camera forever -- three cameras
# saturate a browser's six-per-host budget and reloads crawl.
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "WebRTCVideo.jsx"), encoding="utf-8") as f:
    comp = f.read()
assert "RETRY_MS" in comp and "MAX_RETRIES" in comp,     "the fallback needs a rejoin loop"
assert 'd?.mode === "webrtc" && d?.ready' in comp,     "rejoin only when the master says the publish is READY -- no flicker"
assert "attemptsRef.current = 0" in comp,     "a working connection must restore the rejoin budget"

print("smoke_webrtc OK")
