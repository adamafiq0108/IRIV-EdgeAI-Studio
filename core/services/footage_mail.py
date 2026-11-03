# -*- coding: utf-8 -*-
"""
footage_mail.py -- send a system's snapshots and clips by email.

The Footage page can already show and download footage one file at a time.
This is the other thing people actually do with it: "send me everything from
Tuesday afternoon". Doing that by hand is a download per file and an
attachment per file, which is why it did not get done and why footage stayed
on the box.

WHAT IT SENDS. Either an explicit list of files (what is selected on the page)
or everything in a time range. The range is inclusive of both ends and read
from each file's mtime, which is when the frame was written -- snapshots and
clips carry no other timestamp, and the id is not reliably sortable across the
two kinds.

SIZE IS CHECKED FIRST, AND REFUSED, NOT TRIMMED. Mail servers reject a message
over roughly 25MB, and they do it AFTER the box has spent a minute encoding
attachments. Worse, a partial send looks exactly like a complete one to
whoever receives it. So the total is measured before anything is built, and a
selection that will not fit is refused with the measured size and the limit --
the operator narrows the range or deselects, and knows why.

Base64 inflates attachments by a third; the check accounts for that, because a
19MB selection that becomes a 26MB message is the failure this exists to
prevent.

No framework imports here (docs/conventions.md sec 2).
"""

import io
import json
import os
import secrets
import smtplib
import time
import zipfile
from email.message import EmailMessage
from email.utils import parseaddr

from core.services import mail_accounts as mail_service

#: What a mail server will take, before encoding. Gmail's documented ceiling
#: is 25MB for the whole MESSAGE, so the raw attachment budget has to leave
#: room for base64's 4/3 expansion plus headers -- hence the margin rather
#: than a round 25.
MAX_ATTACH_BYTES = 18 * 1024 * 1024

#: How many files one send may carry, whatever they weigh. A thousand tiny
#: JPEGs is under the size cap and still a message no client will open
#: sensibly, and building it would hold the event loop's thread for minutes.
MAX_FILES = 400

#: Deflate on JPEG and MP4 buys almost nothing -- both are already compressed
#: -- and costs real CPU on a box doing live inference. The zip is for
#: TIDINESS (one attachment instead of two hundred), not for size, so it is
#: stored rather than deflated and says so.
ZIP_COMPRESSION = zipfile.ZIP_STORED

#: A DOWNLOAD is not an email and does not share its budget: nothing has to
#: survive a mail server, so the only real limits are the disk the zip is
#: staged on and the patience of whoever pressed the button. Generous, and
#: still bounded -- an unbounded "zip everything" on a box doing live
#: inference is how a dashboard takes the detection loop down with it.
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
#: The same file-count ceiling as a send, for the same reason: a zip of ten
#: thousand JPEGs takes minutes to write and nobody meant to ask for it.
MAX_DOWNLOAD_FILES = 2000


