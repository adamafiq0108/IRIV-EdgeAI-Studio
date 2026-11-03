// First setup, before the first sign-in, in the shape of a phone's: a short
// run of cards, Next on each, Skip where the step is optional.
//
//   Welcome  ->  Your account (name, password)  ->  Recovery email (skippable)
//            ->  Done, and the login takes over.
//
// Nothing here signs anybody in. The password is set through the open
// /api/auth/first-run, which only answers while the box is still on the
// password it shipped with; the sign-in afterwards is an ordinary one. The
// sending mailbox the old Account setup screen asked for is not part of this --
// it is optional and lives under Settings -> Email.
import { useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { PasswordInput } from "./ui/input.jsx";

const STEPS = ["welcome", "account", "recovery", "done"];

export default function FirstRunWizard({ brand, shippedName, onDone }) {
  const [step, setStep] = useState(0);
  // Starts empty on purpose: the shipped name is not offered as an answer.
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [finalName, setFinalName] = useState(shippedName || "");

  function toRecovery(e) {
    e.preventDefault();
    if (!username.trim()) return setError("Choose a username.");
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(username.trim())) {
      return setError("A username is one word: letters, digits and _.");
    }
    if (password.length < 8) return setError("A password is at least 8 characters.");
    if (password !== confirm) return setError("The two passwords do not match.");
    setError(""); setStep(2);
  }

  // Saves the account; the email rides along when it was filled in and is
  // left out on Skip. This is the only call the wizard makes.
  async function save(withEmail) {
    setBusy(true); setError("");
    try {
      const d = await api.post("/api/auth/first-run", {
        username: username.trim(), password,
        email: withEmail ? email.trim() : "",
      });
      setFinalName(d.username || username.trim());
      setStep(3);
    } catch (err) {
      // A 409 means somebody finished setup first: nothing left to do here.
      if (err?.status === 409) { onDone(username.trim()); return; }
      setError(err?.status === 400 || err?.status === 429 ? err.message
               : friendlyError(err));
    } finally { setBusy(false); }
  }

  const dots = (
    <div className="setup-dots" aria-hidden="true"
         style={{ display: "flex", gap: 6, justifyContent: "center", margin: "0 0 14px" }}>
      {STEPS.map((s, i) => (
        <span key={s} style={{
          width: 7, height: 7, borderRadius: "50%",
          background: i === step ? "var(--accent-text, #04bade)" : "var(--border, #888)",
          opacity: i === step ? 1 : 0.5,
        }} />
      ))}
    </div>
  );

  return (
    <div className="login-card" role="group" aria-label="First setup">
      {dots}
      {error && <div className="login-error">{error}</div>}

      {step === 0 && (
        <>
          <div className="login-title">Welcome</div>
          <div className="login-sub">
            Let&rsquo;s set up {brand?.product_name || "your box"}. It takes a minute.
          </div>
          <button className="btn btn-accent login-btn" autoFocus
                  onClick={() => setStep(1)}>Next</button>
        </>
      )}

      {step === 1 && (
        <form onSubmit={toRecovery}>
          <div className="login-title">Set up password</div>
          <div className="login-sub">
            Choose the username and password you will sign in with. Both are
            required; you can change them later under Settings.
          </div>
          <div className="field"><label>Username</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)}
                   placeholder="choose a username"
                   autoComplete="username" required autoFocus /></div>
          <div className="field"><label>Password</label>
            <PasswordInput native value={password} autoComplete="new-password"
                           onChange={(e) => setPassword(e.target.value)} required />
            <span className="muted small">At least 8 characters.</span></div>
          <div className="field"><label>Confirm password</label>
            <PasswordInput native value={confirm} autoComplete="new-password"
                           onChange={(e) => setConfirm(e.target.value)} required /></div>
          <button className="btn btn-accent login-btn" type="submit">Next</button>
          <button type="button" className="btn login-btn" style={{ marginTop: 8 }}
                  onClick={() => { setError(""); setStep(0); }}>Back</button>
        </form>
      )}

      {step === 2 && (
        <form onSubmit={(e) => { e.preventDefault(); save(!!email.trim()); }}>
          <div className="login-title">Recovery email</div>
          <div className="login-sub">
            Where a password-reset link is sent if you ever forget it. You can
            add it later under Settings.
          </div>
          <div className="field"><label>Email <span className="muted">- optional</span></label>
            <input type="email" value={email} placeholder="you@example.com"
                   onChange={(e) => setEmail(e.target.value)} autoFocus /></div>
          <button className="btn btn-accent login-btn" type="submit" disabled={busy}>
            {busy ? "Saving…" : "Next"}
          </button>
          <button type="button" className="btn login-btn" style={{ marginTop: 8 }}
                  disabled={busy} onClick={() => save(false)}>Skip</button>
        </form>
      )}

      {step === 3 && (
        <>
          <div className="login-title">All set</div>
          <div className="login-sub">Sign in as &ldquo;{finalName}&rdquo; to get started.</div>
          <button className="btn btn-accent login-btn" autoFocus
                  onClick={() => onDone(finalName)}>Sign in</button>
        </>
      )}
    </div>
  );
}
