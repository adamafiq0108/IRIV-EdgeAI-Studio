# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
services/telegram_photos.py -- what the box saw, in the chat.

A command button answered by a Query says how many; this one shows which. The
snapshots are already on disk -- the app writes them when a rule fires -- so
this only has to choose the right ones and hand them to Telegram.

WHY IT IS CAPPED AT TEN. That is Telegram's own limit on a media group, and it
is the right limit anyway: an album is a glance at what just happened. A shift
of evidence is a different job, done by Footage > Export, which zips the
ORIGINALS -- Telegram recompresses every photo in an album, so the picture that
arrives in a chat is not the picture to keep.

The window is capped at 24 hours for the same reason, on the canvas rather than
here (telegram_commands.album_period_ok): a button that could ask for a month
would be a button that always overflows.

No framework imports here (docs/conventions.md sec 2).
"""

#: Telegram's ceiling on one media group. More than this is several messages,
#: which is several buzzes on somebody's phone for one answer.
MAX_ALBUM = 10

#: What "all of them" is allowed to mean. A window can hold hundreds, and a
#: node set to "all" would then be a node that empties a day into a chat --
#: so ALL means "every one, up to this", said out loud in the caption when it
#: bites. Six albums is already a lot of phone; past it, Footage > Export.
MAX_ALL = 60

#: The value that MEANS all. Zero rather than a flag, because the field it
#: comes from is a number box: 0 is the one number "how many" cannot otherwise
#: be, so it needs no second setting to contradict it.
ALL = 0

#: ...and on one photo sent as a photo. Anything bigger is skipped rather than
#: failing the whole album -- a single oversized frame must not cost the other
#: nine.
MAX_PHOTO_BYTES = 10 * 1024 * 1024


def cap(most):
    """How many pictures a setting of `most` actually sends.

    ALL (0) is every one up to MAX_ALL; anything else is itself, held between
    one and MAX_ALL. One place decides this, because the node, the stored
    document and the send would otherwise each have their own opinion.
    """
    try:
        n = int(most)
    except (TypeError, ValueError):
        return MAX_ALBUM
    if n <= ALL:
        return MAX_ALL
    return max(1, min(MAX_ALL, n))


def pick(systems_dir, slug, start, stop, camera="", most=MAX_ALBUM,
         newest=True):
    """(rows, total) -- the snapshots to send, and how many the window held.

    `rows` is at most `cap(most)` entries of {id, path, mtime, camera,
    camera_name}, in the order they happened. `total` is what the window
    actually contained, so the caller can say "10 of 40" rather than pretending
    it sent everything.
    """
    from core.services import detector_control as dc         # noqa: PLC0415

    want_cam = str(camera or "").strip()
    lo = start.timestamp() if start is not None else None
    hi = stop.timestamp() if stop is not None else None
    found = []
    # list_snapshots is newest-first and capped; the cap is generous because a
    # busy line writes a lot in a day and the window does the real narrowing.
    for row in dc.list_snapshots(systems_dir, slug, limit=20000):
        mtime = row.get("mtime")
        if mtime is None:
            continue
        if lo is not None and mtime < lo:
            # Newest-first, so everything after this is older still.
            break
        if hi is not None and mtime > hi:
            continue
        if want_cam and str(row.get("camera") or "") != want_cam:
            continue
        found.append(row)

    total = len(found)
    # The NEWEST are kept when there are too many -- what just happened is the
    # part somebody asking on a phone means -- but they are SENT oldest-first,
    # so the album reads in the order the events did.
    want = cap(most)
    keep = found[:want] if newest else found[-want:]
    rows = []
    for row in sorted(keep, key=lambda r: r["mtime"]):
        path = dc.snapshot_file(systems_dir, slug, row["id"])
        if not path:
            continue                    # deleted between listing and sending
        rows.append({**row, "path": path})
    return rows, total


def summary(rows, total, period_text, camera_name=""):
    """The caption: what this album is, and what it is not showing.

    Says out loud when it is a part of something bigger. An album that is
    silently the newest ten of four hundred reads as "there were ten".
    """
    where = f" · {camera_name}" if camera_name else ""
    if not rows:
        return f"No snapshots {period_text}{where}."
    if total > len(rows):
        return (f"{len(rows)} of {total} snapshots {period_text}{where} - the "
                f"newest. For all of them use Footage → Export, which sends "
                f"the originals.")
    if len(rows) > MAX_ALBUM:
        return (f"{len(rows)} snapshots {period_text}{where}, in albums of "
                f"{MAX_ALBUM}.")
    return f"{len(rows)} snapshot{'' if len(rows) == 1 else 's'} {period_text}{where}."
