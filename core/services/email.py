# -*- coding: utf-8 -*-
"""
email.py -- The Email page's behaviour: config, digest assembly, send.

Config lives in config/email.local.toml (gitignored -- the app password is a
credential and never returns to the browser unmasked). Schedule times are a
comma string because the local TOML writer handles flat scalars only.

The digest covers "today so far": the standard *_today stats keys are already
day-so-far totals at the last poll, which is exactly the window a timer like
12:00 means (midnight to send time). The chart prefers the window's own events
bucketed by hour; a system that ships totals instead of events (no rows to
bucket) falls back to its violations_by_day trend, labelled as such.

No framework imports here (docs/conventions.md sec 2).
"""

import base64
import datetime
import html
import io
import logging
import pathlib
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr

from core import config

logger = logging.getLogger(__name__)

_REPORT_TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "templates" / "report.html"

DEFAULTS = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "sender": "",
    "app_password": "",
    "recipients": "",
    "subject": "Daily compliance summary",
    "body": ("Compliance summary for @date.\n\n"
             "Violations in this period: @violations\n"
             "People detected: @persons\n"
             "Average compliance: @average\n\n"
             "Full details on the dashboard."),
    "include_violations": True,
    "include_persons": True,
    "include_average": True,
    "include_chart": True,
    "include_pdf": True,
    "times": "",           # "12:00,18:00" -- daily send times, local clock
    "enabled": False,
}


def _name(slug):
    """The local config file for one system's email settings.

    Every system owns its whole email config -- its own sender account, its own
    recipients and schedule -- so two masters never send each other's digest.
    `email_<slug>.local.toml`, one per system. slug=None keeps the old flat
    `email.local.toml`: the only caller that passes None is a hub with no
    systems, and it only reads it for the password-reset sender.
    """
    return f"email_{slug}" if slug else "email"


def load(slug, config_dir=None):
    return {**DEFAULTS, **config.load_local(_name(slug), config_dir)}


def save(data, slug, config_dir=None):
    """Merge onto the existing local file. A masked password never overwrites
    the real one -- the browser only ever sees the mask."""
    current = {**DEFAULTS, **config.load_local(_name(slug), config_dir)}
    for key, value in data.items():
        if key not in DEFAULTS:
            continue
        if key == "app_password" and (not value or set(value) == {"*"}):
            continue
        if key in ("include_violations", "include_persons", "include_average",
                   "include_chart", "include_pdf", "enabled"):
            value = bool(value)
        if key == "smtp_port":
            value = int(value or 587)
        current[key] = value
    config.save_local(_name(slug), current, config_dir)


def public(slug, config_dir=None):
    cfg = dict(load(slug, config_dir))
    cfg["app_password"] = config.mask(cfg.get("app_password"))
    return cfg


def parse_times(cfg):
    """Validated HH:MM list from the comma string, bad entries dropped."""
    out = []
    for raw in (cfg.get("times") or "").split(","):
        raw = raw.strip()
        try:
            out.append(datetime.datetime.strptime(raw, "%H:%M").time())
        except ValueError:
            continue
    return out


def recipients_list(cfg):
    return [r.strip() for r in (cfg.get("recipients") or "").split(",")
            if parseaddr(r.strip())[1]]


# -- digest data --------------------------------------------------------------

def build_digest(stats, events, now=None):
    """Everything one send needs, computed once so the body, the chart and the
    PDF can never disagree about the numbers."""
    now = now or datetime.datetime.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    violations = int(stats.get("violations_today") or 0)
    persons = int(stats.get("events_today") or 0)
    average = round(100 * (1 - violations / persons)) if persons else None

    # Window events, hour-bucketed, for systems that stream rows.
    hours = {}
    for e in events:
        ts = str(e.get("ts") or "")
        if ts[:10] == day_start.strftime("%Y-%m-%d"):
            hours[ts[11:13]] = hours.get(ts[11:13], 0) + 1

    return {
        "date": now.strftime("%Y-%m-%d"),
        "window": f"{day_start.strftime('%H:%M')}–{now.strftime('%H:%M')}",
        "violations": violations,
        "persons": persons,
        "average": f"{average}%" if average is not None else "-",
        "hours": hours,
        "by_day": dict(stats.get("violations_by_day") or {}),
    }


def render_body(template, digest):
    return (template
            .replace("@violations", str(digest["violations"]))
            .replace("@persons", str(digest["persons"]))
            .replace("@average", digest["average"])
            .replace("@date", f"{digest['date']} ({digest['window']})"))


# -- chart + report -----------------------------------------------------------

