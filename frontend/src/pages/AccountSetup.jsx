// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// First run: name the administrator account, give it a password of your own,
// and (optionally) put a recovery email and a sending mailbox on file.
// Rendered inside the shell (admin only).
//
// The box ships as `iriv` / iriv1234 (scripts/install.sh; `admin` where nothing
// named the account). Both are a STARTING POINT, and this is the screen where
// they stop being one -- leaving a deployment on a name and password that are
// the same on every unit is the realistic way this product gets broken into. Name and password are optional
// (blank keeps what is there) so the page still works later for the email
// alone; changing either asks for the current password, as everywhere else.
//
// ONE exception: while the board is still on the password it shipped with
// (ctx.password_is_default), the password is required here. That string is in
// the README and is also the Wi-Fi AP key, and setup used to be finishable
// with it live as long as a recovery address was filled in.
//
// The recovery email and the sending mailbox are OPTIONAL (2026-10-05): a box
// on a network with no mail relay is a real deployment, and both can be added
// later under Settings. Leaving them blank never brings this screen back
// (routes/auth.py setup_pending is the only gate); half a mailbox is refused
// in plain words, here and on the server, so it is never silently dropped.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { goWithSession, setToken, withSession } from "../lib/session.js";
import { pageUrl } from "../lib/base.js";
import { SkelCard } from "../components/Loading.jsx";
import { PasswordInput } from "../components/ui/input.jsx";
import { GMAIL_APP_PASSWORD_STEPS, appPasswordProblem } from "../lib/appPassword.js";

// The required marker after a label: a red star for the eye, the word for a
// screen reader (the star itself is hidden from it, "star" helps nobody).
function Req() {
  return (<><span className="req" aria-hidden="true">*</span>
    <span className="sr-only"> (required)</span></>);
}

/** All three mailbox fields blank, or an address with its app password;
 *  the reason in plain words otherwise, "" when fine. Same rule as the
 *  server's _check_mailbox -- the owner hears it before the round trip. */
export function mailboxProblem(sender, appPassword, host) {
  if (!(sender || appPassword || host)) return "";
  if (!sender) return "Add the sending address too, or clear the mailbox "
                      + "fields to set it up later.";
  if (!appPassword) return "Add the app password too, or clear the mailbox "
                           + "fields to set it up later.";
  // App passwords only (2026-10-14): a Gmail account password is refused.
  return appPasswordProblem(appPassword, host);
}

