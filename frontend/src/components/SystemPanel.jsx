// System - the switches that are about the BOX.
//
// Everything else under Settings is about a system (a camera and its rules),
// an account (who may open what) or a way the box talks out (a mailbox, a bot
// token, an address). These are about the appliance itself -- what it is
// allowed to do at all -- and they had been scattered across whichever page
// happened to use them: auto logout under Accounts, whether this box uses
// email at all nowhere, inferred from whether somebody had filled a mailbox
// in.
//
// Master and developer only (auth.SETTINGS_TABS). Each switch here opens or
// closes a path for EVERY account at once, which is a different weight from
// editing one account's row.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import SettingsPage from "./ui/SettingsPage.jsx";
import { MonitorSwitchCard } from "./MonitorPanels.jsx";
import { CardHeader, StateSwitch } from "./ui/CardHeader.jsx";
import { SlidersHorizontal, TriangleAlert } from "lucide-react";

export default function SystemPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get("/api/system/settings").then(setData)
      .catch((e) => toast.error("Could not load system settings", e.message));
  }, []);
  useEffect(load, [load]);

  async function save(patch) {
    setBusy(true);
    try {
      setData(await api.post("/api/system/settings", patch));
    } catch (e) {
      toast.error("Could not save", e.message);
      load();
    } finally { setBusy(false); }
  }

  async function setEmail(on) {
    // Turning it OFF closes the emailed way back in for every account at
    // once, so it asks -- and says what is left, which depends on the box:
    // the front panel answers only where it is actually running.
    if (!on) {
      const panel = !!data?.panel_reset;
      if (!await confirm({
        title: "Stop using email on this box?",
        description: panel
          ? "No reset links, no reset requests, and no recovery addresses on "
            + "accounts. The Forgot screen will send people to the box "
            + "instead: hold the USER button for 5 seconds and the panel "
            + "shows a code that resets the master."
          : "No reset links, no reset requests, and no recovery addresses on "
            + "accounts -- and the front panel is switched off on this box, "
            + "so there will be NO way to reset a forgotten password from the "
            + "network or the hardware. Recovering one would mean a shell on "
            + "the box.",
        confirmLabel: "Stop using email",
        destructive: !panel,
        requireText: panel ? null : "stop email",
        requireHint: panel ? undefined
          : "Type it to confirm there will be no way back in.",
      })) return;
    }
    save({ email: on });
  }

  return (
    <SettingsPage
      title="System"
      icon={SlidersHorizontal}
      description="Manage system settings, security, and account access for this device.">
      {!data ? (
        <p className="muted small">Loading…</p>
      ) : (
        <div className="space-y-4">
          {/* -- password recovery, as an alert (Tok Aba, 2026-10-21) -------
              With email off, the way back in for a forgotten master password
              is the front panel button, or nothing. Said at the very top of
              the page, as an alert, so it is read on opening System rather
              than found halfway down. */}
          {!data.email && (
            <div role="alert" data-password-recovery-alert="1"
                 className="flex items-start gap-3 rounded-xl border border-amber-500/50 bg-amber-500/10 p-4 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden="true" />
              <div>
                <strong>Password Recovery</strong>
                <p style={{ margin: "2px 0 0" }}>
                  This device does not support password recovery by email.
                </p>
                <p style={{ margin: "4px 0 0" }}>
                  {data.panel_reset
                    ? "To reset a forgotten master password, hold the physical USER button on the device for 5 seconds."
                    : "The front panel is off, so a forgotten master password can only be recovered from a shell on the box."}
                </p>
              </div>
            </div>
          )}

          {/* -- sessions ---------------------------------------------- */}
          <section className="rounded-xl border bg-card p-5">
            {/* The heading and the switch say the SAME thing: the switch
                is "sign out automatically", and on means it does. (Beta
                feedback: the heading said Auto Logout while the words beside
                it described staying signed in, so nobody could tell which
                way the switch went.) The stored flag is unchanged --
                auto_logout true = the idle timeout, false = a 30-day window
                (auth.NO_AUTO_LOGOUT_MINUTES, 30 * 24 * 60). */}
            {/* Since 2026-10-14 the switch sits at the header's top right
                with an On / Off label (ui/CardHeader.jsx): "Automatic
                Sign-out: On" says which way it goes without a second line. */}
            <CardHeader title="Automatic Sign-out"
                        subtitle="Automatically sign users out after a period of inactivity.">
              <StateSwitch checked={!!data.auto_logout} disabled={busy}
                           aria-label="Sign out automatically when idle"
                           onCheckedChange={(on) => save({ auto_logout: on })} />
            </CardHeader>
            <p className="muted small" style={{ margin: "6px 0 0" }}>
              When disabled, new sign-ins can remain active for up to 30
              days. Existing sign-ins keep their current expiration time.
            </p>
          </section>

          {/* -- email ------------------------------------------------- */}
          <section className="rounded-xl border bg-card p-5">
            <CardHeader title={data.email ? "Email" : "Email Recovery Unavailable"}
                        subtitle={data.email
                          ? "Accounts may have a recovery address, and a forgotten "
                            + "password can be reset by a link."
                          : "Email cannot be used to recover accounts or reset "
                            + "passwords on this device."}>
              <StateSwitch checked={!!data.email} disabled={busy} aria-label="Email"
                           onCheckedChange={setEmail} />
            </CardHeader>
                {/* What is left when it is off. Said HERE rather than only in
                    the confirm dialog, because the state outlives the click
                    that made it -- somebody reading this page a month later
                    is asking exactly this question. */}
            {!data.email && (
              <p className={data.panel_reset ? "muted small" : "c-warn small"}
                 style={{ margin: "6px 0 0" }}>
                {data.panel_reset
                  ? "To reset a forgotten master password, hold the physical "
                    + "USER button on the device for 5 seconds."
                  : "The front panel is off, so a forgotten password can "
                    + "only be recovered from a shell on the box."}
              </p>
            )}
          </section>

          {/* -- the Monitor account ----------------------------------- */}
          <MonitorSwitchCard />

        </div>
      )}
    </SettingsPage>
  );
}
