# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""Mail a password-reset link -- the part of the forgot flow with no request.

Two callers: /api/auth/forgot (a person typed their address) and the front
panel (somebody held the USER button five seconds at the box). The second
one has no HTTP request to read a base URL or a Host from, so what the link
points at has to come from the box's own configuration, which is what
`base_url` here does.

The master's address is the only one the panel ever mails. Holding a
physical button is proof of being AT the box, which is the same standing as
an owner locked out of it -- and nobody above master exists to approve.
"""
from core import config
from core.services import auth as auth_service
from core.services import email as email_service


def sender(cfg, fallback_slug=None):
    """The SMTP settings a reset email goes out through.

    The box's default mailbox first (Settings > Email), then the borrowed
    per-system config that used to be the only option -- `fallback_slug` is
    the system whose sender the caller would borrow, None when there is none.
    """
    try:
        from core.services import mail_accounts as mail_service  # noqa: PLC0415
        acc, _problem = mail_service.resolve("", cfg)
        if acc and acc.get("app_password"):
            return {"sender": acc["sender"], "app_password": acc["app_password"],
                    "smtp_host": acc["smtp_host"], "smtp_port": acc["smtp_port"]}
    except Exception:                                       # noqa: BLE001
        pass
    return email_service.load(fallback_slug, cfg)


def base_url(cfg, fallback=""):
    """Where an emailed link points when no request says otherwise.

    The configured public URL if the deployment set one, else the address
    the front panel already tells people to type (panel.dashboard_url), else
    `fallback`. A scheme is added when the value has none.
    """
    configured = str((config.auth(cfg).get("session", {}) or {})
                     .get("public_url") or "").strip()
    url = configured or str(fallback or "").strip()
    if url and "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


def send(cfg, username, link_base, fallback_slug=None):
    """Mint this account's reset token and mail the link to its address.
    Returns (ok, message). Synchronous."""
    to = auth_service.user_email(username, cfg)
    if not to:
        return False, "that account has no email on file"
    if not link_base:
        return False, "no address to point the link at (set the dashboard URL)"
    token = auth_service.issue_reset_token(cfg, username)
    link = f"{link_base}/reset-password?token={token}"
    body = (f"A password reset was requested for the account '{username}' on "
            "this dashboard.\n\n"
            f"Open this link to set a new password (valid 30 minutes):\n{link}\n\n"
            "If you did not request this, ignore this email and tell your "
            "administrator.")
    return email_service.send_plain(sender(cfg, fallback_slug), [to],
                                    f"Dashboard password reset for {username}", body)
