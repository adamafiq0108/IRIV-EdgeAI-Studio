// TelegramPanel - the box's Telegram bots, under Settings.
//
// The bot token and chat id used to be typed into each Telegram node, so a
// deployment whose twelve rules all message one operations group held the same
// token in twelve places on the canvas - and in every template exported from
// it. Rotating it meant finding every node; missing one produced a rule whose
// alerts silently stopped arriving.
//
// So: declared once here, referenced by name from every Telegram node. Exactly
// the shape the Email page uses for mailboxes, deliberately - there is one
// thing to learn, not two.
//
// The token is never read back. The row says whether one is SET; changing it
// means pasting a new one. Whoever holds it can post as the bot into every
// chat it is in, and a credential returned to a browser ends up in a cache, a
// screenshot and a bug report.
//
// Customer copy (2026-10-11), held to what the code does: a workflow's
// Telegram step posts as the bot ASSIGNED to its system (there is no picker
// "by name"), the allowed list gates inbound commands only (telegram_net.
// allowed, empty = nobody, /start included) and never outgoing messages,
// saving checks the token's SHAPE and does not contact Telegram, a blank name
// falls back to the test chat id and then to "Telegram bot", and an invalid
// line in the allowed list is dropped on save rather than refused. Server and
// Telegram text is never shown raw.
//
// Allowed email addresses (2026-10-11) are now the main way in: a listed
// person sends /start, then their address when the bot asks, and is linked
// to it as a Telegram USER (telegram_links.py). An unknown chat's /start is
// the one thing it can get a reply to (telegram_net.py, 2026-10-14). The chat ID list still works exactly as before,
// under Advanced. Each address shows who holds it, with Remove to free it.
//
// Security pass (Pipi S2-M1/S2-L3/S2-I1, 2026-10-11): a claimant is shown as
// numeric Telegram ID, @username ("no username" said out loud) and the free
// name in quotes, never the name alone -- a first name can read "Ana B
// (@ana)". Remove also blocks that user from the address, listed under it
// with Allow again. In a group a claim must be addressed to the bot (/link,
// a mention, or a reply), which the help says along with why (privacy mode).
import { useCallback, useEffect, useState } from "react";
import { api, friendlyError } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { useAsk, useConfirm } from "./ui/ask.jsx";
import SettingsPage, { SettingsSection } from "./ui/SettingsPage.jsx";
import { Send, Plus, Trash2, Star, Loader2, TriangleAlert } from "lucide-react";
import { PasswordInput } from "./ui/input.jsx";

const BLANK = { id: "", name: "", token: "", chat_id: "", system: "",
                allow: [], emails: [], welcome: "" };

//: "1 allowed email address · 2 allowed chats" for a bot row.
//: "Telegram user ID 123, username @ana, name shown as "Ana B"" -- the same
//: line telegram_links.identity writes into the Inbox notice.
export function claimant(c) {
  const id = `Telegram user ID ${c?.user_id || "unknown"}`;
  const user = c?.username ? `username @${c.username}` : "no username";
  return `${id}, ${user}` + (c?.name ? `, name shown as "${c.name}"` : "");
}

//: The three fields of one claimant, as separate pieces of text.
function Claimant({ c }) {
  return (
    <>
      <span data-tg-claim-id className="font-mono text-xs">ID {c.user_id || "unknown"}</span>
      <span data-tg-claim-username className="text-xs">
        {c.username ? `@${c.username}` : "no username"}
      </span>
      {c.name && (
        <span data-tg-claim-name className="text-xs">name shown as "{c.name}"</span>
      )}
    </>
  );
}