def _chart_figure(digest, title_prefix):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=150)
    if digest["hours"]:
        labels = [f"{h:02d}" for h in range(24)]
        values = [digest["hours"].get(h, 0) for h in labels]
        ax.plot(labels, values, color="#ff4d6d", linewidth=2)
        ax.fill_between(labels, values, color="#ff4d6d", alpha=0.08)
        ax.set_title(f"{title_prefix} - events by hour, {digest['date']} {digest['window']}",
                     fontsize=10)
    else:
        days = sorted(digest["by_day"])[-14:]
        ax.bar([d[5:] for d in days], [digest["by_day"][d] for d in days],
               color="#1d4ed8", alpha=0.85)
        ax.set_title(f"{title_prefix} - violations per day (daily trend)", fontsize=10)
    ax.tick_params(labelsize=8)
    ax.grid(color="#e2e8f0", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    return fig


def chart_jpeg(digest, title_prefix):
    import matplotlib.pyplot as plt
    fig = _chart_figure(digest, title_prefix)
    buf = io.BytesIO()
    fig.savefig(buf, format="jpeg", pil_kwargs={"quality": 90})
    plt.close(fig)
    return buf.getvalue()


def _render_report_html(digest, system_name, cfg):
    """Fill the print template (core/templates/report.html) for one send.

    Same @token scheme as render_body -- the service stays framework-free, no
    Jinja. Every value is HTML-escaped: the numbers are trusted, but the brand
    name and system name come from config a deployment edits, and a stray '<'
    there must not break the document.
    """
    brand = config.brand()
    chart = base64.b64encode(chart_jpeg(digest, system_name)).decode("ascii")
    tokens = {
        "@accent": brand.get("accent") or "#17a2c4",
        "@brand_name": brand.get("product_name") or "Compliance",
        "@system_name": system_name,
        "@report_no": f"CR-{digest['date'].replace('-', '')}",
        "@date": digest["date"],
        "@window": digest["window"],
        "@violations": str(digest["violations"]),
        "@persons": str(digest["persons"]),
        "@average": digest["average"],
        # data: URI so the one HTML string carries its own chart -- WeasyPrint
        # fetches nothing over the network.
        "@chart": f"data:image/jpeg;base64,{chart}",
    }
    out = _REPORT_TEMPLATE.read_text(encoding="utf-8")
    for token, value in tokens.items():
        # @chart is already a safe data URI; escaping its base64 would corrupt it.
        out = out.replace(token, value if token == "@chart" else html.escape(str(value)))
    return out


def report_pdf(digest, system_name, cfg):
    """One-page PDF. The styled HTML template via WeasyPrint when it is
    installed; the matplotlib fallback otherwise.

    WeasyPrint is a heavy, system-library dependency (pango, cairo) that a Pi
    may not have. A report that only changes appearance must never be the reason
    a digest fails to send, so an install without it degrades to the plain
    matplotlib page rather than raising.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:
        logger.info("[EMAIL] WeasyPrint unavailable (%s); using matplotlib report", e)
        return _report_pdf_matplotlib(digest, system_name, cfg)

    html_str = _render_report_html(digest, system_name, cfg)
    return HTML(string=html_str, base_url=str(_REPORT_TEMPLATE.parent)).write_pdf()


def _report_pdf_matplotlib(digest, system_name, cfg):
    """One page, always: header, the period, the chosen numbers, the chart."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(8.27, 11.69), dpi=120)   # A4 portrait
    fig.text(0.08, 0.95, f"{system_name} - Compliance Report",
             fontsize=16, fontweight="bold")
    fig.text(0.08, 0.925, f"Period: {digest['date']}  {digest['window']}   "
             f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
             fontsize=9, color="#555555")

    y = 0.86
    rows = []
    if cfg.get("include_violations"):
        rows.append(("Violations", str(digest["violations"])))
    if cfg.get("include_persons"):
        rows.append(("People detected", str(digest["persons"])))
    if cfg.get("include_average"):
        rows.append(("Average compliance", digest["average"]))
    for label, value in rows:
        fig.text(0.08, y, label, fontsize=11, color="#555555")
        fig.text(0.55, y, value, fontsize=11, fontweight="bold")
        y -= 0.035

    chart = _chart_figure(digest, system_name)
    buf = io.BytesIO()
    chart.savefig(buf, format="png")
    plt.close(chart)
    buf.seek(0)
    img = plt.imread(buf)
    ax = fig.add_axes([0.08, y - 0.42, 0.84, 0.38])
    ax.imshow(img)
    ax.axis("off")

    out = io.BytesIO()
    fig.savefig(out, format="pdf")
    plt.close(fig)
    return out.getvalue()


def send_plain(cfg, to_list, subject, body):
    """One text-only mail through the configured sender. Used by password
    recovery; returns (ok, message)."""
    if not cfg.get("sender") or not cfg.get("app_password"):
        return False, "sender email and app password are not configured on the Email page"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(to_list)
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg.get("smtp_host") or "smtp.gmail.com",
                          int(cfg.get("smtp_port") or 587), timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.login(cfg["sender"], cfg["app_password"])
            s.send_message(msg)
        return True, "sent"
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed - check the sender email and app password"
    except (smtplib.SMTPException, OSError) as e:
        return False, f"SMTP error: {e}"


# -- send ---------------------------------------------------------------------

def send(cfg, digest, system_name):
    """Send one digest. Returns (ok, message)."""
    if not cfg.get("sender") or not cfg.get("app_password"):
        return False, "sender email and app password are not configured"
    to = recipients_list(cfg)
    if not to:
        return False, "no valid recipient emails configured"

    msg = EmailMessage()
    msg["Subject"] = cfg.get("subject") or DEFAULTS["subject"]
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(to)
    msg.set_content(render_body(cfg.get("body") or DEFAULTS["body"], digest))

    try:
        if cfg.get("include_chart"):
            msg.add_attachment(chart_jpeg(digest, system_name),
                               maintype="image", subtype="jpeg",
                               filename=f"chart-{digest['date']}.jpg")
        if cfg.get("include_pdf"):
            msg.add_attachment(report_pdf(digest, system_name, cfg),
                               maintype="application", subtype="pdf",
                               filename=f"report-{digest['date']}.pdf")
    except Exception as e:
        logger.exception("[EMAIL] attachment build failed")
        return False, f"could not build attachments: {e}"

    try:
        with smtplib.SMTP(cfg.get("smtp_host") or "smtp.gmail.com",
                          int(cfg.get("smtp_port") or 587), timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.login(cfg["sender"], cfg["app_password"])
            s.send_message(msg)
        logger.info("[EMAIL] sent to %s", to)
        return True, f"sent to {', '.join(to)}"
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed - check the sender email and app password"
    except (smtplib.SMTPException, OSError) as e:
        return False, f"SMTP error: {e}"


# -- deferred send (the undo window) ------------------------------------------
#
# A manual "Send now" goes out after a short delay instead of immediately, and
# stays cancellable for the whole delay. Mail is the one action in this product
# with NO undo once it leaves -- a wrong recipient or a half-written body is
# gone the moment SMTP accepts it -- so the regret window has to exist BEFORE
# the send rather than as an apology after it.
#
# A timer thread, not an asyncio task: send() is blocking SMTP work that
# already runs off the loop, and a thread keeps the pending send alive
# independently of whichever request created it.

#: id -> {"timer", "at", "to", "cancelled"}. Process-local: a master restart
#: during the window drops the send, which is the safe direction to fail.
_pending_sends = {}
_send_seq = 0

#: How long "Send now" waits. Long enough to notice a wrong recipient and
#: click Undo, short enough that nobody thinks it failed.
SEND_DELAY_S = 10.0


def schedule_send(cfg, digest, system_name, delay_s=None, on_done=None):
    """Queue one digest to go out after `delay_s`, cancellable until then.

    Returns {"id", "send_at", "to"} -- the id is what cancel_send needs, and
    the recipients come back so the page can show WHO it is about to mail
    while the operator still has the chance to stop it.
    """
    import threading
    import time as _time

    global _send_seq
    delay = SEND_DELAY_S if delay_s is None else max(0.0, float(delay_s))
    to = recipients_list(cfg)
    _send_seq += 1
    sid = f"send{_send_seq}"
    entry = {"at": _time.time() + delay, "to": to, "cancelled": False,
             "timer": None}

    def _fire():
        # Re-checked under no lock on purpose: cancel() only ever sets this
        # flag, and Timer.cancel() already prevents the common case. The flag
        # closes the race where cancel lands while the thread is starting.
        if entry["cancelled"]:
            return
        _pending_sends.pop(sid, None)
        try:
            ok, message = send(cfg, digest, system_name)
        except Exception as exc:                             # noqa: BLE001
            ok, message = False, str(exc)
            logger.exception("[EMAIL] deferred send failed")
        if on_done:
            try:
                on_done(ok, message)
            except Exception:                                # noqa: BLE001
                logger.exception("[EMAIL] send callback failed")

    entry["timer"] = threading.Timer(delay, _fire)
    entry["timer"].daemon = True
    _pending_sends[sid] = entry
    entry["timer"].start()
    return {"id": sid, "send_at": entry["at"], "to": to, "delay_s": delay}


def cancel_send(send_id):
    """Stop a queued send. True when it was still stoppable.

    False is not an error worth shouting about -- it means the window closed
    and the mail has gone, which the page reports as "too late" rather than as
    a failure.
    """
    entry = _pending_sends.pop(str(send_id or ""), None)
    if not entry:
        return False
    entry["cancelled"] = True
    try:
        entry["timer"].cancel()
    except Exception:                                        # noqa: BLE001
        pass
    return True
