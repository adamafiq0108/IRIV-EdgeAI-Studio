// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// BoardPanel - who owns each of the board's terminals. Hub, Administration.
//
// A GPIO line is ONE piece of hardware and every system on the box shares it,
// so this decision cannot sensibly live inside one system's settings: two
// systems disagreeing about who drives DO0 is the exact conflict the page
// exists to prevent. Box-wide, admin-only, enforced server-side.
//
// The switch means "this platform keeps its hands off". A released terminal
// is not driven, not read, and not offered in the workflow palette - which is
// what makes it safe for the factory Node-RED flow (or a customer's own
// script) to own it. Nothing here drives a pin; it decides who may.
//
// Releasing is REFUSED while a rule is bound to the terminal, and the server
// names the rules. That refusal is the whole safety story: the alternative is
// a settings click dropping a relay that something is holding closed, and the
// machine wired to that relay does not care that the click happened in a
// settings page. Taking a terminal BACK is always allowed - nothing of ours
// is driving it by definition.
//
// Customer copy (2026-10-11): "connection" for terminal, "this device" for the
// platform, and every sentence held to what the code does. Two of the copy
// spec's claims were not true and are worded to the code instead: Node-RED
// does not politely use only the connections we leave free (its factory flow
// takes the ones it is wired to, and the page disables every switch while it
// runs), and a release is refused for ANY rule bound to the connection,
// including one in a stopped system, not only an active one.
import { useCallback, useEffect, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { Skeleton } from "./ui/skeleton.jsx";
import SettingsPage from "./ui/SettingsPage.jsx";
import { CardHeader, StateSwitch } from "./ui/CardHeader.jsx";
import { CircuitBoard, Plug, TriangleAlert } from "lucide-react";

//: What each kind of terminal is for, in the words on the silkscreen.
const KIND_LABEL = {
  input: "Digital input",
  output: "Digital output",
  pwm: "PWM / LED",
};

//: The front panel's two connections, by the names printed beside them.
const PANEL_NAME = { BUTTON: "Button", BUZZER: "Buzzer" };

//: Who is using a connection, for a sentence: the system's name and the rule.
//: Every configured rule, running or not, because a stopped system's rule
//: blocks a release just the same (board_release.set_released).
function usersSentence(users) {
  const names = [...new Set((users || []).map(
    (u) => `${u.name || u.slug} (${u.label})`))];
  return names.join(", ");
}

export default function BoardPanel() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState("");
  const toast = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      setData(await api.get("/api/hub/board"));
    } catch (e) {
      toast.error("Could not load the hardware connections", friendlyError(e));
      setData({ ok: false, ports: [] });
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  async function toggle(port) {
    const releasing = !port.released;
    setBusy(port.id);
    try {
      const d = await api.post("/api/hub/board/release",
                               { port: port.id, released: releasing });
      setData(d);
      toast.success(
        releasing ? `${port.id} released`
                  : `${port.id} is controlled by this device again`,
        releasing
          ? "This device no longer controls or reads it. Another application can use it now."
          : "It can be used in workflows again.");
    } catch (e) {
      // The refusal is the interesting case: a rule is bound to the
      // connection. Named, because "remove those rules first" is useless
      // without knowing which rules -- but named from the page's own list
      // (the system's NAME, not the slug the server's sentence carries), and
      // anything else goes through friendlyError rather than raw.
      const who = usersSentence(port.users);
      toast.error(`Could not release ${port.id}`,
        releasing && e?.status === 400
          ? (who ? `This connection is currently in use by ${who}. ` : "This connection is currently in use. ")
            + "Disable or remove the rule using it before releasing the connection."
          : friendlyError(e));
      load();
    } finally { setBusy(""); }
  }

  async function toggleNodered(on) {
    // Starting it is the one that needs saying out loud: Node-RED reclaims
    // its lines the moment it comes up, and a workflow already driving one of
    // them simply stops working with no error anywhere.
    // The front panel half is set_nodered's: it switches the panel off first
    // (same screen, button and buzzer) and back on when Node-RED stops.
    if (on && !await confirm({
      title: "Start Node-RED?",
      description: "Node-RED's flow will take control of the hardware "
        + "connections it uses, along with the display, the buzzer and the "
        + "USER button, and the front panel is switched off while it runs. "
        + "Workflows on this device that use those connections stop working. "
        + "To keep both, open Node-RED (port 1880) and remove or disable its "
        + "hardware nodes.",
      confirmLabel: "Start Node-RED" })) return;
    setBusy("nodered");
    try {
      const d = await api.post("/api/hub/board/nodered", { on });
      setData((s) => ({ ...s, nodered: d.nodered }));
      toast.success(on ? "Node-RED started" : "Node-RED stopped", d.message);
      // The terminals it was holding change hands, so re-read them.
      load();
    } catch (e) {
      // A 400 here carries systemctl's own stderr; that is a log line, not a
      // sentence for the operator.
      toast.error(on ? "Could not start Node-RED" : "Could not stop Node-RED",
                  e?.status === 400
                    ? "Try again. If it keeps failing, restart the device."
                    : friendlyError(e));
    } finally { setBusy(""); }
  }

  if (!data) return <Skeleton className="h-64 w-full" />;

  const ports = data.ports || [];
  // NOT `var(--muted)`: that token is defined twice in this stylesheet -- once
  // as a grey TEXT colour and once as shadcn's muted BACKGROUND -- and the
  // background one wins, so `.muted` in dark mode paints near-black text on a
  // near-black card. `--muted-foreground` is the one that means "secondary
  // text" in both themes.
  const dim = "var(--muted-foreground, #a1a1aa)";
  const line = "1px solid var(--line, rgba(128,128,128,.22))";
  const cell = { padding: "10px 12px", borderBottom: line, verticalAlign: "middle" };

  // Whether the board belongs to Node-RED right now. ACTIVE, not enabled:
  // enabled is what happens at the next boot, and a terminal is held or free
  // now. The two disagree exactly while somebody is switching it over.
  const nrOwns = !!data.nodered?.active;
  // The front panel is the other claimant, and it is ours: it runs in the hub
  // and holds the user button (and claims the buzzer per beep) off the same
  // board map this page lists. Per terminal, not whole-board: the panel takes
  // two of them, Node-RED takes the lot.
  const panelHolds = new Set(data.panel?.holds || []);

  return (
    <SettingsPage
      title="Hardware Inputs & Outputs"
      icon={CircuitBoard}
      description={<>
        Manage which hardware connections are controlled by this device and
        which are available to other applications or workflows.
        <span style={{ display: "block", marginTop: 4 }}>
          When a connection is assigned to another application, this device
          will no longer control or read it.
        </span>
      </>}>
      {!data.detected && (
        <p style={{ color: dim, marginBottom: 10 }}>
          No IRIV board detected{data.model ? ` (this device reports "${data.model}")` : ""}.
          The connections below are the board's standard layout; nothing on
          this page controls a pin on this device.
        </p>
      )}

      {/* A GPIO line has exactly ONE consumer. While the factory flow runs it
          holds the board's terminals, the OLED, the buzzer and the user
          button, and none of them are ours to give -- which the toggle below
          already says in prose. The switches used to stay live anyway, so
          every one of them was a button that would fail, and the failure
          arrived as "held by another program (lg)" rather than as the thing
          the operator had switched on two minutes earlier. */}
      {nrOwns && (
        <p style={{ color: dim, marginBottom: 10 }}>
          Node-RED is running and controls the hardware connections. This
          device cannot control or read them until Node-RED is stopped below.
        </p>
      )}

      {/* The front panel is the other claimant, and unlike Node-RED it is
          ours -- so this says which terminals it has rather than offering a
          second switch for it. Its own page owns that switch; two switches
          for one thing is two places for the answer to differ. */}
      {!nrOwns && panelHolds.size > 0 && (
        <div data-board-panel-note
             style={{ color: dim, marginBottom: 10, fontSize: 13 }}>
          <strong style={{ color: "var(--foreground, inherit)" }}>
            Front Panel Controls
          </strong>
          <p style={{ margin: "2px 0 0" }}>
            The front panel currently uses the physical{" "}
            {[...panelHolds].map((id) => PANEL_NAME[id] || id).join(" and ")}{" "}
            {panelHolds.size > 1 ? "connections" : "connection"}.
          </p>
          <p style={{ margin: "2px 0 0" }}>
            To control {panelHolds.size > 1 ? "these connections" : "this connection"}{" "}
            through a workflow instead, disable front-panel control under
            Settings &rarr; Front Panel.
          </p>
        </div>
      )}

      <div className="panel-card" style={{ padding: 0, overflowX: "auto",
                                           opacity: nrOwns ? 0.55 : 1 }}>
        {/* A real table, styled here: the product's `.table` class is only
            `display:table`, so a bare <table> came out with no padding, no
            rules between rows and every column jammed against the next. */}
        <table style={{ width: "100%", borderCollapse: "collapse",
                        fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              {["Connection", "Hardware Pin", "Currently Used By", "Device Control"].map((h, i) => (
                <th key={h} style={{ ...cell, color: dim, fontWeight: 600,
                                     fontSize: 11, letterSpacing: ".05em",
                                     textTransform: "uppercase", whiteSpace: "nowrap",
                                     textAlign: i === 3 ? "right" : "left" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ports.map((p) => {
              // RUNNING systems only. This column answers "what is on this
              // terminal right now", and a stopped system is not on it -- its
              // rule exists and will take the terminal again when it starts,
              // but listing it here made eight terminals look occupied on a
              // box driving none of them. A stopped system's claim is its own
              // system's business, not this page's.
              const users = (p.users || []).filter((u) => u.running);
              return (
                <tr key={p.id}>
                  <td style={cell}>
                    {/* Inherits the card's own text colour rather than naming
                        one: the terminal name is primary text and should
                        follow the theme without a token in the middle. */}
                    <div style={{ fontWeight: 600 }}>{p.id}</div>
                    <div style={{ color: dim, fontSize: 12 }}>
                      {KIND_LABEL[p.kind] || p.label}
                    </div>
                  </td>
                  <td style={{ ...cell, color: dim, whiteSpace: "nowrap" }}>
                    BCM {p.pin}
                  </td>
                  <td style={cell}>
                    {/* Nothing of ours on it: either another program holds
                        the line right now (`busy`, from libgpiod -- its own
                        consumer tag, often just "lg", goes in the tooltip
                        rather than the cell), or nobody does. */}
                    {users.length === 0 ? (
                      <span style={{ color: dim }}
                            title={p.busy && p.consumer
                              ? `Held by "${p.consumer}"` : undefined}>
                        {p.busy ? "Another application"
                                : "Not currently in use"}
                        {p.released && (
                          <span style={{ display: "block", fontSize: 12 }}>
                            Released to other applications
                          </span>
                        )}
                      </span>
                    ) : (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                        {/* Named, because "in use" without saying by what is
                            exactly the dead end this page exists to end.
                            Everything listed is RUNNING -- see the filter
                            where `users` is built -- so the green dot states
                            a fact rather than one of two possible states.

                            --brand-accent, not --accent: this panel mounts
                            inside the hub's shadcn subtree, where --accent is
                            a muted BACKGROUND token and these chips would
                            render nearly invisible against the card. */}
                        {/* The system's NAME, not its slug. The slug is a
                            filename ("counting_template_editor"); the name is
                            what it is called on every other page, and a
                            terminal held by something the operator has never
                            heard of is a terminal they cannot act on. The
                            slug stays in the tooltip for whoever needs to
                            match it to a config file. */}
                        {users.map((u, i) => (
                          <span key={i}
                                title={`${u.name || u.slug} (${u.slug}) is running `
                                       + `and uses ${p.id} for ${u.label}`}
                                style={{ display: "inline-flex", gap: 6,
                                         alignItems: "center",
                                         padding: "2px 8px", borderRadius: 999,
                                         fontSize: 12, whiteSpace: "nowrap",
                                         border: "1px solid color-mix(in srgb, var(--brand-accent, #04bade) 55%, transparent)",
                                         color: "var(--brand-accent, #04bade)" }}>
                            <span style={{ width: 7, height: 7, borderRadius: 999,
                                           flex: "none",
                                           background: "var(--ok, #16a34a)" }} />
                            <b style={{ fontWeight: 600 }}>{u.name || u.slug}</b>
                            <span style={{ opacity: 0.85 }}>{u.label}</span>
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td style={{ ...cell, textAlign: "right", whiteSpace: "nowrap" }}>
                    {/* Disabled while Node-RED or the front panel holds it. A
                        rule bound to it does NOT disable the switch: the
                        server refuses the release and the toast names the
                        rule, and the tooltip says so beforehand. Taking a
                        terminal BACK is never blocked. */}
                    <button type="button" role="switch"
                            aria-checked={!p.released}
                            aria-label={`${p.id}: Device controls this connection`}
                            className={"switch" + (p.released ? "" : " switch-on")
                                       + (busy === p.id ? " is-busy" : "")}
                            style={{ "--sw-w": "34px", "--sw-h": "19px" }}
                            disabled={busy === p.id || nrOwns
                                      || panelHolds.has(p.id)}
                            title={nrOwns
                              ? "Node-RED is running and controls this connection. Stop Node-RED below to control it from this device."
                              : panelHolds.has(p.id)
                              ? `${p.id} is used by the front panel. Disable front-panel control under Settings → Front Panel to use it in a workflow.`
                              : p.released
                              ? `${p.id} is released. Turn on to let this device control it again.`
                              : (p.users || []).length
                              ? `${p.id} is used by ${usersSentence(p.users)}. Disable or remove the rule before releasing it.`
                              : `Device controls this connection. Turn off to release ${p.id} for another application.`}
                            onClick={() => { if (busy !== p.id) toggle(p); }}>
                      <span className="switch-knob" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* ---- the OTHER claimant on these terminals ----
          Node-RED belongs on this page and nowhere else: the board ships with
          a factory flow that drives several of these lines plus the OLED, the
          buzzer and the USER button, and "held by another program" a few rows
          up is usually it. This is where an operator can see that and decide.
          Stopping it DISABLES it too -- a flow that comes back at the next
          power cycle to take terminals a workflow now drives is a fault that
          surfaces on site rather than here. */}
      {data.nodered?.installed && (
        <div className="panel-card" style={{ marginTop: 14 }}>
          {/* The card's on/off at the top right with its state label left
              of it (ui/CardHeader.jsx, 2026-10-14): Running / Stopped is the
              label, so the status pill and the switch are one thing now. */}
          {/* Not the spec's "Node-RED can access connections that are not
              reserved by this device": the factory flow takes the lines it
              is wired to, whatever this page says, and every switch above is
              disabled while it runs. */}
          <CardHeader title="Node-RED Integration"
                      subtitle={"Node-RED is a separate flow tool installed on the board. While "
                        + "it runs, its flow controls the hardware connections it uses, "
                        + "plus the display, the buzzer and the USER button."}>
            <StateSwitch checked={!!data.nodered.active} aria-label="Node-RED"
                         onLabel="Running" offLabel="Stopped"
                         labelProps={{ "data-nodered-status": "" }}
                         disabled={busy === "nodered"}
                         title={data.nodered.active
                           ? "Stop Node-RED and keep it off when the device starts"
                           : "Start Node-RED and start it whenever the device starts"}
                         onCheckedChange={(on) => toggleNodered(!!on)} />
          </CardHeader>
          <div>
              <div data-nodered-state style={{ color: dim, fontSize: 12, marginTop: 6 }}>
                {data.nodered.active
                  ? "Node-RED is running. This device cannot control or read hardware connections until it is stopped."
                  : "Node-RED is stopped. Hardware connections remain available to this device."}
                {data.nodered.active !== data.nodered.enabled && (
                  <> {data.nodered.enabled
                       ? "It is set to start when the device starts."
                       : "It will not start when the device starts."}</>
                )}
              </div>
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 12, color: dim,
                    fontSize: 12, lineHeight: 1.5 }}>
        <Plug size={14} style={{ flex: "none", marginTop: 2 }} />
        {/* The spec said "the Node-RED connection list"; what actually drops
            a released connection is this product's own workflow block list
            (Workflow.jsx, `released`). Node-RED has no list we control. */}
        <div data-board-release-note>
          <strong style={{ display: "block" }}>Before releasing a connection</strong>
          Released connections are no longer offered in the workflow block
          list. Existing workflows that use them stay saved but do not operate
          until this device controls the connection again.
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 8, color: dim,
                    fontSize: 12, lineHeight: 1.5 }}>
        <TriangleAlert size={14} style={{ flex: "none", marginTop: 2 }} />
        <div data-board-release-warning>
          A connection cannot be released while a rule is using it, including
          a rule in a system that is stopped. Disable or remove the rule first.
        </div>
      </div>
    </SettingsPage>
  );
}