export function accessSummary(bot) {
  const e = (bot.emails || []).length;
  const c = (bot.allow || []).length;
  if (!e && !c) return "No one can send commands";
  const parts = [];
  if (e) parts.push(`${e} allowed email address${e === 1 ? "" : "es"}`);
  if (c) parts.push(`${c} allowed chat${c === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

//: The server's shapes (telegram_bots._TOKEN, CHAT_ID_RE), so the form
//: refuses exactly what the save would and nothing more.
const TOKEN = /^\d{5,}:[A-Za-z0-9_-]{20,}$/;
const CHAT_ID = /^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$/;

//: [title, sentence] for the first thing telegram_bots._clean would refuse.
export function botFormProblem(form) {
  const token = String(form.token || "").trim();
  if (!token && !form.id) return ["Bot Token Required",
    "Enter the token provided by BotFather."];
  if (token && !/^\*+$/.test(token) && !TOKEN.test(token)) return [
    "Invalid Bot Token", "Check the bot token and try again."];
  const chat = String(form.chat_id || "").trim();
  if (chat && !CHAT_ID.test(chat)) return ["Invalid Chat ID",
    "Check the chat ID and enter a valid value."];
  return null;
}

//: A failed test, in words. The route's 400 is Telegram's own description
//: (telegram_bots.explain) or urllib's text; only its kind is used here.
function testFailure(e) {
  const low = String(e?.message || "").toLowerCase();
  if (e?.status !== 400) return friendlyError(e);
  if (low.includes("unauthorized"))
    return "The bot token was not accepted. Check the token from BotFather and save it again.";
  if (low.includes("chat not found") || low.includes("can't initiate")
      || low.includes("bot was blocked"))
    return "Telegram could not reach that chat. A person must send the bot a "
      + "message first, and a group must have the bot added to it.";
  if (low.includes("rights"))
    return "The bot is in the chat but is not allowed to send messages there. "
      + "Give it permission to send messages.";
  return "Check that the bot token is valid and that the bot can access the "
    + "selected chat. For group chats, make sure the bot has been added to the "
    + "group and has the permissions required to send messages.";
}

export default function TelegramPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const ask = useAsk();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);      // null = not editing
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState("");
  // Addresses already on accounts, for the "Add From Accounts" picker. Read
  // through /api/users, so it lists exactly the accounts this viewer may see
  // there and nothing more; if that read fails the picker is simply absent.
  const [accountEmails, setAccountEmails] = useState([]);

  const load = useCallback(() => {
    api.get("/api/telegram/bots")
      .then((d) => { setData(d); setError(null); })
      .catch(setError);
  }, []);
  useEffect(load, [load]);
  const editing = !!form;
  useEffect(() => {
    if (!editing) return;
    api.get("/api/users")
      .then((d) => setAccountEmails([...new Set((Array.isArray(d?.users) ? d.users : [])
        .filter((u) => u && u.enabled !== false && u.email)
        .map((u) => String(u.email).toLowerCase()))].sort()))
      .catch(() => setAccountEmails([]));
  }, [editing]);

  const bots = data?.bots || [];
  const defaultId = data?.default_id || "";

  async function save() {
    const problem = botFormProblem(form);
    if (problem) { toast.error(problem[0], problem[1]); return; }
    setBusy(true);
    try {
      // The allowlists are parsed HERE, once, rather than on every keystroke.
      // The claims are the server's to report, never the form's to send.
      const { allow_text, emails_text, claims, delete_failed, claims_paused_until,
              ...rest } = form;
      const lines = (text) => text.split("\n").map((x) => x.trim()).filter(Boolean);
      const body = { ...rest };
      if (allow_text !== undefined) body.allow = lines(allow_text);
      if (emails_text !== undefined) body.emails = lines(emails_text);
      const d = await api.post("/api/telegram/bots", body);
      setData(d);
      setForm(null);
      // Saving checks the token's shape only; nothing is sent to Telegram.
      toast.success("Telegram Bot Saved",
                    "Use Send Test Message to check that the bot can post "
                    + "into your chat.");
    } catch (e) {
      // A 400 is one of _clean's refusals; the checks above said the useful
      // part, and the server's sentence can quote what was pasted.
      toast.error("Could Not Save Telegram Bot",
                  e?.status === 400
                    ? "Check the information you entered and try again."
                    : friendlyError(e));
    } finally { setBusy(false); }
  }

  async function remove(bot) {
    // Named consequences when the bot answers for a system: that system's
    // Telegram steps and commands stop until another bot is assigned.
    const sysName = bot.system
      ? ((data?.systems || []).find((x) => x.slug === bot.system)?.name || bot.system)
      : "";
    if (!await confirm({
      title: "Delete this Telegram bot?",
      description: `"${bot.name}" will no longer be available for sending `
        + "notifications from this device. Workflows that depend on it may "
        + "stop sending Telegram messages."
        + (sysName ? ` It is assigned to ${sysName}: that system's Telegram `
           + "steps and commands stop until another bot is assigned." : ""),
      confirmLabel: "Delete Bot",
      destructive: true })) return;
    setBusy(true);
    try {
      setData(await api.post("/api/telegram/bots/delete", { id: bot.id }));
      toast.success("Telegram bot deleted");
    } catch (e) { toast.error("Could not delete the Telegram bot", friendlyError(e)); }
    finally { setBusy(false); }
  }

  async function unclaim(bot, claim) {
    if (!await confirm({
      title: "Remove this claim?",
      description: `${claimant(claim)} will no longer be able to use the `
        + `commands of "${bot.name}", and cannot claim ${claim.email} again `
        + `until you choose Allow again. ${claim.email} stays on the list, so `
        + "the right person can send it to the bot.",
      confirmLabel: "Remove Claim",
      destructive: true })) return;
    setBusy(true);
    try {
      setData(await api.post("/api/telegram/bots/unlink",
                             { id: bot.id, email: claim.email }));
      toast.success("Claim removed");
    } catch (e) { toast.error("Could not remove the claim", friendlyError(e)); }
    finally { setBusy(false); }
  }

  async function unblock(bot, claim, who) {
    if (!await confirm({
      title: "Allow this user again?",
      description: `${claimant(who)} will be able to claim ${claim.email} on `
        + `"${bot.name}" again by sending it to the bot.`,
      confirmLabel: "Allow Again" })) return;
    setBusy(true);
    try {
      setData(await api.post("/api/telegram/bots/unblock",
                             { id: bot.id, email: claim.email, user_id: who.user_id }));
      toast.success("User allowed again");
    } catch (e) { toast.error("Could not allow the user again", friendlyError(e)); }
    finally { setBusy(false); }
  }

  async function makeDefault(bot) {
    setBusy(true);
    try {
      setData(await api.post("/api/telegram/bots/default", { id: bot.id }));
      toast.success(`${bot.name} is now the default bot`);
    } catch (e) { toast.error("Could not set the default", friendlyError(e)); }
    finally { setBusy(false); }
  }

  async function test(bot) {
    // WHERE to prove it works. A bot no longer carries a chat -- each Telegram
    // node names who hears it -- so the button asks, unless this bot happens
    // to keep one for exactly this.
    let chat = bot.chat_id || "";
    if (!chat) {
      chat = await ask({
        title: `Send Test Message: ${bot.name}`,
        description: "Enter the chat ID to send the test message to. A "
          + "group's chat ID starts with -100; a public channel can use "
          + "@name. A person must send the bot a message first, and a group "
          + "must have the bot added to it. Chat IDs are normally learned "
          + "automatically when someone links their email address to the bot.",
        label: "Chat ID", defaultValue: "" });
      if (!chat) return;
      if (!CHAT_ID.test(String(chat).trim())) {
        toast.error("Invalid Chat ID", "Check the chat ID and enter a valid value.");
        return;
      }
    }
    setTesting(bot.id);
    try {
      await api.post("/api/telegram/bots/test", { id: bot.id, chat_id: chat });
      toast.success("Test message sent", `A test message was sent to chat ${chat}.`);
    } catch (e) {
      toast.error("Test Message Failed", testFailure(e));
    } finally { setTesting(""); }
  }

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  // The bot that holds the chosen system today, if it is another one: saving
  // takes the system off it (save_bot, one bot per system), so it is said
  // before Save rather than discovered after.
  const displaced = form?.system
    ? bots.find((b) => b.system === form.system && b.id !== form.id) : null;
  const systemName = (slug) =>
    (data?.systems || []).find((x) => x.slug === slug)?.name || slug;

  return (
    <SettingsPage
      title="Telegram Notifications"
      icon={Send}
      description={<>
        Connect Telegram bots to send alerts and notifications from this device.
        {/* Not the spec's "selected by name": a Telegram step posts as the
            bot assigned to its system (Workflow.jsx "Posts as"). */}
        <span style={{ display: "block", marginTop: 4 }}>
          Once a bot is assigned to a system, that system's workflows send
          their Telegram messages through it.
        </span>
      </>}
      actions={!form && (
        <button type="button" onClick={() => setForm({ ...BLANK })}
                className="inline-flex items-center gap-1.5 rounded-md border
                           px-3 py-1.5 text-sm font-medium hover:bg-muted">
          <Plus className="size-4" /> Add Telegram Bot
        </button>
      )}>

      {error && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5
                        p-4 text-sm text-destructive">
          {friendlyError(error) || "Could not load the Telegram bots."}
        </div>
      )}

      {form && (
        <SettingsSection
          title={form.id ? "Edit Bot" : "Add Telegram Bot"}>
          <div data-tg-howto className="mt-3 rounded-lg border bg-muted/40 p-3 text-sm">
            <p className="font-medium">How to create a Telegram bot</p>
            <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-muted-foreground">
              <li>Open Telegram and start a conversation with @BotFather.</li>
              <li>Follow the instructions to create a bot.</li>
              <li>Copy the bot token provided by Telegram and enter it below.</li>
              <li>Add the bot to the chat or group where you want to receive
                  notifications.</li>
              <li>Send a message to the bot or group if needed to initialize
                  the conversation.</li>
              <li>To let people use the bot's commands, list their email
                  addresses below. Each person then sends /start to the bot
                  and sends their address when the bot asks for it. In a
                  group they can also send /link followed by their
                  address.</li>
            </ol>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <label className="grid gap-1.5">
              <span className="text-sm font-medium">Bot Name</span>
              <input className="rounded-md border bg-background px-3 py-1.5 text-sm"
                     value={form.name} onChange={set("name")}
                     placeholder="e.g. Line Alerts" />
              <span className="text-xs text-muted-foreground">
                Choose a name to identify this bot in this list and on
                workflow Telegram steps. If left blank, the test
                chat ID is used as its name, or "Telegram bot" if there is
                none.
              </span>
            </label>
            {/* OPTIONAL, and not where the alerts go. A bot is who the box
                posts AS; who hears a message is named on the Telegram node
                that sends it, because one bot legitimately talks to the
                maintenance group, the shift channel and one supervisor. This
                is only where the Test button posts. */}
            {/* WHICH SYSTEM this bot belongs to, and it is exclusive both
                ways: a system has one bot, a bot serves one system. That is
                what lets an inbound /summary be answered at all -- the person
                typing it says nothing about which warehouse they mean, so the
                bot that received it has to. Unassigned is a real state: a bot
                mid-setup saves and tests, it simply appears on no canvas. */}
            <label className="grid gap-1.5">
              <span className="text-sm font-medium">Assigned System</span>
              <select className="rounded-md border bg-background px-3 py-1.5 text-sm"
                      value={form.system || ""} onChange={set("system")}>
                <option value="">Not assigned</option>
                {(data?.systems || []).map((sysm) => (
                  <option key={sysm.slug} value={sysm.slug}>{sysm.name}</option>
                ))}
                {/* A slug whose system is gone must not read as "Not assigned":
                    the bot is pointing at nothing, and this is where that is
                    visible. */}
                {form.system
                  && !(data?.systems || []).some((x) => x.slug === form.system) && (
                  <option value={form.system}>
                    {form.system} (this system no longer exists)
                  </option>
                )}
              </select>
              <span className="text-xs text-muted-foreground">
                Choose the system that will use this bot. Each system uses one
                bot and each bot serves one system, so assigning this bot moves
                it from its current assignment.
              </span>
              {displaced && (
                <span data-tg-displaced role="note"
                      className="text-xs font-medium text-amber-600 dark:text-amber-400">
                  {displaced.name} is currently assigned to{" "}
                  {systemName(form.system)}. Saving assigns this bot instead,
                  and {displaced.name} becomes Not assigned.
                </span>
              )}
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-medium">Test Chat ID (Optional)</span>
              <input className="rounded-md border bg-background px-3 py-1.5 text-sm"
                     value={form.chat_id} onChange={set("chat_id")}
                     placeholder="e.g. -1001234567890" />
              <span className="text-xs text-muted-foreground">
                Enter the chat ID where test messages should be sent. If left
                blank, Send Test Message asks for a chat ID each time. A
                workflow Telegram step that does not name its own chat also
                sends here.
              </span>
            </label>
            {/* WHO MAY ASK IT ANYTHING, by email address. The person sends
                their address to the bot and is linked to it; the admin never
                has to find a numeric id. Inbound only, like the chat ID list
                below, and both empty still means nobody. The address proves
                only that the sender KNOWS it, which is what the warning line
                says out loud. */}
            <div className="grid gap-2 sm:col-span-2" data-tg-emails>
              <span className="text-sm font-medium">Allowed Email Addresses</span>
              {/* The warning first (Tok Aba, 2026-10-14): it is the one thing
                  to know before typing an address in. */}
              <div data-tg-email-warn role="note" className="tg-notice">
                <div className="tg-notice-title">
                  <TriangleAlert size={16} aria-hidden="true" />
                  Important Security Notice
                </div>
                <p className="tg-notice-strong">
                  Anyone who knows an email address on this list can claim it
                  before its owner does.
                </p>
                <p>Only add email addresses of people you know and trust.</p>
              </div>
              <label className="grid gap-1.5">
                <span className="text-sm font-semibold">Email addresses</span>
                <span data-tg-email-help className="text-sm text-muted-foreground">
                  Enter one email address per line.
                </span>
                <textarea rows={4}
                          className="rounded-lg border bg-background px-3 py-2
                                     text-sm font-mono"
                          value={form.emails_text ?? (form.emails || []).join("\n")}
                          placeholder={"name@example.com\nanother@example.com"}
                          onChange={(e) => setForm((f) => ({
                            ...f, emails_text: e.target.value }))} />
              </label>
              <span data-tg-email-drop className="text-xs text-muted-foreground">
                Invalid email addresses will be removed when you save.
              </span>
              {accountEmails.length > 0 && (
                <select data-tg-account-pick aria-label="Add From Accounts"
                        className="w-fit rounded-md border bg-background px-3
                                   py-1.5 text-sm"
                        value=""
                        onChange={(e) => {
                          const pick = e.target.value;
                          if (!pick) return;
                          setForm((f) => {
                            const text = f.emails_text ?? (f.emails || []).join("\n");
                            const have = text.split("\n").map((x) => x.trim().toLowerCase());
                            if (have.includes(pick)) return f;
                            return { ...f, emails_text: (text.trim() ? text.replace(/\n*$/, "\n") : "") + pick };
                          });
                        }}>
                  <option value="">Add From Accounts</option>
                  {accountEmails.map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
              )}
              {/* The how-to, folded: it is what a person does on Telegram,
                  not what the admin types here. */}
              <details data-tg-link-howto className="text-xs text-muted-foreground">
                <summary className="cursor-pointer text-sm font-medium text-foreground">
                  How people link their email
                </summary>
                <ol className="mt-2 grid list-decimal gap-1 pl-5">
                  <li>A person on the list sends /start to the bot, then sends
                      their email address when the bot asks for it.</li>
                  <li>In a group, the address must be a reply to the bot's
                      question, /link name@example.com, or @BotName
                      name@example.com.</li>
                  <li>People who are already linked never need /start
                      again.</li>
                  <li>In a group, only linked people get answers.</li>
                  <li data-tg-privacy>With Telegram's privacy mode on (the
                      default), a bot that is not a group admin only sees
                      commands, mentions and replies in a group. Make the bot
                      a group admin with 'Delete messages' if it should delete
                      the message that holds the address.</li>
                </ol>
              </details>
            </div>
            {/* The chat ID list, unchanged, for bots set up before email
                claims and for a whole group that should be answered. Folded
                away because it is the expert's tool now, not the way in. */}
            <details data-tg-advanced className="sm:col-span-2">
              <summary className="cursor-pointer text-sm font-medium">
                Advanced
                {(form.allow || []).length > 0 && form.allow_text === undefined && (
                  <span className="ml-2 font-normal text-muted-foreground">
                    ({form.allow.length} allowed chat ID{form.allow.length === 1 ? "" : "s"})
                  </span>
                )}
              </summary>
            {/* WHO MAY ASK IT ANYTHING. Inbound only -- this has no bearing
                on where alerts go. Empty means nobody, deliberately: Telegram
                has no login, a bot's @name is discoverable, and a bot that
                answered whoever found it would be a data leak with a friendly
                face. There is no self-service door on purpose. */}
            <label className="mt-2 grid gap-1.5">
              <span className="text-sm font-medium">Allowed Chat IDs</span>
              {/* The RAW text is what is edited; the list is made from it when
                  the form is saved. Parsing on every keystroke made this box
                  impossible to type a second id into: Enter left an empty last
                  line, .filter(Boolean) dropped it, and the value re-rendered
                  with the newline gone -- so the cursor could never leave the
                  first line. A field that fights the Enter key is a field
                  nobody can put two things in. */}
              <textarea rows={3}
                        className="rounded-md border bg-background px-3 py-1.5
                                   text-sm font-mono"
                        value={form.allow_text ?? (form.allow || []).join("\n")}
                        placeholder={"123456789\n-1001234567890"}
                        onChange={(e) => setForm((f) => ({
                          ...f, allow_text: e.target.value }))} />
              <span data-tg-allow-help className="text-xs text-muted-foreground">
                Chat IDs are normally learned automatically when someone links
                their email address to the bot, so entering them by hand is
                only for special cases. Enter one chat ID per line. Everyone in
                these chats can send commands to this bot and receive
                responses to those commands, including the /start welcome,
                and never needs to link an address. Leave this list and
                Allowed Email Addresses empty to deny command access to all
                chats. When Allowed Email Addresses is not empty, a chat that
                is not set up yet can send /start. The bot then asks for an
                email address from that list. Anything else from an unknown
                chat is ignored. Lines that are not valid chat IDs are
                dropped when you save.
                <span className="mt-1 block">
                  These lists control access to bot commands only. They do not
                  affect where workflow notifications are sent.
                </span>
              </span>
            </label>
            </details>
            <label className="grid gap-1.5 sm:col-span-2">
              <span className="text-sm font-medium">Welcome Message (Optional)</span>
              <textarea rows={2}
                        className="rounded-md border bg-background px-3 py-1.5 text-sm"
                        value={form.welcome || ""} onChange={set("welcome")}
                        placeholder="Welcome! I can help you check system information and available commands." />
              <span className="text-xs text-muted-foreground">
                This message is shown when someone starts a conversation with
                the bot using /start. The bot lists the commands available in
                its assigned system underneath, so the list always matches the
                commands that system currently has.
              </span>
            </label>
            <label className="grid gap-1.5 sm:col-span-2">
              <span className="text-sm font-medium">
                Telegram Bot Token
                {form.id && (
                  <span className="ml-2 font-normal text-muted-foreground">
                    (leave blank to keep the saved token)
                  </span>
                )}
              </span>
              <PasswordInput native
                     className="rounded-md border bg-background px-3 py-1.5 text-sm"
                     autoComplete="new-password"
                     value={form.token} onChange={set("token")}
                     placeholder={form.id ? "Unchanged" : "Enter your bot token"} />
              <span className="text-xs text-muted-foreground">
                Copy the token provided by BotFather. Keep this token private.
                Anyone with access to it may be able to control your bot.
              </span>
            </label>
          </div>
          <div className="mt-4 flex items-center gap-2">
            <button type="button" disabled={busy} onClick={save}
                    className="rounded-md bg-primary px-3 py-1.5 text-sm
                               font-medium text-primary-foreground
                               disabled:opacity-50">
              {busy ? "Saving…" : "Save Telegram Bot"}
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
        title="Connected Bots"
        description={"Manage the Telegram bots this device can use to send "
          + "notifications."
          + (bots.length
            ? " The default bot is used wherever no specific bot is chosen."
            : "")}>
        {!data ? (
          <p className="mt-3 text-sm text-muted-foreground">Loading…</p>
        ) : !bots.length ? (
          <div data-tg-empty className="mt-3 text-sm">
            <p className="font-medium">No Telegram bots configured</p>
            <p className="mt-1 text-muted-foreground">
              Add a Telegram bot to send alerts from workflows to Telegram
              chats or groups.
            </p>
            {!form && (
              <button type="button" onClick={() => setForm({ ...BLANK })}
                      className="mt-3 inline-flex items-center gap-1.5 rounded-md
                                 border px-3 py-1.5 text-sm font-medium hover:bg-muted">
                <Plus className="size-4" /> Add Telegram Bot
              </button>
            )}
          </div>
        ) : (
          <ul className="mt-3 divide-y rounded-lg border">
            {bots.map((b) => (
              <li key={b.id} className="flex flex-wrap items-center gap-3 p-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{b.name}</span>
                    {b.id === defaultId && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-xs
                                       font-medium text-muted-foreground">
                        Default
                      </span>
                    )}
                    {/* Said out loud, because a bot with no token looks
                        identical to a working one until it fails to post. */}
                    {!b.has_token && (
                      <span className="rounded bg-destructive/10 px-1.5 py-0.5
                                       text-xs font-medium text-destructive">
                        No token
                      </span>
                    )}
                  </div>
                  <div className="truncate text-sm text-muted-foreground">
                    {b.system
                      ? `Assigned System: ${systemName(b.system)}`
                      : "Not assigned"}
                    {b.chat_id ? ` · Test chat ID ${b.chat_id}` : ""}
                    {` · ${accessSummary(b)}`}
                  </div>
                  {/* Who holds each listed address. Remove frees it: the
                      holder loses access on their next message and the
                      address waits, Unclaimed, for the right person. */}
                  {(b.claims || []).length > 0 && (
                    <ul data-tg-claims className="mt-2 space-y-1 text-sm">
                      {b.claims.map((c) => (
                        <li key={c.email} className="grid gap-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-xs">{c.email}</span>
                            {c.claimed ? (
                              <span data-tg-claimed-by
                                    className="flex flex-wrap items-center gap-2
                                               text-muted-foreground">
                                Claimed by <Claimant c={c} />
                              </span>
                            ) : (
                              <span className="text-muted-foreground">Unclaimed</span>
                            )}
                            {c.claimed && (
                              <button type="button" disabled={busy}
                                      onClick={() => unclaim(b, c)}
                                      aria-label={`Remove claim on ${c.email}`}
                                      className="rounded-md border px-2 py-0.5 text-xs
                                                 hover:bg-muted disabled:opacity-50">
                                Remove
                              </button>
                            )}
                          </div>
                          {/* Removed claimants stay blocked from this address
                              until the admin lets them back. */}
                          {(c.blocked || []).map((x) => (
                            <div key={x.user_id} data-tg-blocked
                                 className="flex flex-wrap items-center gap-2 pl-4
                                            text-muted-foreground">
                              <span className="text-xs">Blocked from claiming:</span>
                              <Claimant c={x} />
                              <button type="button" disabled={busy}
                                      onClick={() => unblock(b, c, x)}
                                      aria-label={`Allow Telegram user ${x.user_id} to claim ${c.email} again`}
                                      className="rounded-md border px-2 py-0.5 text-xs
                                                 hover:bg-muted disabled:opacity-50">
                                Allow again
                              </button>
                            </div>
                          ))}
                        </li>
                      ))}
                    </ul>
                  )}
                  {/* A group would not let the bot delete a message holding an
                      address, so it is still there for the group to read. */}
                  {b.claims_paused_until > 0 && (
                    <p data-tg-paused role="note"
                       className="mt-2 text-xs font-medium text-amber-600 dark:text-amber-400">
                      Email claims on this bot are paused until{" "}
                      {new Date(b.claims_paused_until * 1000).toLocaleTimeString(
                        [], { hour: "2-digit", minute: "2-digit" })}{" "}
                      after many failed attempts. People already linked are
                      not affected.
                    </p>
                  )}
                  {b.delete_failed && (
                    <p data-tg-delete-hint role="note"
                       className="mt-2 text-xs font-medium text-amber-600 dark:text-amber-400">
                      Make the bot a group admin with 'Delete messages' to hide
                      email addresses.
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button type="button" disabled={busy || testing === b.id}
                          onClick={() => test(b)}
                          className="inline-flex items-center gap-1.5 rounded-md
                                     border px-2 py-1 text-sm hover:bg-muted
                                     disabled:opacity-50">
                    {testing === b.id
                      ? <Loader2 className="size-4 animate-spin" />
                      : <Send className="size-4" />}
                    Send Test Message
                  </button>
                  {b.id !== defaultId && (
                    <button type="button" disabled={busy}
                            onClick={() => makeDefault(b)}
                            title="Make this the default bot"
                            aria-label={`Make ${b.name} the default bot`}
                            className="inline-flex items-center rounded-md border
                                       p-1.5 hover:bg-muted disabled:opacity-50">
                      <Star className="size-4" />
                    </button>
                  )}
                  <button type="button" disabled={busy}
                          onClick={() => setForm({ ...b, token: "" })}
                          className="rounded-md border px-2 py-1 text-sm
                                     hover:bg-muted disabled:opacity-50">
                    Edit Bot
                  </button>
                  <button type="button" disabled={busy} onClick={() => remove(b)}
                          title="Delete Bot"
                          aria-label={`Delete Bot ${b.name}`}
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
