// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The Monitor account's two panels.
//
//   MonitorSwitchCard  System settings (master and developer): the switch, the
//                      per-device access tokens (the only way the account
//                      signs in), and the box's MAC address(es) a program
//                      can save next to a token.
//   MonitorAccess      Settings > Accounts > Monitor (site admin and up):
//                      which pages and which systems it may open.
//
// What the account may DO is fixed (start and stop detection, download
// footage) and is only described here, never ticked: see
// core/services/monitor_account.py.
import { useCallback, useEffect, useState } from "react";
import { Boxes, Check, Download, FileDown, Layers, ListChecks, Lock, Play,
         TriangleAlert } from "lucide-react";
import { api } from "../api/client.js";
import { apiUrl } from "../lib/base.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { Button } from "./ui/button.jsx";
import { PAGE_ICONS, SECTIONS } from "./nav-sections.js";
import { CardHeader, StateSwitch } from "./ui/CardHeader.jsx";

export function useMonitor() {
  const toast = useToast();
  const [data, setData] = useState(null);
  const load = useCallback(() => {
    api.get("/api/hub/monitor").then(setData)
      .catch((e) => toast.error("Could not load the Monitor account", e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(load, [load]);
  return [data, setData, load];
}

export function MonitorSwitchCard() {
  const toast = useToast();
  const confirm = useConfirm();
  const [data, setData, load] = useMonitor();
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState("");     // shown once, then gone
  const [device, setDevice] = useState("");     // name of the device a new token is for

  async function send(path, body, method = "post") {
    setBusy(true);
    try {
      const out = await api[method](path, body);
      setData(out);
      return out;
    } catch (e) {
      toast.error("Could not save", e.message);
      load();
      return null;
    } finally { setBusy(false); }
  }

  async function turn(on) {
    if (on) {
      const out = await send(apiUrl("/api/hub/monitor"), { enabled: true });
      if (out) toast.success("Monitor account is on");
    } else {
      if (!await confirm({
        title: "Turn the Monitor account off?",
        description: "It signs out at once and every access token is removed, "
          + "so each central device needs a new token if you turn it on again. "
          + "Its pages and systems are kept.",
        confirmLabel: "Turn off", destructive: true })) return;
      setToken("");
      await send(apiUrl("/api/hub/monitor"), { enabled: false });
    }
  }

  async function makeToken() {
    if (!device.trim()) return void toast.error("Name the device this token is for");
    const out = await send(apiUrl("/api/hub/monitor/token"), { name: device.trim() });
    if (out?.token) { setToken(out.token); setDevice(""); }
  }

  async function dropToken(t) {
    if (!await confirm({
      title: `Remove the token of "${t.name}"?`,
      description: "That device is signed out and can no longer connect to this "
        + "box. Other devices keep working.",
      confirmLabel: "Remove", destructive: true })) return;
    setToken("");
    await send(apiUrl(`/api/hub/monitor/token/${encodeURIComponent(t.id)}`), undefined, "del");
  }

  const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "never");

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast.success("Copied"); }
    catch { toast.error("Could not copy: select it and copy by hand"); }
  }

  if (!data) return (
    <section className="rounded-xl border bg-card p-5">
      <p className="muted small">Loading…</p>
    </section>
  );
  const on = !!data.enabled;
  return (
    <section className="rounded-xl border bg-card p-5" data-monitor-card="">
      <CardHeader title="Monitor Account"
                  subtitle="One sign-in that can see the systems you allow, without changing them.">
        <StateSwitch checked={on} disabled={busy} aria-label="Monitor account"
                     onCheckedChange={turn} />
      </CardHeader>
      {on && (
        <div style={{ marginTop: 16 }}>
          <strong className="small">Access tokens</strong>
          {/* In front, as an alert, so it is read before a token is made. */}
          <div role="alert" data-token-alert="1"
               className="flex items-start gap-3 rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm"
               style={{ margin: "6px 0 10px" }}>
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden="true" />
            <span>
              Important: Each device has a unique, one-time-visible token for
              passwordless authentication via /api/auth/monitor-token. The token
              identifies the device by name and MAC address, even if its IP
              changes. Revoking the token or MAC address disables access, while
              turning off the account revokes all tokens.
            </span>
          </div>
          {token && (
            <div className="rounded-lg border p-3" style={{ marginBottom: 8 }}>
              <code style={{ wordBreak: "break-all" }}>{token}</code>
              <div className="muted small" style={{ margin: "4px 0 8px" }}>
                Copy it now. It cannot be shown again.
              </div>
              <Button size="sm" onClick={() => copy(token)}>Copy token</Button>
            </div>
          )}
          {(data.tokens || []).length > 0 && (
            <ul style={{ listStyle: "none", margin: "0 0 10px", padding: 0, display: "grid", gap: 6 }}>
              {data.tokens.map((t) => (
                <li key={t.id} className="rounded-lg border p-2"
                    style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <strong className="small">{t.name}</strong>
                    <span className="muted small" style={{ display: "block" }}>
                      Made {when(t.created)} - last used {when(t.last_used)}
                      {t.last_ip ? ` from ${t.last_ip}` : ""}
                    </span>
                  </span>
                  <Button size="sm" variant="outline" disabled={busy}
                          onClick={() => dropToken(t)}>Remove</Button>
                </li>
              ))}
            </ul>
          )}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <input className="input" style={{ maxWidth: 240 }} value={device}
                   maxLength={40} placeholder="Device name, e.g. Plant dashboard"
                   onChange={(e) => setDevice(e.target.value)} />
            <Button size="sm" disabled={busy} onClick={makeToken}>Create token</Button>
          </div>
          {data.box && (
            <p className="muted small" style={{ margin: "10px 0 0" }}>
              This box: <strong>{data.box.hostname}</strong>
              {data.box.macs?.length > 0 && (
                <> - MAC {data.box.macs.map((m) => `${m.mac} (${m.port})`).join(", ")}</>
              )}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

/** One selectable tile: an icon, a label and a tick. A button with
 *  role="checkbox", so the keyboard and a screen reader get a real checkbox. */
function Tile({ on, onClick, icon: Icon, children, disabled }) {
  return (
    <button type="button" role="checkbox" aria-checked={on} disabled={disabled}
            onClick={onClick} data-tile={on ? "on" : "off"}
            className={"flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-sm "
              + "transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50 "
              + (on ? "border-primary bg-primary/10 font-medium"
                    : "bg-background hover:bg-muted/60")}>
      {Icon && <Icon size={16} aria-hidden="true"
                     className={on ? "text-primary" : "text-muted-foreground"} />}
      <span style={{ flex: 1, minWidth: 0 }}>{children}</span>
      <span aria-hidden="true"
            className={"grid size-4 place-content-center rounded-full border "
              + (on ? "border-primary bg-primary text-primary-foreground" : "border-input")}>
        {on && <Check size={11} />}
      </span>
    </button>
  );
}

function Block({ title, hint, right, children }) {
  return (
    <section className="rounded-xl border bg-card p-5">
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <strong>{title}</strong>
          {hint && <div className="muted small" style={{ marginTop: 2 }}>{hint}</div>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));

export function MonitorAccess() {
  const toast = useToast();
  const [data, setData, load] = useMonitor();
  const [pages, setPages] = useState([]);
  const [all, setAll] = useState(true);
  const [slugs, setSlugs] = useState([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!data) return;
    setPages(data.pages || []);
    setAll(!!data.systems?.all);
    setSlugs(data.systems?.slugs || []);
  }, [data]);

  if (!data) return <p className="muted small">Loading…</p>;
  if (!data.enabled) {
    return (
      <p className="muted small">
        The Monitor account is off. A master turns it on in Settings, System.
      </p>
    );
  }

  const dirty = !sameSet(pages, data.pages || [])
    || all !== !!data.systems?.all
    || (!all && !sameSet(slugs, data.systems?.slugs || []));
  const toggle = (list, set, key) =>
    set(list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);
  const labelOf = Object.fromEntries((data.grantable || []).map((g) => [g.page, g.label]));
  const groups = SECTIONS
    .map((sec) => ({ ...sec, pages: sec.pages.filter((p) => labelOf[p]) }))
    .filter((sec) => sec.pages.length);
  const placed = new Set(groups.flatMap((g) => g.pages));
  const rest = (data.grantable || []).map((g) => g.page).filter((p) => !placed.has(p));
  if (rest.length) groups.push({ key: "more", label: "More", pages: rest });
  const defaults = data.default_pages || [];

  async function save() {
    if (!pages.length) return void toast.error("Leave at least one page");
    if (!all && !slugs.length) return void toast.error("Choose at least one system");
    setBusy(true);
    try {
      await api.post("/api/hub/monitor/pages", { pages });
      const out = await api.post("/api/hub/monitor/systems", { all, systems: slugs });
      setData(out);
      toast.success("Monitor access saved");
    } catch (e) {
      toast.error("Could not save", e.message);
      load();
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-4" data-monitor-access="">
      <Block title="Monitor account"
             hint="What this account can do is fixed. Choose below what it can open."
             right={<span className="rounded-full border px-2.5 py-0.5 small"
                          style={{ whiteSpace: "nowrap" }}>{data.username}</span>}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {[["Start and stop detection", Play], ["Download footage", Download],
            ["Download table files (CSV)", FileDown]].map(([text, Icon]) => (
            <span key={text} className="flex items-center gap-2 rounded-full border bg-muted/40 px-3 py-1 text-sm">
              <Icon size={14} aria-hidden="true" className="text-primary" />{text}
            </span>
          ))}
          <span className="flex items-center gap-2 rounded-full border bg-muted/40 px-3 py-1 text-sm text-muted-foreground">
            <Lock size={14} aria-hidden="true" />Cannot change anything else
          </span>
        </div>
      </Block>

      <Block title="Pages it can open"
             hint={`${pages.length} of ${(data.grantable || []).length} pages chosen`}
             right={!sameSet(pages, defaults) && (
               <Button size="sm" variant="outline" onClick={() => setPages(defaults)}>
                 Use the default</Button>)}>
        <div className="space-y-4">
          {groups.map((g) => (
            <div key={g.key}>
              <div className="muted small" style={{ margin: "0 0 6px", fontWeight: 600,
                                                    letterSpacing: ".04em", textTransform: "uppercase" }}>
                {g.label}
              </div>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {g.pages.map((p) => (
                  <Tile key={p} on={pages.includes(p)} icon={PAGE_ICONS[p]}
                        onClick={() => toggle(pages, setPages, p)}>
                    {labelOf[p]}
                  </Tile>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Block>

      <Block title="Systems it can open"
             hint="A system it can open stays online: it cannot be powered off from the Hub, and it comes back if it drops.">
        <div className="grid gap-2 sm:grid-cols-2">
          <Tile on={all} icon={Layers} onClick={() => setAll(true)}>
            All systems
            <span className="muted small" style={{ display: "block", fontWeight: 400 }}>
              Including ones added later</span>
          </Tile>
          <Tile on={!all} icon={ListChecks} onClick={() => setAll(false)}>
            Choose systems
            <span className="muted small" style={{ display: "block", fontWeight: 400 }}>
              Only the ones you tick</span>
          </Tile>
        </div>
        {!all && (
          <div style={{ marginTop: 12 }}>
            {(data.available || []).length === 0
              ? <p className="muted small">This box has no systems yet.</p>
              : (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {data.available.map((sys) => (
                    <Tile key={sys.slug} on={slugs.includes(sys.slug)} icon={Boxes}
                          onClick={() => toggle(slugs, setSlugs, sys.slug)}>
                      {sys.name}
                    </Tile>
                  ))}
                </div>
              )}
          </div>
        )}
      </Block>

      <div className="flex items-center gap-3 rounded-xl border bg-card px-5 py-3">
        <span className={"small " + (dirty ? "" : "muted")} style={{ flex: 1 }}>
          {dirty ? "You have unsaved changes." : "All changes are saved."}
        </span>
        <Button size="sm" disabled={busy || !dirty} onClick={save}>Save access</Button>
      </div>
    </div>
  );
}