def human_bytes(n):
    """Sizes appear in refusals, so they have to read like sizes."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def collect(systems_dir, slug, items=None, start=None, end=None, kinds=None):
    """The files to send: [{kind, name, path, bytes, mtime}], oldest first.

    `items` is an explicit selection ([{kind, id}]) -- what the page has
    ticked. Without it, everything whose mtime falls in [start, end]. Both are
    epoch seconds; either may be None for an open end.

    Ordered oldest-first on purpose: a zip that opens in the order things
    happened is one somebody can read, where newest-first (the page's order)
    puts the end of the story at the top.
    """
    from core.services import detector_control as dc

    want_kinds = set(kinds or ("snapshot", "clip"))
    rows = []

    def add(kind, name, path):
        try:
            st = os.stat(path)
        except OSError:
            return                      # deleted between listing and sending
        rows.append({"kind": kind, "name": name, "path": path,
                     "bytes": st.st_size, "mtime": int(st.st_mtime)})

    if items:
        # An explicit selection is NOT filtered by the range: the operator
        # ticked these, and silently dropping one because its clock differs
        # from the range they also had open would be baffling.
        for it in items[:MAX_FILES]:
            if not isinstance(it, dict):
                continue
            kind = str(it.get("kind") or "snapshot")
            ident = str(it.get("id") or "").strip()
            if not ident:
                continue
            if kind == "clip":
                path = dc.clip_path(systems_dir, slug, ident)
                if path:
                    add("clip", ident, path)
            else:
                path = dc.snapshot_file(systems_dir, slug, ident)
                if path:
                    add("snapshot", f"{ident}.jpg", path)
        rows.sort(key=lambda r: r["mtime"])
        return rows

    if "snapshot" in want_kinds:
        for row in dc.list_snapshots(systems_dir, slug, limit=20000):
            path = dc.snapshot_file(systems_dir, slug, row["id"])
            if path and _in_range(row.get("mtime"), start, end):
                add("snapshot", f"{row['id']}.jpg", path)
    if "clip" in want_kinds:
        for row in dc.list_clips(systems_dir, slug, limit=5000):
            path = dc.clip_path(systems_dir, slug, row["name"])
            if path and _in_range(row.get("mtime"), start, end):
                add("clip", row["name"], path)

    rows.sort(key=lambda r: r["mtime"])
    return rows


def _in_range(mtime, start, end):
    if mtime is None:
        return False
    if start is not None and mtime < start:
        return False
    if end is not None and mtime > end:
        return False
    return True


def check(rows, as_zip=True):
    """(ok, problem, total_bytes). Measured BEFORE anything is built.

    A zip of already-compressed footage is not meaningfully smaller than its
    contents, so the same budget applies either way -- promising otherwise
    would move the failure from here to the mail server, which is the whole
    point of checking.
    """
    total = sum(r["bytes"] for r in rows)
    if not rows:
        return False, ("nothing to send - no footage matches that selection"), 0
    if len(rows) > MAX_FILES:
        return False, (f"{len(rows)} files is more than one email can carry "
                       f"(limit {MAX_FILES}) - narrow the range"), total
    if total > MAX_ATTACH_BYTES:
        return False, (f"that selection is {human_bytes(total)}, over the "
                       f"{human_bytes(MAX_ATTACH_BYTES)} an email can carry - "
                       "narrow the date range, or send snapshots and clips "
                       "separately"), total
    del as_zip
    return True, "", total


def check_download(rows):
    """(ok, problem, total_bytes) for a zip the operator will SAVE.

    Its own budget, deliberately: the mail limits exist because a message has
    to cross a mail server, and a file being saved to the machine in front of
    you crosses nothing. Refusing a 40MB download because Gmail would refuse
    it would be a limit borrowed from a different problem.
    """
    total = sum(r["bytes"] for r in rows)
    if not rows:
        return False, "nothing to download - no footage matches that selection", 0
    if len(rows) > MAX_DOWNLOAD_FILES:
        return False, (f"{len(rows)} files is more than one download can carry "
                       f"(limit {MAX_DOWNLOAD_FILES}) - narrow the range or "
                       "tick fewer"), total
    if total > MAX_DOWNLOAD_BYTES:
        return False, (f"that selection is {human_bytes(total)}, over the "
                       f"{human_bytes(MAX_DOWNLOAD_BYTES)} one download may "
                       "carry - narrow the date range"), total
    return True, "", total


def write_zip(rows, path):
    """Write the footage into one zip AT `path`. Returns (ok, problem).

    Staged on disk rather than built in memory: the send path can hold its
    bundle in a BytesIO because a mail attachment is capped at 18MB, and a
    download has no such ceiling -- holding a gigabyte of evidence in RAM on a
    box running inference is how the detector gets killed by the page that was
    only trying to save some photos.

    Stored, not deflated, for the reason ZIP_COMPRESSION gives: JPEG and MP4
    are already compressed, and the CPU belongs to the detection loop.
    """
    try:
        with zipfile.ZipFile(path, "w", ZIP_COMPRESSION, allowZip64=True) as z:
            for r in rows:
                # Foldered by kind, so a bundle of both opens as two folders
                # rather than as one list somebody has to sort by extension.
                z.write(r["path"], arcname=f"{r['kind']}s/{r['name']}")
    except OSError as exc:
        return False, f"could not build the download: {exc}"
    return True, ""


def zip_name(label):
    """What the saved file is called: the system, and when it was taken."""
    return f"{_safe(label)}-footage-{_stamp()}.zip"


def send(systems_dir, slug, rows, to, account_id=None, subject="", body="",
         as_zip=True, config_dir=None, system_name=""):
    """Build and send. Returns (ok, message). Blocking -- call off the loop."""
    recipients = [a for a in
                  ((parseaddr(p.strip())[1] or "")
                   for p in str(to or "").replace(";", ",").split(","))
                  if a]
    if not recipients:
        return False, "no valid recipient addresses"

    acc, problem = mail_service.resolve(account_id, config_dir)
    if problem or not acc:
        return False, problem or "no sending mailbox is configured"
    if not acc.get("app_password"):
        return False, (f"the mailbox \"{acc['name']}\" has no app password - "
                       "set one under Settings → Email")

    ok, problem, total = check(rows, as_zip)
    if not ok:
        return False, problem

    label = system_name or slug
    when = _range_label(rows)
    msg = EmailMessage()
    msg["Subject"] = subject or f"{label} - footage {when}"
    msg["From"] = acc["sender"]
    msg["To"] = ", ".join(recipients)
    # The body always LISTS what is attached, even when the operator wrote
    # their own. A zip called footage.zip tells the recipient nothing about
    # which cameras or which hours it covers, and they cannot ask the box.
    lines = [body.strip()] if body and body.strip() else []
    lines.append(f"{len(rows)} file{'' if len(rows) == 1 else 's'} "
                 f"({human_bytes(total)}) from {label}, {when}.")
    snaps = sum(1 for r in rows if r["kind"] == "snapshot")
    clips = len(rows) - snaps
    if snaps and clips:
        lines.append(f"{snaps} snapshot{'' if snaps == 1 else 's'} and "
                     f"{clips} clip{'' if clips == 1 else 's'}.")
    msg.set_content("\n\n".join(lines) + "\n")

    try:
        if as_zip:
            buf = io.BytesIO()
            # Stored, not deflated: JPEG and MP4 are already compressed, and
            # spending CPU to save a percent on a box running inference is a
            # bad trade. The zip is here to make ONE attachment, not a small
            # one.
            with zipfile.ZipFile(buf, "w", ZIP_COMPRESSION) as z:
                for r in rows:
                    z.write(r["path"], arcname=f"{r['kind']}s/{r['name']}")
            msg.add_attachment(buf.getvalue(), maintype="application",
                               subtype="zip",
                               filename=f"{_safe(label)}-footage-{_stamp()}.zip")
        else:
            for r in rows:
                with open(r["path"], "rb") as fh:
                    data = fh.read()
                if r["kind"] == "clip":
                    msg.add_attachment(data, maintype="video", subtype="mp4",
                                       filename=r["name"])
                else:
                    msg.add_attachment(data, maintype="image", subtype="jpeg",
                                       filename=r["name"])
    except OSError as e:
        return False, f"could not read the footage: {e}"

    try:
        with smtplib.SMTP(acc["smtp_host"], acc["smtp_port"], timeout=120) as s:
            s.ehlo()
            s.starttls()
            s.login(acc["sender"], acc["app_password"])
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return False, ("the mail server refused the mailbox's credentials - "
                       "Gmail needs an App Password, not the account password")
    except smtplib.SMTPDataError as e:
        # The failure this module exists to avoid, so if it still happens the
        # message says what actually went wrong rather than a raw SMTP code.
        return False, (f"the mail server rejected the message ({e.smtp_code}) "
                       "- it is probably too large for the recipient's server; "
                       "try a narrower range")
    except Exception as e:                                  # noqa: BLE001
        return False, f"could not send: {e}"

    return True, (f"sent {len(rows)} file{'' if len(rows) == 1 else 's'} "
                  f"({human_bytes(total)}) to {', '.join(recipients)}")


def _range_label(rows):
    if not rows:
        return "no period"
    first = time.strftime("%Y-%m-%d %H:%M", time.localtime(rows[0]["mtime"]))
    last = time.strftime("%Y-%m-%d %H:%M", time.localtime(rows[-1]["mtime"]))
    return first if first == last else f"{first} to {last}"


def _stamp():
    return time.strftime("%Y%m%d-%H%M")


def _safe(text):
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in str(text)]
    return ("".join(keep).strip("-") or "system")[:40]


#: What a Telegram bot may upload in one document. The API's own ceiling is
#: 50MB for a bot; the margin is for the multipart envelope around it.
MAX_TELEGRAM_BYTES = 45 * 1024 * 1024


def check_telegram(rows):
    """(ok, problem, total_bytes) for a Telegram send.

    A separate budget from the mail one because Telegram is a separate limit,
    and borrowing the mail server's would refuse a 30MB zip that Telegram
    would have taken without complaint.
    """
    if not rows:
        return False, "nothing to send", 0
    total = sum(int(r.get("bytes") or 0) for r in rows)
    if len(rows) > MAX_FILES:
        return False, (f"{len(rows)} files is more than one message should "
                       f"carry ({MAX_FILES}) - narrow the date range"), total
    if total > MAX_TELEGRAM_BYTES:
        return False, (f"{human_bytes(total)} is over Telegram's "
                       f"{human_bytes(MAX_TELEGRAM_BYTES)} limit for one "
                       "file - narrow the date range"), total
    return True, "", total


def send_telegram(systems_dir, slug, rows, chat_id, bot_id=None, caption="",
                  config_dir=None, system_name=""):
    """Post the footage to a Telegram chat as ONE zip. Returns (ok, message).

    Always a zip, never separate files: Telegram uploads one document per
    request, so "separate files" would be two hundred round trips and two
    hundred notifications on somebody's phone. The choice belongs to email,
    where an attachment list is a list; here it would be a denial of service
    on the recipient.

    Blocking -- call it off the event loop.
    """
    from core.services import telegram_bots as bot_service    # noqa: PLC0415

    chat = str(chat_id or "").strip()
    # Checked BEFORE zipping: an id that cannot work should not cost a minute
    # of compression first, and "@someone" is the mistake people arrive with --
    # Telegram answers it with a bare 400 that explains nothing.
    wrong = bot_service.bad_chat(chat)
    if wrong:
        return False, wrong
    bot, problem = bot_service.resolve(bot_id, config_dir)
    if problem or not bot:
        return False, problem or "no Telegram bot is set up"
    token = str(bot.get("token") or "").strip()
    if not token:
        return False, (f"the Telegram bot \"{bot.get('name')}\" has no token - "
                       "set one under Settings → Telegram")

    ok, problem, total = check_telegram(rows)
    if not ok:
        return False, problem

    import tempfile                                           # noqa: PLC0415
    import urllib.request                                     # noqa: PLC0415

    label = system_name or slug
    name = zip_name(label)
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        # Staged on disk rather than built in RAM: a gigabyte of evidence in
        # memory on a box running inference is how a report takes the
        # detection loop down with it.
        built, why = write_zip(rows, path)
        if not built:
            return False, why
        with open(path, "rb") as fh:
            blob = fh.read()
        text = (caption or f"{label} - footage {_range_label(rows)}")[:1024]
        body, ctype = _multipart({"chat_id": chat, "caption": text},
                                 "document", name, blob, "application/zip")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data=body, method="POST", headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                answer = json.loads(resp.read(20000).decode("utf-8", "replace"))
        except Exception as exc:                              # noqa: BLE001
            # urllib renders a refusal as "HTTP Error 400: Bad Request" and
            # discards the body -- and the body is where Telegram says WHY.
            return False, bot_service.explain(exc)
        if not answer.get("ok"):
            return False, bot_service.explain(
                str(answer.get("description") or "Telegram refused it"))
        return True, (f"{len(rows)} file{'' if len(rows) == 1 else 's'} "
                      f"({human_bytes(total)}) posted to Telegram")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _multipart(fields, file_field, filename, blob, content_type):
    """(body, content-type) for one file plus a few text fields."""
    boundary = "----irivai" + secrets.token_hex(8)
    out = []
    for key, value in fields.items():
        out.append(f"--{boundary}\r\n"
                   f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                   f"{value}\r\n".encode("utf-8"))
    out.append(f"--{boundary}\r\n"
               f'Content-Disposition: form-data; name="{file_field}"; '
               f'filename="{filename}"\r\n'
               f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    out.append(blob)
    out.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(out), f"multipart/form-data; boundary={boundary}"
