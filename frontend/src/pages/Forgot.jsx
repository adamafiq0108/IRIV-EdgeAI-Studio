// Forgot password: two ways back in.
//
// BY EMAIL: type the address on your account. The server says the SAME thing
// whether the address matched or not, so this page cannot be used to learn
// who has an account. For most accounts the next step is an administrator
// approving the request from their Inbox; master's link is mailed at once.
//
// AT THE BOX (master only): hold the USER button on the front panel for five
// seconds and a six-digit code appears on its screen for ten minutes. Typed
// here with a new password it resets master -- no email, no mailbox needed,
// which is the case this exists for: a box with no address on file still
// has an owner, and the owner is the one with their hand on the hardware.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import ThemeToggle from "../components/ThemeToggle.jsx";
import { applyBrand } from "../state/app.jsx";
import { PasswordInput } from "../components/ui/input.jsx";
import { apiUrl } from "../lib/base.js";

export default function Forgot() {
  const [brand, setBrand] = useState({});
  const [mode, setMode] = useState("email");     // email | box
  const [emailOff, setEmailOff] = useState(false);
  // Which way back in this box HAS is not known until /api/public answers, and
  // guessing costs a visible flash: the email form paints, then swaps itself
  // for the code screen a moment later. Nothing below renders until the answer
  // is in -- on a box this is a same-host call, so the wait is a frame or two.
  const [ready, setReady] = useState(false);
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState("");
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    api.get("/api/public").then((d) => {
      setBrand(d.brand || {}); applyBrand(d.brand);
      // A box that does not use email has nothing to do with an address, so
      // the screen does not ask for one: it opens on the way back in that
      // this box actually has. Asking anyway would be a field that accepts
      // what you type and then answers "if that address is on an account
      // here..." about a box with no mail path at all.
      if (d.email === false) { setEmailOff(true); setMode("box"); }
    }).catch(() => {})
      // Ready either way: if the call failed we cannot know, and a screen that
      // never opens is worse than one that opens on the email path -- which is
      // what every box does unless it says otherwise.
      .finally(() => setReady(true));
  }, []);

  async function submitEmail(e) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const d = await api.post("/api/auth/forgot", { email: email.trim() });
      setSent(d.message || "If that email is on an account here, the next step is on its way.");
    } catch (err) { setError(err.message || "Could not send the request."); }
    finally { setBusy(false); }
  }

  async function submitCode(e) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await api.post("/api/auth/panel-reset", { code: code.replace(/\D/g, ""), password: pw });
      navigate("/login", { replace: true });
    } catch (err) { setError(err.message || "Could not reset the password."); }
    finally { setBusy(false); }
  }

  const valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const digits = code.replace(/\D/g, "");
  const codeReady = digits.length === 6 && pw.length >= 8 && pw === pw2;

  const tab = (id, label) => (
    <button type="button" onClick={() => { setMode(id); setError(""); }}
            className={"login-tab" + (mode === id ? " is-on" : "")}
            aria-selected={mode === id} role="tab">
      {label}
    </button>
  );

  return (
    <div className="login-wrap">
      <ThemeToggle />
      <form className="login-card" onSubmit={mode === "email" ? submitEmail : submitCode}>
        <img className="login-logo" src={apiUrl("/static/img/cytron.png")} alt="" />
        <div className="login-title">Reset password</div>
        <div className="login-sub">{brand.brand_sub || ""}</div>

        {!ready ? (
          /* Held, not guessed. The card keeps its size so the logo and title
             do not jump when the real form arrives underneath them. */
          <div aria-busy="true" aria-label="Loading"
               style={{ display: "grid", gap: 10, margin: "4px 0 2px" }}>
            <div className="skel" style={{ height: 34 }} />
            <div className="skel" style={{ height: 12, width: "92%" }} />
            <div className="skel" style={{ height: 12, width: "70%" }} />
            <div className="skel" style={{ height: 38, marginTop: 6 }} />
            <div className="skel" style={{ height: 38 }} />
          </div>
        ) : sent ? (
          <>
            <div className="login-ok">
              <strong>Request sent.</strong> {sent}
            </div>
            {/* What happens next, in order: the operator is about to walk
                away from this screen and wait, and this is the one place
                that can say what they are waiting for. */}
            <ol className="login-text" style={{ paddingLeft: 18, margin: "0 0 4px" }}>
              <li>An administrator sees the request in their Inbox and approves it.</li>
              <li>A reset link arrives at <strong>{email.trim()}</strong>.</li>
              <li>Open it and choose a new password.</li>
            </ol>
            <a className="btn login-btn" href="/login" style={{ marginTop: 12 }}>Back to sign in</a>
          </>
        ) : (
          <>
            {/* One way back in needs no chooser. */}
            {!emailOff && (
              <div className="login-tabs" role="tablist">
                {tab("email", "By email")}
                {tab("box", "At the box (master)")}
              </div>
            )}
            {error && <div className="login-error">{error}</div>}

            {mode === "email" ? (
              <>
                <p className="login-text">
                  Type the email address on your account and we will send a
                  link to choose a new password. An administrator may need to
                  approve the request first.
                </p>
                <div className="field"><label>Email</label>
                  <input type="email" value={email} placeholder="you@example.com"
                         autoComplete="email" autoFocus required
                         onChange={(e) => setEmail(e.target.value)} /></div>
                <button className="btn btn-accent login-btn" type="submit"
                        disabled={busy || !valid}>
                  {busy ? "Sending…" : "Send reset link"}
                </button>
              </>
            ) : (
              <>
                <p className="login-text">
                  For the <strong>master</strong> account, with no email needed.
                  Hold the <strong>USER</strong> button on the box for
                  5&nbsp;seconds: a 6-digit code appears on its screen for
                  10&nbsp;minutes. Type it here with a new password.
                </p>
                <div className="field"><label>Code on the screen</label>
                  <input inputMode="numeric" autoComplete="one-time-code" autoFocus
                         value={code} placeholder="123 456" maxLength={7}
                         className="login-code"
                         onChange={(e) => {
                           const d = e.target.value.replace(/\D/g, "").slice(0, 6);
                           setCode(d.length > 3 ? `${d.slice(0, 3)} ${d.slice(3)}` : d);
                         }} /></div>
                <div className="field"><label>New master password</label>
                  <PasswordInput native value={pw}
                                 shown={show} onToggle={() => setShow((v) => !v)}
                                 autoComplete="new-password" minLength={8}
                                 placeholder="at least 8 characters"
                                 onChange={(e) => setPw(e.target.value)} />
                </div>
                <div className="field"><label>Repeat new password</label>
                  <PasswordInput native value={pw2}
                                 shown={show} onToggle={() => setShow((v) => !v)}
                                 autoComplete="new-password"
                                 onChange={(e) => setPw2(e.target.value)} /></div>
                {pw2 && pw !== pw2 && (
                  <div className="login-text" style={{ color: "var(--alert)" }}>The two passwords differ.</div>
                )}
                <button className="btn btn-accent login-btn" type="submit"
                        disabled={busy || !codeReady}>
                  {busy ? "Saving…" : "Set master password"}
                </button>
              </>
            )}
            <a className="login-forgot" href="/login">Back to sign in</a>
          </>
        )}
      </form>
    </div>
  );
}