export default function AccountSetup() {
  const [ctx, setCtx] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Where finishing lands, same gate the login uses: on a master, the Systems
  // hub. This screen IS a sign-in -- it is where the first one is diverted to
  // -- so finishing it must arrive where a sign-in arrives, and not drop the
  // new administrator inside whichever system's port they happened to open.
  const [hubUrl, setHubUrl] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    api.get("/api/auth/account-setup").then(setCtx).catch((e) => setError(e.message));
    api.get("/api/public").then((d) => setHubUrl(d.hub_url || "")).catch(() => {});
  }, []);

  async function submit(e) {
    e.preventDefault();
    const form = new FormData(e.target);
    const username = String(form.get("username") || "").trim().toLowerCase();
    const password = String(form.get("password") || "");
    const confirm = String(form.get("confirm") || "");
    const renaming = !!username && username !== ctx?.username;
    // A board still on the shipped password does not get to skip this field:
    // that password is printed in the manual and is also the Wi-Fi AP key, so
    // leaving it is leaving the box open to anyone in radio range. The server
    // refuses the same submission -- this is only so the owner hears it
    // before the round trip.
    if (ctx?.password_is_default && !password) {
      return setError("Choose a password: this board is still on the one it "
                      + "shipped with.");
    }
    if (password && password !== confirm) {
      return setError("The two passwords do not match.");
    }
    if (password && password.length < 8) {
      return setError("A password is at least 8 characters.");
    }
    const mailbox = mailboxProblem(
      String(form.get("smtp_sender") || "").trim(),
      String(form.get("smtp_password") || ""),
      String(form.get("smtp_host") || "").trim());
    if (mailbox) return setError(mailbox);
    setBusy(true); setError("");
    try {
      const d = await api.post("/api/auth/account-setup", {
        email: form.get("email"),
        username: renaming ? username : "",
        password,
        current: form.get("current"),
        // The mailbox this box sends FROM. Written only when both halves are
        // present -- see the server: an address with no password cannot
        // authenticate, and half a mailbox fails at the reset itself.
        smtp_sender: form.get("smtp_sender"),
        smtp_password: form.get("smtp_password"),
        smtp_host: form.get("smtp_host"),
      });
      // A rename/password change re-signs the session server-side; this tab
      // adopts the new token before navigating so it does not arrive dead.
      if (d?.token) setToken(d.token);
      // Cross-PORT move, so the identity rides in the fragment -- sessionStorage
      // does not follow to another port.
      // A board that was on the shipped password reloads rather than
      // navigates: the shell it loaded was the setup-only one (AppShell,
      // needs_account_setup) and has to be fetched again to become the box.
      if (hubUrl) goWithSession(withSession(hubUrl));
      else if (renaming || ctx?.password_is_default) window.location.assign(pageUrl("/"));
      else navigate("/", { replace: true });
    } catch (err) { setError(err.message || "Could not save."); setBusy(false); }
  }

  if (!ctx && !error) return <SkelCard rows={3} />;

  // Required exactly while the board is still on the shipped password: the
  // new password, its confirmation and the current one. The name never is.
  const must = !!ctx?.password_is_default;

  // Centred in the content area (setup-page / setup-col, theme.css): this is
  // the only thing on screen at first run, and a form pinned to the left of a
  // wide monitor read as a page that had failed to load the rest.
  return (
    <section className="page active setup-page">
      <div className="setup-col">
      <div className="page-head"><div className="page-title">Account setup</div></div>
      <div className="panel-card">
        {error && <div className="login-error" style={{ marginBottom: 12 }}>{error}</div>}
        <form onSubmit={submit}>
          <div className="acct-h3" style={{ marginTop: 0 }}>Administrator account</div>
          <p className="muted" style={{ marginBottom: 12 }}>
            {/* The shipped NAME is whatever the server says it is ("iriv" from
                the installer, "admin" where nothing set one -- UAT-106), so it
                is quoted from ctx rather than written in. The rename stays
                optional; the password is what this screen is in the way for. */}
            {ctx?.password_is_default && ctx?.is_default_username
              ? `This account is still called “${ctx.username}” and still signs `
                + "in with the password it shipped with - the one printed in "
                + "the manual. Choose your own password to continue; a name of "
                + "your own is recommended too."
              : ctx?.password_is_default
              ? "This board is still signing in with the password it shipped "
                + "with - the one printed in the manual. Choose your own to "
                + "continue."
              : ctx?.is_default_username
              ? `This account is still called “${ctx.username}”, the name every `
                + "box ships with. A name of your own is recommended."
              : "Change the name or password of this account, or leave both "
                + "blank to keep them."}
          </p>
          {must && <p className="req-legend"><span className="req"
            aria-hidden="true">*</span> required</p>}
          <div className="field"><label>Username</label>
            <input name="username" defaultValue={ctx?.username || ""}
                   autoComplete="username" /></div>
          {/* Required exactly while the board is still on the shipped
              password. An owner who has already chosen one is not marched
              through choosing again -- for them this stays "blank to keep". */}
          <div className="field"><label>New password
            {must ? <Req /> : <span className="muted"> - blank to keep</span>}
            </label>
            <PasswordInput native name="password" autoComplete="new-password"
                   required={!!ctx?.password_is_default} />
            {/* The server ends every other session of this account on a new
                password (UAT-108); a colleague commissioning from another
                laptop should not find out by being bounced. */}
            <span className="muted small">
              Saving a new password signs out every other browser that is
              signed in as this account.
            </span></div>
          <div className="field"><label>Confirm new password{must && <Req />}</label>
            <PasswordInput native name="confirm" autoComplete="new-password"
                   required={!!ctx?.password_is_default} /></div>
          <div className="field"><label>Current password{must && <Req />}</label>
            <PasswordInput native name="current" autoComplete="current-password"
                   required={!!ctx?.password_is_default} />
            <span className="muted small">
              Needed only if you change the username or the password.
            </span></div>

          {/* Optional. The warning keeps its meaning in plain words: with no
              address, "forgot password" has nowhere to send the link. */}
          <div className="acct-h3">Recovery <span className="muted">- optional</span></div>
          <p className="muted" style={{ marginBottom: 12 }}>
            You can leave this blank and add it later under
            Settings → Accounts. Without a recovery email, a lost password
            cannot be reset by email.
          </p>
          <div className="field"><label>Recovery email</label>
            <input type="email" name="email" defaultValue={ctx?.current || ""}
                   placeholder="admin@example.com" />
            <span className="muted small">
              Where a password-reset link is sent.
            </span></div>

          {/* The SENDING mailbox, on this screen and not a later one.
              A recovery address with nothing able to mail it is a lock-out
              that looks like a working setup -- which is exactly what the old
              "SMTP isn't configured yet" note described and then left for
              somebody to fix another day. Asking here costs two fields at the
              moment the operator is already thinking about being locked out.

              Optional: a box on a network with no mail relay is a real
              deployment, and it must not be blocked from finishing setup.
              Started but unfinished is refused (mailboxProblem). */}
          <div className="acct-h3">Sending mailbox <span className="muted">- optional</span></div>
          <p className="muted" style={{ marginBottom: 12 }}>
            {ctx?.smtp_ready
              ? "A mailbox is already set up. Fill these in to replace it, or "
                + "leave them blank."
              : "The account this box SENDS from - the reset link above cannot "
                + "be delivered without one. It is also what Workflow Email "
                + "nodes and Footage use. Leave all three blank to skip it "
                + "for now and add it later under Settings → Email. If "
                + "you fill it in, the address and app password go together."}
          </p>
          <div className="field"><label>Sending address</label>
            <input type="email" name="smtp_sender"
                   placeholder="alerts@example.com" /></div>
          <div className="field"><label htmlFor="setup-app-password">App Password</label>
            <PasswordInput native name="smtp_password" id="setup-app-password"
                   autoComplete="new-password"
                   placeholder="16-character app password" />
            <span className="muted small" data-setup-pw-help="">
              Use an app password, not your normal email password. Your email
              provider creates it in the account&apos;s security settings (for
              Gmail: Google Account &gt; Security &gt; 2-Step Verification &gt;
              App passwords). For another server, use the SMTP password your
              provider tells you to use for apps.
            </span>
            <details className="muted small" data-setup-pw-howto="">
              <summary style={{ cursor: "pointer" }}>How to create one (Gmail)</summary>
              <ol style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {GMAIL_APP_PASSWORD_STEPS.map((step) => <li key={step}>{step}</li>)}
              </ol>
            </details></div>
          <div className="field"><label>SMTP server <span className="muted">- blank for Gmail</span></label>
            <input name="smtp_host" placeholder="smtp.gmail.com" /></div>
          <button className="btn btn-accent" type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save & continue"}
          </button>
        </form>
      </div>
      </div>
    </section>
  );
}
