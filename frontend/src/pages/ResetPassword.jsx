// Set a new master password from an emailed link.
// The token in the URL is validated up front; a bad/expired token disables the form.
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import ThemeToggle from "../components/ThemeToggle.jsx";
import { PasswordInput } from "../components/ui/input.jsx";
import { apiUrl } from "../lib/base.js";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [valid, setValid] = useState(null);
  const [who, setWho] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [show, setShow] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    api.get(`/api/auth/reset?token=${encodeURIComponent(token)}`)
      .then((d) => { setValid(!!d.valid); setWho(d.username || ""); }).catch(() => setValid(false));
  }, [token]);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError("");
    const form = new FormData(e.target);
    try {
      await api.post("/api/auth/reset", { token, password: form.get("password") });
      navigate("/login", { replace: true });
    } catch (err) {
      setError(err.message || "Could not reset the password.");
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <ThemeToggle />
      <form className="login-card" onSubmit={submit}>
        <img className="login-logo" src={apiUrl("/static/img/cytron.png")} alt="" />
        <div className="login-title">Set a new password</div>
        {/* The link is for ONE account now; say which, so a link opened from
            the wrong mailbox is caught before a password is typed. */}
        {valid && who && <div className="login-sub">for the account <strong>{who}</strong></div>}
        {valid === false && <div className="login-error">This reset link is invalid or has expired.</div>}
        {error && <div className="login-error">{error}</div>}
        {valid === null && <p className="login-text">Checking the link…</p>}
        {valid && (
          <>
            <div className="field"><label>New password</label>
              <PasswordInput native name="password" value={pw}
                             shown={show} onToggle={() => setShow((v) => !v)}
                             autoComplete="new-password" required autoFocus minLength={8}
                             placeholder="at least 8 characters"
                             onChange={(e) => setPw(e.target.value)} />
            </div>
            {/* Typed twice: this is the one password screen with no "current
                password" to fall back on if a typo goes through. */}
            <div className="field"><label>Repeat new password</label>
              <PasswordInput native value={pw2} autoComplete="new-password"
                             shown={show} onToggle={() => setShow((v) => !v)}
                             required onChange={(e) => setPw2(e.target.value)} /></div>
            {pw2 && pw !== pw2 && (
              <div className="login-text" style={{ color: "var(--alert)" }}>The two passwords differ.</div>
            )}
            <button className="btn btn-accent login-btn" type="submit"
                    disabled={busy || pw.length < 8 || pw !== pw2}>
              {busy ? "Saving…" : "Save password"}
            </button>
          </>
        )}
        {valid === false && (
          <a className="btn login-btn" href="/forgot">Request a new link</a>
        )}
        <a className="login-forgot" href="/login">Back to sign in</a>
      </form>
    </div>
  );
}
