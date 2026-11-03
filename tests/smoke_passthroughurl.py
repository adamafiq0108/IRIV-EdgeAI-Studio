# -*- coding: utf-8 -*-
"""Passthrough's pure half: which camera gets it, and from which address.

[video] feed = "passthrough" lets the video gateway pull an RTSP camera's
MAIN stream for the live view while detection decodes its sub-stream
(systems/demo/app/passthrough.py). The main stream is derived from the
detect URL by the path pairs the common brands use; a wrong derivation is a
camera whose view silently shows another stream, and a wrong eligibility
rule is an overlay-less picture or a USB camera handed to an RTSP puller.

    python tests/smoke_passthroughurl.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import passthrough as pt                              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def main_of(url):
    return pt.main_stream(url)[0]


# 1. sub -> main, by brand; credentials, host, port and query ride along
C = "rtsp://admin:P@ss@192.168.1.64:554"
for sub, want in (
        (f"{C}/Streaming/Channels/102", f"{C}/Streaming/Channels/101"),
        (f"{C}/streaming/channels/202", f"{C}/streaming/channels/201"),
        (f"{C}/Streaming/Channels/1002", f"{C}/Streaming/Channels/1001"),
        (f"{C}/Streaming/Channels/102?transportmode=unicast",
         f"{C}/Streaming/Channels/101?transportmode=unicast"),
        (f"{C}/h264/ch1/sub/av_stream", f"{C}/h264/ch1/main/av_stream"),
        (f"{C}/stream2", f"{C}/stream1"),
        (f"{C}/cam/realmonitor?channel=1&subtype=1",
         f"{C}/cam/realmonitor?channel=1&subtype=0"),
        (f"{C}/cam/realmonitor?subtype=1&channel=2",
         f"{C}/cam/realmonitor?subtype=0&channel=2")):
    got = main_of(sub)
    check(f"main stream of {pt.mask(sub)}", got == want, f"got {pt.mask(got)}")

# 2. already the main stream: the view pulls the same address
for main in (f"{C}/Streaming/Channels/101", f"{C}/stream1",
             f"{C}/cam/realmonitor?channel=1&subtype=0",
             f"{C}/h264/ch1/main/av_stream"):
    check(f"already main: {pt.mask(main)}", main_of(main) == main)

# 3. not derivable / not RTSP: no address, a reason without the address
for src, why in ((f"{C}/live/ch00_1", "no main stream known"),
                 (f"{C}/Streaming/Channels/103", "no main stream known"),
                 (f"{C}/cam/realmonitor?channel=1&subtype=2", "no main stream known"),
                 ("0", "not an RTSP camera"), ("synthetic", "not an RTSP camera"),
                 ("file:clip.mp4", "not an RTSP camera"),
                 ("gst:videotestsrc ! appsink", "not an RTSP camera"),
                 ("gige:cam1", "not an RTSP camera"),
                 ("orbbec:0", "not an RTSP camera"),
                 ("http://127.0.0.1:5102/raw?camera=x", "not an RTSP camera"),
                 ("rtsps://u:p@cam/stream2", "not an RTSP camera"),
                 ("rtsp://u:p@cam/stream2\nX: y", "not an RTSP camera"),
                 ("rtsp:///stream2", "not an RTSP camera"),
                 ("rtsp://cam:99999/stream2", "not an RTSP camera")):
    url, reason = pt.main_stream(src)
    check(f"no main for {src!r}", url is None and why in reason
          and "P@ss" not in reason and "@" not in reason, f"{url!r} {reason!r}")

# 4. view_url: explicit view_source wins, on the same host only
e = {"source": f"{C}/live/ch00_1",
     "view_source": "rtsp://admin:P@ss@192.168.1.64:554/live/ch00_0"}
check("explicit view_source used", pt.view_url(e) == (e["view_source"], ""))
e2 = dict(e, view_source="rtsp://admin:x@10.0.0.9/live/main")
check("view_source on another host refused",
      pt.view_url(e2)[0] is None and "another host" in pt.view_url(e2)[1])
e3 = dict(e, view_source="http://192.168.1.64/live")
check("non-rtsp view_source refused", pt.view_url(e3)[0] is None)
check("view_source on a USB camera ignored",
      pt.view_url({"source": "0", "view_source": e["view_source"]})[0] is None)

# 5. decide(): every legacy reason, and the passthrough answer
rtsp = {"source": f"{C}/Streaming/Channels/102"}
check("default feed is legacy, silent",
      pt.decide("legacy", True, "webrtc", rtsp) == ("legacy", "", None))
check("overlay server keeps legacy",
      pt.decide("passthrough", False, "webrtc", rtsp)[:2] == ("legacy", "overlay is server"))
check("stream mjpeg keeps legacy",
      pt.decide("passthrough", True, "mjpeg", rtsp)[:2] == ("legacy", "stream is mjpeg"))
check("gstreamer detector keeps legacy",
      pt.decide("passthrough", True, "webrtc", rtsp, gst_mode=True)[0] == "legacy")
check("parked camera has no pull",
      pt.decide("passthrough", True, "webrtc", rtsp, parked=True)[:2]
      == ("legacy", "camera is off"))
check("USB camera keeps legacy",
      pt.decide("passthrough", True, "webrtc", {"source": "0"})[:2]
      == ("legacy", "not an RTSP camera"))
feed, why, url = pt.decide("passthrough", True, "webrtc", rtsp)
check("eligible RTSP camera is passthrough from the main stream",
      feed == "passthrough" and why == "" and url == f"{C}/Streaming/Channels/101")

# 6. feed_setting reads [video] feed and defaults on anything odd
check("no [video] = legacy", pt.feed_setting({}) == "legacy")
check("feed passthrough", pt.feed_setting({"video": {"feed": " Passthrough "}}) == "passthrough")
check("typo = legacy", pt.feed_setting({"video": {"feed": "pasthrough"}}) == "legacy")
check("odd table = legacy", pt.feed_setting({"video": "x"}) == "legacy")

# 7. mask(): user:password gone from any URL in a text
for raw in ("rtsp://admin:P@ss@cam/x", "failed: rtsp://u:p/w@cam:554/s and more",
            '{"source":"rtsp://u:secret@10.0.0.2/stream1"}'):
    m = pt.mask(raw)
    check(f"mask {m}", "P@ss" not in m and "p/w" not in m and "secret" not in m
          and "***@" in m, m)
check("mask leaves a plain URL alone",
      pt.mask("rtsp://cam/stream1") == "rtsp://cam/stream1")

# 8. path conf: v1.20.1 option names, on-demand, tcp
conf = pt.path_conf("rtsp://u:p@cam/stream1")
check("path conf keys", set(conf) == {"source", "sourceOnDemand",
                                      "sourceOnDemandStartTimeout",
                                      "sourceOnDemandCloseAfter", "rtspTransport"},
      conf)
check("on demand, tcp, start timeout under the page's 6 s handshake",
      conf["sourceOnDemand"] is True and conf["rtspTransport"] == "tcp"
      and float(conf["sourceOnDemandStartTimeout"].rstrip("s")) < 5.0)

print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
