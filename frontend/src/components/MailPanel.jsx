// MailPanel - the box's sending mailboxes, under Settings.
//
// A sender used to be a field buried in each system's email config, which
// meant a deployment whose four systems all mail from one alerts@ mailbox
// held that account -- and its app password -- in four places. Rotating the
// password was four edits, and missing one produced a system whose mail
// silently stopped authenticating at whatever hour its rule next fired.
//
// So: declared once here, referenced by id from every system's Email nodes
// and Footage page. The list is the page; a mailbox is a row, and adding one
// is a form that only appears when you ask for it, because most boxes have
// exactly one and should not open on an empty form.
//
// The password is never read back. The row says whether one is SET; changing
// it means typing a new one. That is not paranoia about admins -- they can
// send mail as this address anyway -- it is that a credential returned to a
// browser ends up in a cache, a screenshot and a bug report.
//
// Customer copy (2026-10-11): "email account" for mailbox, and nothing the
// code does not do. Saving does NOT send anything (the Test button does), a
// blank server falls back to smtp.gmail.com and a blank port to 587 on the
// server, and every connection uses STARTTLS whatever the port -- so the
// form checks only what mail_accounts._clean refuses, in the same terms, and
// says the rest out loud. Server and SMTP errors are never shown raw.
import { useCallback, useEffect, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import SettingsPage, { SettingsSection } from "./ui/SettingsPage.jsx";
import { Mail, Plus, Trash2, Star, Send, Loader2 } from "lucide-react";
import { PasswordInput } from "./ui/input.jsx";
import { GMAIL_APP_PASSWORD_STEPS, appPasswordProblem, isGmailHost } from "../lib/appPassword.js";

const BLANK = { id: "", name: "", sender: "", app_password: "",
                smtp_host: "smtp.gmail.com", smtp_port: 587 };

//: The server's own address test (mail_accounts._ADDR), so the form refuses
//: exactly what the save would and nothing more.
const ADDR = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

//: [title, sentence] for the first thing the server would refuse, or null.
//: Mirrors mail_accounts._clean: the address is required and must look like
//: one; a NEW account needs a password (an edit left blank keeps the stored
//: one); a port, when given, is 1-65535. A blank server is not refused there
//: (it falls back to smtp.gmail.com), so it is not refused here either.
export function mailFormProblem(form) {
  const sender = String(form.sender || "").trim();
  if (!sender) return ["Email Address Required",
    "Enter the email address this device will use to send messages."];
  if (!ADDR.test(sender)) return ["Invalid Email Address",
    "Enter a valid email address and try again."];
  if (!form.id && !String(form.app_password || "")) return ["App Password Required",
    "Enter the app password your email provider created for this device."];
  // App passwords only (2026-10-14): for Gmail the format is known, so a
  // normal account password is refused here, before the round trip.
  const pwProblem = appPasswordProblem(form.app_password, form.smtp_host);
  if (pwProblem) return ["Not an App Password", pwProblem];
  const raw = String(form.smtp_port ?? "").trim();
  if (raw !== "") {
    const port = Number(raw);
    if (!Number.isInteger(port) || port < 1 || port > 65535) return [
      "Invalid SMTP Port", "Enter a valid port number between 1 and 65535."];
  }
  return null;
}

export default function MailPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);      // null = not editing
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState("");

  const load = useCallback(() => {
    api.get("/api/mail/accounts")
      .then((d) => { setData(d); setError(null); })
      .catch(setError);
  }, []);
  useEffect(load, [load]);

  const accounts = data?.accounts || [];
  const defaultId = data?.default_id || "";

  async function save() {
    const problem = mailFormProblem(form);
    if (problem) { toast.error(problem[0], problem[1]); return; }
    setBusy(true);
    try {
      const d = await api.post("/api/mail/accounts", form);
      setData(d);
      setForm(null);
      // Saving stores the settings and sends nothing, so it never claims the
      // password works; Test is what proves that.
      toast.success("Email Account Saved",
                    "The email account has been saved and is available for use "
                    + "wherever email sending is supported. Use Test to check "
                    + "that it can send.");
    } catch (e) {
      // A 400 is one of _clean's refusals, which can quote what was typed;
      // the checks above already said the useful part.
      toast.error("Could Not Save Email Account",
                  e?.status === 400
                    ? "Check the information you entered and try again."
                    : friendlyError(e));
    } finally { setBusy(false); }
  }

  async function remove(acc) {
    // Named in the question: "delete this mailbox" is a different decision
    // depending on WHICH, and every Email node pointing at it stops sending.
    if (!await confirm({
      title: `Delete "${acc.name}"?`,
      description: "Workflow email steps and Footage sends that use this "
        + "email account will report a missing sender until they are set to "
        + "another one.",
      destructive: true })) return;
    setBusy(true);
    try {
      setData(await api.post("/api/mail/accounts/delete", { id: acc.id }));
      toast.success("Email account deleted");
    } catch (e) { toast.error("Could not delete the email account", friendlyError(e)); }
    finally { setBusy(false); }
  }

  async function makeDefault(acc) {
    setBusy(true);
    try {
      setData(await api.post("/api/mail/accounts/default", { id: acc.id }));
      toast.success(`${acc.name} is now the default sender`);
    } catch (e) { toast.error("Could not set the default", friendlyError(e)); }
    finally { setBusy(false); }
  }

  async function test(acc) {
    setTesting(acc.id);
    try {
      await api.post("/api/mail/accounts/test", { id: acc.id });
      // The route sends to the account's own address when no `to` is given.
      toast.success("Test email sent", `A test email was sent to ${acc.sender}.`);
    } catch (e) {
      // The route's 400 carries smtplib's own text after "could not send:".
      // Only the credentials refusal is turned into words; the rest is a
      // plain sentence, never the server's string.
      const refused = /refused these credentials/.test(e?.message || "");
      toast.error("The test email could not be sent",
        refused
          ? "The mail server did not accept this email address and password. "
            + "For Gmail, use an App Password."
          : e?.status === 400
          ? "Check the server, port and password, then try again."
          : friendlyError(e));
    } finally { setTesting(""); }
  }

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <SettingsPage
      title="Email"
      icon={Mail}
      description="The email accounts this device can send from. Set up once
                   here and chosen by name wherever email is sent."
      actions={!form && (
        <button type="button" onClick={() => setForm({ ...BLANK })}
                className="inline-flex items-center gap-1.5 rounded-md border
                           px-3 py-1.5 text-sm font-medium hover:bg-muted">
          <Plus className="size-4" /> Add Email Account
        </button>
      )}>

      {error && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5
                        p-4 text-sm text-destructive">
          {friendlyError(error) || "Could not load the email accounts."}
        </div>
      )}

      {form && (
        <SettingsSection
          title={form.id ? "Edit Email Account" : "Add Email Account"}
          description="Configure an email account that this device can use to
                       send alerts and other messages.">
          {/* Gmail first because it is the common case, and its one trap
              (the account password is refused) only shows at send time. */}
          <div data-mail-gmail-help
               className="mt-3 rounded-lg border bg-muted/40 p-3 text-sm">
            <p className="font-medium">Using Gmail?</p>
            <p className="mt-1 text-muted-foreground">
              Gmail requires an App Password for SMTP access. Use an App
              Password instead of your regular Google Account password.
            </p>
            <p className="mt-1 text-muted-foreground">
              To create one, open your Google Account settings and go to
              Security → 2-Step Verification → App passwords. You may need to
              enable 2-Step Verification before you can create an App Password.
            </p>
            <p className="mt-1 text-muted-foreground">
              Other email providers may use a different server, port and
              password type. Check your provider's SMTP settings.
            </p>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <label className="grid gap-1.5">
              <span className="text-sm font-medium">Account Name</span>
              <input className="rounded-md border bg-background px-3 py-1.5 text-sm"
                     value={form.name} onChange={set("name")}
                     placeholder="e.g. Line Alerts" />
              <span className="text-xs text-muted-foreground">
                Choose a name to identify this account when selecting an email
                sender in workflows. If left blank, the sending email address
                will be used as its name.
              </span>
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-medium">Sending Email Address</span>
              <input className="rounded-md border bg-background px-3 py-1.5 text-sm"
                     type="email" value={form.sender} onChange={set("sender")}
                     placeholder="alerts@example.com" />
              <span className="text-xs text-muted-foreground">
                Enter the email address that recipients will see as the sender.
              </span>
            </label>
            {/* App passwords only (Tok Aba, 2026-10-14). For Gmail the box
                knows the format and refuses anything else as you type; for
                another server it takes what the provider issued. */}
            <div className="grid gap-1.5 sm:col-span-2" data-mail-app-password="">
              <label className="grid gap-1.5">
                <span className="text-sm font-medium">
                  App Password
                  {form.id && (
                    <span className="ml-2 font-normal text-muted-foreground">
                      (leave blank to keep the saved password)
                    </span>
                  )}
                </span>
                <PasswordInput native
                       className="rounded-md border bg-background px-3 py-1.5 text-sm"
                       autoComplete="new-password" aria-label="App Password"
                       aria-invalid={!!appPasswordProblem(form.app_password, form.smtp_host)}
                       value={form.app_password} onChange={set("app_password")}
                       placeholder={form.id ? "Unchanged" : "16-character app password"} />
              </label>
              {appPasswordProblem(form.app_password, form.smtp_host) && (
                <span className="text-xs text-destructive" role="alert" data-mail-pw-problem="">
                  {appPasswordProblem(form.app_password, form.smtp_host)}
                </span>
              )}
              <span className="text-xs text-muted-foreground" data-mail-pw-help="">
                Use an app password, not your normal email password. Your email
                provider creates it in the account&apos;s security settings (for
                Gmail: Google Account &gt; Security &gt; 2-Step Verification &gt;
                App passwords).
                {!isGmailHost(form.smtp_host) && (
                  <> For this server, enter the app password, or the SMTP password
                    your provider tells you to use for apps.</>
                )}
              </span>
              <details className="text-xs text-muted-foreground" data-mail-pw-howto="">
                <summary className="cursor-pointer font-medium">How to create one (Gmail)</summary>
                <ol className="mt-1 list-decimal pl-5">
                  {GMAIL_APP_PASSWORD_STEPS.map((step) => <li key={step}>{step}</li>)}
                </ol>
              </details>
            </div>
            <label className="grid gap-1.5">
              <span className="text-sm font-medium">Outgoing Mail Server (SMTP)</span>
              <input className="rounded-md border bg-background px-3 py-1.5 text-sm"
                     value={form.smtp_host} onChange={set("smtp_host")}
                     placeholder="smtp.gmail.com" />
              <span className="text-xs text-muted-foreground">
                Enter the outgoing mail server provided by your email service
                provider. If left blank, smtp.gmail.com is used.
              </span>
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-medium">SMTP Port</span>
              <input className="rounded-md border bg-background px-3 py-1.5 text-sm"
                     type="number" value={form.smtp_port} onChange={set("smtp_port")} />
              <span className="text-xs text-muted-foreground">
                Port 587 uses STARTTLS encryption with the current email
                settings. This device always connects with STARTTLS, so a port
                that expects SSL from the start (such as 465) will not work.
              </span>
            </label>
          </div>
          <div className="mt-4 flex items-center gap-2">
            <button type="button" disabled={busy} onClick={save}
                    className="rounded-md bg-primary px-3 py-1.5 text-sm
                               font-medium text-primary-foreground
                               disabled:opacity-50">
              {busy ? "Saving…" : "Save Email Account"}
            </button>
            <button type="button" disabled={busy} onClick={() => setForm(null)}
                    className="rounded-md border px-3 py-1.5 text-sm font-medium
                               hover:bg-muted disabled:opacity-50">
              Cancel
            </button>
          </div>
        </SettingsSection>
      )}

      <SettingsSection
        title="Email Accounts"
        description={accounts.length
          ? "The default account is used wherever no specific account is chosen."
          : undefined}>
        {!data ? (
          <p className="mt-3 text-sm text-muted-foreground">Loading…</p>
        ) : !accounts.length ? (
          // An empty state that says what the thing is FOR. "No mailboxes"
          // alone leaves an admin guessing whether that matters.
          <div data-mail-empty className="mt-3 text-sm">
            <p className="font-medium">No email accounts configured</p>
            <p className="mt-1 text-muted-foreground">
              Add an email account to send alerts from workflows or email
              footage from the Footage page.
            </p>
            {!form && (
              <button type="button" onClick={() => setForm({ ...BLANK })}
                      className="mt-3 inline-flex items-center gap-1.5 rounded-md
                                 border px-3 py-1.5 text-sm font-medium hover:bg-muted">
                <Plus className="size-4" /> Add Email Account
              </button>
            )}
          </div>
        ) : (
          <ul className="mt-3 divide-y rounded-lg border">
            {accounts.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-3 p-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{a.name}</span>
                    {a.id === defaultId && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-xs
                                       font-medium text-muted-foreground">
                        Default
                      </span>
                    )}
                    {/* Said out loud, because a mailbox with no password looks
                        identical to a working one until it fails to send. */}
                    {!a.has_password && (
                      <span className="rounded bg-destructive/10 px-1.5 py-0.5
                                       text-xs font-medium text-destructive">
                        No password
                      </span>
                    )}
                  </div>
                  <div className="truncate text-sm text-muted-foreground">
                    {a.sender} · {a.smtp_host}:{a.smtp_port}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button type="button" disabled={busy || testing === a.id}
                          onClick={() => test(a)} title="Send a test email"
                          className="inline-flex items-center gap-1.5 rounded-md
                                     border px-2 py-1 text-sm hover:bg-muted
                                     disabled:opacity-50">
                    {testing === a.id
                      ? <Loader2 className="size-4 animate-spin" />
                      : <Send className="size-4" />}
                    Test
                  </button>
                  {a.id !== defaultId && (
                    <button type="button" disabled={busy}
                            onClick={() => makeDefault(a)}
                            title="Make this the default email account"
                            aria-label={`Make ${a.name} the default email account`}
                            className="inline-flex items-center rounded-md border
                                       p-1.5 hover:bg-muted disabled:opacity-50">
                      <Star className="size-4" />
                    </button>
                  )}
                  <button type="button" disabled={busy}
                          onClick={() => setForm({ ...a, app_password: "" })}
                          className="rounded-md border px-2 py-1 text-sm
                                     hover:bg-muted disabled:opacity-50">
                    Edit
                  </button>
                  <button type="button" disabled={busy} onClick={() => remove(a)}
                          title={`Delete ${a.name}`}
                          aria-label={`Delete ${a.name}`}
                          className="inline-flex items-center rounded-md border
                                     p-1.5 text-destructive hover:bg-destructive/10
                                     disabled:opacity-50">
                    <Trash2 className="size-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </SettingsSection>
    </SettingsPage>
  );
}
