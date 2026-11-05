// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Standalone login card. Brand comes from the open
// /api/public; submit posts JSON to /api/auth/login and routes on success.
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, friendlyError } from "../api/client.js";
import { goWithSession, setToken, withSession } from "../lib/session.js";
import { applyBrand } from "../state/app.jsx";
import ThemeToggle from "../components/ThemeToggle.jsx";
import { PasswordInput } from "../components/ui/input.jsx";
import { apiUrl } from "../lib/base.js";
import FirstRunWizard from "../components/FirstRunWizard.jsx";

export default function Login() {
  const [brand, setBrand] = useState({});
  const [hubUrl, setHubUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "/";

  // null until asked. A box still on its shipped password shows the first-run
  // wizard instead of the sign-in; a failed ask falls through to the sign-in.
  const [first, setFirst] = useState(null);
  const [justSetUp, setJustSetUp] = useState("");

  useEffect(() => {
    api.get("/api/public").then((d) => {
      setBrand(d.brand || {}); applyBrand(d.brand); setHubUrl(d.hub_url || "");
    }).catch(() => {});
    api.get("/api/auth/first-run").then(setFirst)
      .catch(() => setFirst({ pending: false }));
  }, []);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError("");
    const form = new FormData(e.target);
    try {
      const d = await api.post("/api/auth/login", {
        username: form.get("username"), password: form.get("password"),
      });
      // THIS TAB is now this user, whatever other tabs are signed in as.
      if (d.token) setToken(d.token);
      if (d.needs_account_setup) { navigate("/account-setup", { replace: true }); return; }
      // Landing gate: EVERY sign-in on a master goes to the Systems hub (port
      // 5100 unless a deployment says otherwise), deep link or not. Signing in
      // is arriving at the box, and you arrive at the grid -- a system page is
      // somewhere you go from there. A saved link to one master's page used to
      // survive the login and drop the operator inside a single system with no
      // sense of what else was on the board.
      //
      // The hub returns an empty hub_url, so there this is inert and its own
      // deep links (all on 5100 already) are honoured as before.
      //
      // Preferred from the LOGIN response, falling back to the /api/public
      // fetch: that fetch is a second request, and a slow or failed one left
      // hubUrl empty at submit time -- which read as "stay on this master".
      const hub = d.hub_url || hubUrl;
      // Crossing to the hub is a cross-PORT move: sessionStorage does not
      // follow, so the identity travels in the link's fragment.
      if (hub) { goWithSession(withSession(hub)); return; }
      navigate(next, { replace: true });
    } catch (err) {
      // Only a 401 is a wrong username or password (UAT-118). A box that did
      // not answer, a rate limit or a server fault each read as a typo before,
      // and the only fix offered for all of them was "type it again".
      // The 429 keeps the server's own sentence: it says how long to wait.
      // So does the device limit (a 403 tagged device_limit): the password
      // was right, this browser is an 11th device, and the sentence says
      // where to remove one -- "You don't have access" would send the
      // operator to retype a password that was never the problem.
      setError(err?.status === 401 ? "Wrong username or password."
               : err?.status === 429 && err.message ? err.message
               : err?.status === 403 && err?.data?.device_limit && err.message
                 ? err.message
               : friendlyError(err));
      setBusy(false);
    }
  }

  if (!first) return <div className="login-wrap"><ThemeToggle /></div>;
  if (first.pending) {
    return (
      <div className="login-wrap">
        <ThemeToggle />
        <FirstRunWizard brand={brand} shippedName={first.username}
          onDone={(name) => { setJustSetUp(name); setFirst({ pending: false }); }} />
      </div>
    );
  }

  return (
    <div className="login-wrap">
      <ThemeToggle />
      <form className="login-card" onSubmit={submit}>
        <img className="login-logo" src={apiUrl("/static/img/cytron.png")} alt="" />
        <div className="login-title">{brand.product_name || "IRIV EdgeAI Studio"}</div>
        <div className="login-sub">{brand.brand_sub || ""}</div>
        {error && <div className="login-error" role="alert">{error}</div>}
        {/* Placeholders name the FIELD, not an example account: "admin" in
            the box taught every installer that admin is the login. */}
        <div className="field"><label>Username</label>
          <input type="text" name="username" placeholder="username"
                 defaultValue={justSetUp} autoComplete="username" required
                 autoFocus={!justSetUp} /></div>
        <div className="field"><label>Password</label>
          {/* The toggle is type="button", so Enter still submits the form. */}
          <PasswordInput native name="password" placeholder="password"
                         shown={showPw} onToggle={() => setShowPw((v) => !v)}
                         autoComplete="current-password" required
                         autoFocus={!!justSetUp} />
        </div>
        <button className="btn btn-accent login-btn" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <a className="login-forgot" href="/forgot">Forgot password?</a>
      </form>
    </div>
  );
}
