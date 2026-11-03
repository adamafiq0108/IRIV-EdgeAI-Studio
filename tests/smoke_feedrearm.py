# A live feed that never paints, and the refresh that made it worse.
#
# WebRTCVideo already has three watchdogs and falls back to MJPEG, but all of
# them can end in a session that is "connected" and carrying nothing -- and
# then Camera Setup's "Camera connecting -- opening the video stream..." sheet
# sits there for as long as anyone cares to watch. The operator's own fix was
# to leave the page and come back, which works because it REMOUNTS the player.
# So the page does that itself now.
#
# The second half is the reason the obvious workaround failed: pressing reload
# while a stream is opening produced "the system did not answer in time" over
# a perfectly healthy box. A live feed is a long-lived connection and a browser
# allows about six per origin, so the status call queued behind one that never
# finished. A timeout there is a question worth asking again, not a page to
# throw away.
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


zones = read("frontend", "src", "pages", "Zones.jsx")

# -- the feed re-arms itself ------------------------------------------------
check("a stalled feed is re-armed on a timer",
      "const FEED_RETRY_MS = 3000;" in zones and "const FEED_RETRIES = 3;" in zones)
# Three seconds is the number the operator asked for. Pinned because it is a
# JUDGEMENT (long enough not to interrupt a slow camera, short enough that
# nobody reaches for reload), not an implementation detail.
check("...after three seconds, not thirty", "FEED_RETRY_MS = 3000" in zones)
check("...and it gives up rather than flickering for ever",
      "feedRetries.current >= FEED_RETRIES" in zones)
check("...the wait growing with each attempt",
      "FEED_RETRY_MS * (spent ? 2 : feedRetries.current + 1)" in zones)
# ...and the ladder ENDS in a state, not in a loader turning for ever: the
# spent budget's last wait sets feedFailed, which unmounts the player and
# shows "No video" with a Retry (Tok Aba, 2026-10-09).
check("...and a spent budget ends in No video, not an endless loader",
      "if (spent) { setFeedFailed(true); return; }" in zones
      and "data-feed-failed" in zones and "retryFeed" in zones)
check("a painted frame clears the count",
      "if (feedLive) { feedRetries.current = 0; return undefined; }" in zones)
check("...and so does changing camera",
      "feedRetries.current = 0;" in zones.split("feedNonce]")[0])

# The bump has to REMOUNT the player. Changing the img URL alone re-requests
# the MJPEG fallback and leaves a wedged WebRTC session exactly where it was,
# which is the failure this whole thing is about.
m = re.search(r"<WebRTCVideo\s*\n\s*(key=\{feedKey\})", zones)
check("re-arming remounts the player, not just the img URL", bool(m),
      "WebRTCVideo has no key={feedKey}")

# -- a timeout is asked again, not painted over the page --------------------
for page in ("Zones.jsx", "Detection.jsx"):
    src = read("frontend", "src", "pages", page)
    check(f"{page} retries a timed-out status call",
          "e?.status === 0 && attempt < 2" in src, "no retry guard")
    check(f"...and {page} still reports a real failure",
          "setError(e);" in src)
    check(f"...{page}'s loader can count its own attempts",
          "function load(" in src)

# Status 0 is the client's own word for "timed out or the network went" -- if
# that ever became a different number, the guard above would silently stop
# retrying and the bug would come back with no test failing.
client = read("frontend", "src", "api", "client.js")
check("status 0 is what a timeout still reports",
      'new ApiError("the system did not answer in time", 0)' in client)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
