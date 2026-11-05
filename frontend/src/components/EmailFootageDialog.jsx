// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// EmailFootageDialog - send a system's footage by email, once or forever.
//
// THREE ways to say what to send, because there are three ways people ask for
// it: the files they have ticked, everything in a date and time range, or the
// same export repeating on its own. The dialog opens on whichever the page
// already implies - a selection if there is one, the visible range if not -
// so the common case is still "type an address and send".
//
// The repeating one is the reason this dialog grew. Emailing evidence was a
// manual act, which is right for "send me this morning" and wrong for a shift
// handover or a weekly record: those want to arrive without anybody
// remembering to make them, and a person who has to remember is a person who
// forgets in the week it mattered. So Save sits beside Send - Save stores the
// schedule and sends nothing, Send posts what is on screen right now and
// stores nothing. Neither does the other's job, because an operator adjusting
// next month's time must not thereby post last month's evidence.
//
// It ASKS THE BOX what the selection weighs before offering to send it. Mail
// servers reject anything over roughly 25MB, and they do it AFTER a minute
// spent encoding attachments, so a size that will not fit is far better known
// now, in a sentence, than as a failure later. That is also why Send is
// disabled rather than optimistic: the number on screen is measured, not
// guessed.
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client.js";
import { useToast } from "./ui/Toast.jsx";
import { Field, Section } from "./ui/primitives.jsx";
import EmailListField from "./EmailListField.jsx";
import { useEscape } from "../hooks/use-escape.js";
import { useConfirm } from "./ui/ask.jsx";

export default function EmailFootageDialog({ onClose, media, from, to,
                                             selection, shownCount }) {
  const toast = useToast();
  // The backdrop closes on a click; Escape is what a keyboard has
  // instead, since a backdrop is not something anybody can tab to.
  useEscape(true, onClose);
  const isClip = media === "clip";
  // "What is ticked" is only a choice when something is.
  const [scope, setScope] = useState(selection.length ? "selection" : "range");
  // Times, not just dates: "Tuesday afternoon" is the actual request, and a
  // whole day of a busy line is far past what an email can carry.
  const [start, setStart] = useState(`${from}T00:00`);
  const [end, setEnd] = useState(`${to}T23:59`);
  const [both, setBoth] = useState(false);   // also include the other kind
  const [asZip, setAsZip] = useState(true);
  const [accounts, setAccounts] = useState([]);
  const [account, setAccount] = useState("");
  const [addr, setAddr] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  // The repeating export, loaded once and edited in place. `saved` is what
  // the box will actually do, shown back in its own words -- a schedule an
  // operator cannot read is one they cannot trust to run for a year.
  const [rep, setRep] = useState(null);
  const [saved, setSaved] = useState("");
  const confirm = useConfirm();
  const [saving, setSaving] = useState(false);
  const [window_, setWindow] = useState(null);   // [start, end] epoch seconds
  // WHERE it goes. Two independent switches rather than one picker: wanting
  // the record in an inbox AND on the shift group's phone is a normal ask,
  // and either alone is the common case.
  const [byMail, setByMail] = useState(true);
  const [byChat, setByChat] = useState(false);
  const [bots, setBots] = useState([]);
  const [bot, setBot] = useState("");
  const [chatId, setChatId] = useState("");

  useEffect(() => {
    api.get("/api/mail/accounts")
      .then((d) => {
        setAccounts(d.accounts || []);
        setAccount(d.default_id || "");
      })
      .catch(() => setAccounts([]));
    api.get("/api/telegram/bots")
      .then((d) => {
        setBots(d.bots || []);
        setBot(d.default_id || "");
      })
      .catch(() => setBots([]));
    api.get("/api/detector/footage/schedule")
      .then((d) => {
        setRep(d.schedule);
        setSaved(d.describe || "");
        // The saved schedule's destinations are the dialog's, so opening it
        // shows what is actually running rather than the defaults.
        if (d.schedule) {
          setByMail(!!d.schedule.send_email);
          setByChat(!!d.schedule.send_telegram);
          if (d.schedule.chat_id) setChatId(d.schedule.chat_id);
          if (d.schedule.bot) setBot(d.schedule.bot);
          if (d.schedule.to) setAddr(d.schedule.to);
        }
        // The window it would cover if it fired now. "Send now" on a
        // repeating export must send THAT period, not the date boxes above
        // it, or the preview would measure one thing and the button send
        // another.
        setWindow([d.next_window, d.next_end]);
      })
      .catch(() => setRep(null));
  }, []);

  const setRepField = (key) => (value) =>
    setRep((r) => ({ ...(r || {}), [key]: value }));

  const payload = useMemo(() => {
    if (scope === "selection") return { items: selection, zip: asZip };
    const kinds = both ? ["snapshot", "clip"]
                       : [isClip ? "clip" : "snapshot"];
    // A repeating export measures and sends the period it COVERS, so the
    // number above the button is the number that will be attached.
    if (scope === "repeat" && window_) {
      return { start: window_[0], end: window_[1], zip: asZip,
               kinds: (rep && rep.include_clips) || both
                 ? ["snapshot", "clip"] : ["snapshot"] };
    }
    return { start, end, zip: asZip, kinds };
  }, [scope, selection, start, end, both, isClip, asZip, window_, rep]);

  // Re-measured whenever the answer would change. Debounced, because the
  // range inputs fire per keystroke and each measure stats every file.
  useEffect(() => {
    let dead = false;
    setPreview(null);
    const t = setTimeout(() => {
      api.post("/api/detector/footage/email/preview", payload)
        .then((d) => { if (!dead) setPreview(d); })
        .catch((e) => {
          if (!dead) setPreview({ sendable: false, problem: e.message });
        });
    }, 350);
    return () => { dead = true; clearTimeout(t); };
  }, [payload]);

  async function send() {
    setBusy(true);
    try {
      const d = await api.post("/api/detector/footage/email",
                               { ...payload, to: addr, account, subject, body,
                                 email: byMail, telegram: byChat,
                                 chat_id: chatId, bot });
      toast.success("Footage sent", d.message);
      onClose();
    } catch (e) {
      toast.error("Could not send the footage", e.message);
    } finally { setBusy(false); }
  }

  // Closing throws away an unsaved repeating export -- a period, a
  // recipient list, a subject and a message somebody typed. Only asked when
  // there IS something to lose: a confirm on every close is a click people
  // learn to dismiss without reading, which is how the one that mattered gets
  // dismissed too.
  async function cancel() {
    if (dirty && !await confirm({
      title: "Close without saving?",
      description: "This repeating export has changes that have not been "
        + "saved. Closing now loses them; anything already running is "
        + "unaffected.",
      confirmText: "Discard changes",
      cancelText: "Keep editing",
    })) {
      return;
    }
    onClose();
  }

  async function saveSchedule(enabled) {
    setSaving(true);
    try {
      const d = await api.put("/api/detector/footage/schedule", {
        ...(rep || {}), enabled,
        // The recipient, the mailbox and the wording are the SAME fields the
        // manual send uses. Asking for them twice in one dialog would be two
        // addresses to keep in step and one of them silently wrong.
        to: addr, account, subject, body,
        send_email: byMail, send_telegram: byChat,
        chat_id: chatId, bot,
        as_zip: asZip, include_clips: both,
      });
      setRep(d.schedule);
      setSaved(d.describe || "");
      toast.success(enabled ? "Auto export saved"
                            : "Auto export switched off",
                    enabled ? d.describe : "Nothing will be sent on its own.");
    } catch (e) {
      toast.error("Could not save the schedule", e.message);
    } finally { setSaving(false); }
  }

  const hasTarget = (byMail && !!addr.trim()) || (byChat && !!chatId.trim());
  const canSend = hasTarget && !!preview?.sendable && !busy;
  // Saving needs no measurement: the size that matters is the one at the
  // moment it fires, months from now, and refusing to save today because
  // today's period is large would be answering the wrong question.
  // Save applies to the REPEATING export and nothing else: a date range and
  // a hand-picked selection are a query, deciding what Send now attaches.
  // There is nothing to store, which is why Save is off rather than missing.
  const canSave = scope === "repeat" && hasTarget && !!rep && !saving;
  // Is there anything to lose? Compared against the STORED schedule rather
  // than a flag set on every keystroke: a flag says "touched", and somebody
  // who typed a character and deleted it again should not be interrogated on
  // the way out. Only the fields Save actually sends are compared -- the date
  // boxes and the selection belong to Send now and are never stored.
  const dirty = scope === "repeat" && hasTarget && !!rep && (
    (rep.to || "") !== addr || (rep.account || "") !== account
    || (rep.subject || "") !== subject || (rep.body || "") !== body
    || !!rep.send_email !== byMail || !!rep.send_telegram !== byChat
    || (rep.chat_id || "") !== chatId || (rep.bot || "") !== bot
    || !!rep.as_zip !== asZip || !!rep.include_clips !== both);

  const saveWhy = scope !== "repeat"
    ? "Only a repeating export can be saved. Choose Auto to set one up; a date range is sent now, not stored."
    : (hasTarget ? "Store this schedule. Nothing is sent now."
                 : "A recipient is needed first");

  // PORTALLED TO THE BODY, and that is the whole fix for "the dim covers the
  // footage list and the dialog sits over it rather than over the window".
  //
  // `position: fixed` is only fixed to the VIEWPORT while no ancestor is a
  // containing block, and a transform, a filter or a `contain` anywhere up the
  // tree makes one -- the page's own entrance animation is enough while it
  // plays. Then `inset: 0` resolves against that ancestor: the scrim is the
  // size of the card, and "centred" means centred in the card. Rendering
  // outside the page entirely means no ancestor can do it, whatever CSS
  // arrives later.
  return createPortal(
    // `open` is not decoration: `.lightbox` alone is `display:none`, and the
    // class that reveals it is `.lightbox.open`. Without it this dialog
    // mounted, fetched, measured -- and painted nothing, so the Email button
    // looked dead.
    <div className="lightbox open" onClick={onClose}>
      <div className="panel-card rail-form" role="dialog" aria-modal="true"
           aria-label="Email footage"
           onClick={(e) => e.stopPropagation()}
           style={{ maxWidth: 560, width: "min(560px, calc(100vw - 32px))",
                    maxHeight: "calc(100vh - 48px)", overflowY: "auto" }}>
        <div className="section-title">Email footage</div>

        {!accounts.length ? (
          // Nothing else here can work without one, so it is the only thing
          // shown: a form whose Send can never succeed is worse than a
          // sentence saying why.
          <p className="muted small">
            No sending mailbox is set up. Add one under{" "}
            <b>Settings → Email</b> on the hub, then come back.
          </p>
        ) : (<>
          {/* Three questions, in the order they are asked: who gets it, what
              is in it, and what the message looks like. It was one flat stack
              of eight, so choosing an attachment format meant reading past
              the schedule to reach it. */}
          <Section title="Who gets it">
            <Field label="Send from">
              <select className="input" value={account}
                      onChange={(e) => setAccount(e.target.value)}>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}{a.name === a.sender ? "" : ` - ${a.sender}`}
                  </option>
                ))}
              </select>
            </Field>

            {/* One address per row; still sent and saved as one ", "
                string (EmailListField). */}
            <div className="ui-field" data-footage-to="">
              <span className="ui-field-label">To</span>
              <EmailListField label="To" value={addr} onChange={setAddr} />
            </div>

            {/* The two channels. Independent switches, because "email it to
                me and post it to the shift group" is one request, not two
                exports. Each is attempted and reported separately: a group
                that cannot be reached must not cancel the email. */}
            <label className="wf-check">
              <input type="checkbox" checked={byMail}
                     onChange={(e) => setByMail(e.target.checked)} />
              <span>Send it by <strong>email</strong></span>
            </label>
            <label className="wf-check"
                   title={bots.length ? undefined
                     : "No Telegram bot is set up - Settings → Telegram"}>
              <input type="checkbox" checked={byChat} disabled={!bots.length}
                     onChange={(e) => setByChat(e.target.checked)} />
              <span>
                Post it to <strong>Telegram</strong>
                {bots.length ? "" : " - no bot is set up yet"}
              </span>
            </label>

            {byChat && (
              <>
                <div className="wf-row">
                  <Field label="Bot" style={{ flex: "1 1 150px" }}>
                    <select className="input" value={bot}
                            onChange={(e) => setBot(e.target.value)}>
                      {bots.map((b) => (
                        <option key={b.id} value={b.id}>{b.name}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Chat id" style={{ flex: "1 1 150px" }}>
                    <input className="input" value={chatId}
                           onChange={(e) => setChatId(e.target.value)}
                           placeholder="-1001234567890" />
                  </Field>
                </div>
                <p className="muted small">
                  A NUMBER, not a name: a group’s starts with −100, a person’s
                  is their own id, and <code className="mono">@name</code> works
                  for a public channel only - a private person has no @ address
                  a bot can send to. A person must message the bot once before
                  it may message them, and the bot must be in a group before it
                  can post there; take the number from{" "}
                  <code className="mono">
                    api.telegram.org/bot&lt;token&gt;/getUpdates
                  </code>{" "}
                  afterwards.
                </p>
                <p className="muted small">
                  Telegram takes one file per message, so it always gets the
                  zip - two hundred separate uploads would be two hundred
                  notifications. Its size limit is its own, not the mail
                  server’s.
                </p>
              </>
            )}
          </Section>

          <Section title="What to send">
            <Field label="Which footage">
              <div className="rail-seg">
                <button className={"btn btn-sm"
                                   + (scope === "selection" ? " btn-accent" : "")}
                        disabled={!selection.length}
                        title={selection.length ? undefined
                                                : "Tick some footage first"}
                        onClick={() => setScope("selection")}>
                  Selected ({selection.length})
                </button>
                <button className={"btn btn-sm"
                                   + (scope === "range" ? " btn-accent" : "")}
                        onClick={() => setScope("range")}>
                  Date &amp; time range
                </button>
                <button className={"btn btn-sm"
                                   + (scope === "repeat" ? " btn-accent" : "")}
                        disabled={!rep}
                        onClick={() => setScope("repeat")}>
                  Auto
                </button>
              </div>
            </Field>

            {scope === "repeat" && rep && (
              <>
                {/* A repeating export covers the period that just ENDED --
                    yesterday for a daily, last week for a weekly -- rather
                    than "the last 24 hours" from the moment it fires. A window
                    that slides with the clock overlaps its neighbours, so two
                    reports share hours and a gap between them is invisible. */}
                <div className="wf-row">
                  <Field label="How often" style={{ flex: "1 1 150px" }}>
                    <select className="input" value={rep.every || "daily"}
                            onChange={(e) => setRepField("every")(e.target.value)}>
                      <option value="hourly">Every hour</option>
                      <option value="daily">Every day</option>
                      <option value="weekly">Every week</option>
                      <option value="monthly">Every month</option>
                      <option value="months">Every N months</option>
                    </select>
                  </Field>

                  {rep.every === "months" && (
                    <Field label="How many months" style={{ flex: "1 1 120px" }}>
                      <input className="input" type="number" min="1" max="24"
                             value={rep.every_n ?? 1}
                             onChange={(e) =>
                               setRepField("every_n")(+e.target.value)} />
                    </Field>
                  )}

                  {rep.every === "hourly" ? (
                    <Field label="Minutes past the hour" style={{ flex: "1 1 120px" }}>
                      <input className="input" type="number" min="0" max="59"
                             value={rep.minute ?? 0}
                             onChange={(e) =>
                               setRepField("minute")(+e.target.value)} />
                    </Field>
                  ) : (
                    <Field label="Time" style={{ flex: "1 1 120px" }}>
                      <input className="input" type="time" value={rep.at || "08:00"}
                             onChange={(e) => setRepField("at")(e.target.value)} />
                    </Field>
                  )}

                  {rep.every === "weekly" && (
                    <Field label="Day" style={{ flex: "1 1 130px" }}>
                      <select className="input" value={rep.weekday ?? 0}
                              onChange={(e) =>
                                setRepField("weekday")(+e.target.value)}>
                        {["Monday", "Tuesday", "Wednesday", "Thursday",
                          "Friday", "Saturday", "Sunday"].map((d, i) => (
                          <option key={d} value={i}>{d}</option>
                        ))}
                      </select>
                    </Field>
                  )}

                  {(rep.every === "monthly" || rep.every === "months") && (
                    <Field label="Day of the month" style={{ flex: "1 1 130px" }}>
                      <select className="input" value={rep.day ?? 1}
                              onChange={(e) => setRepField("day")(+e.target.value)}>
                        {/* 0 first, because "the last day" is what a month-end
                            report means and picking 31 quietly skips the
                            eleven months that do not have one. */}
                        <option value={0}>The last day</option>
                        {Array.from({ length: 31 }, (_, i) => (
                          <option key={i + 1} value={i + 1}>{i + 1}</option>
                        ))}
                      </select>
                    </Field>
                  )}
                </div>

                <label className="wf-check">
                  <input type="checkbox" checked={!!both}
                         onChange={(e) => setBoth(e.target.checked)} />
                  <span>Include clips as well</span>
                </label>

                {/* What it will actually do, in the box the node inspectors
                    use for the same job: a statement of fact, not a field. */}
                <div className="rail-brief">
                  <div className="rail-brief-row">
                    <span className="muted small">{rep.enabled ? "running" : "not yet"}</span>
                    <span className="small">
                      {rep.enabled
                        ? <>{saved} - it covers the period that just ended, and
                            uses the address, mailbox and wording above.</>
                        : <>Press <b>Save</b> to start it. Saving never sends,
                            so setting this up posts nothing.</>}
                    </span>
                  </div>
                  {rep.last_result && (
                    <div className="rail-brief-row">
                      <span className="muted small">last</span>
                      <span className="small">{rep.last_result}</span>
                    </div>
                  )}
                </div>
              </>
            )}

            {scope === "range" && (
              <>
                <div className="wf-row">
                  <Field label="From" style={{ flex: "1 1 170px" }}>
                    <input className="input" type="datetime-local" value={start}
                           onChange={(e) => setStart(e.target.value)} />
                  </Field>
                  <Field label="To" style={{ flex: "1 1 170px" }}>
                    <input className="input" type="datetime-local" value={end}
                           onChange={(e) => setEnd(e.target.value)} />
                  </Field>
                </div>
                <label className="wf-check">
                  <input type="checkbox" checked={both}
                         onChange={(e) => setBoth(e.target.checked)} />
                  <span>Include {isClip ? "photos" : "clips"} as well</span>
                </label>
              </>
            )}
          </Section>

          <Section title="The message" open={false}
                   count={[subject, body].filter((v) => String(v || "").trim()).length
                          + (asZip ? 0 : 1)}>
            <Field label="Attach as">
              <div className="rail-seg">
                <button className={"btn btn-sm" + (asZip ? " btn-accent" : "")}
                        onClick={() => setAsZip(true)}>One zip file</button>
                <button className={"btn btn-sm" + (!asZip ? " btn-accent" : "")}
                        onClick={() => setAsZip(false)}>Separate files</button>
              </div>
            </Field>
            <p className="muted small">
              {asZip
                ? "One attachment. Photos and clips are already compressed, so the zip is for tidiness, not size."
                : "One attachment per file - easier to open, but a long list clutters the message."}
            </p>

            <Field label="Subject" hint="Blank uses the system name and the period.">
              <input className="input" value={subject}
                     onChange={(e) => setSubject(e.target.value)}
                     placeholder="blank uses the system and the period" />
            </Field>
            <Field label="Message" hint="Optional. A note for whoever receives it.">
              <textarea className="input" rows={2} value={body}
                        onChange={(e) => setBody(e.target.value)}
                        placeholder="A note for whoever receives it" />
            </Field>
          </Section>

          {/* Measured, not guessed - and it decides whether Send is enabled,
              so it sits directly above the button. */}
          <div className="rail-brief" style={{ marginTop: 0 }}>
            {!preview ? (
              <span className="muted small">Measuring…</span>
            ) : preview.sendable ? (
              <span className="small">
                {preview.count} file{preview.count === 1 ? "" : "s"} ·{" "}
                <b>{preview.human}</b>
                <span className="muted"> of {preview.limit_human}</span>
              </span>
            ) : (
              <span className="small c-danger">{preview.problem}</span>
            )}
          </div>

          {/* Save and Send are separate buttons doing separate things, and
              neither is the other's confirmation. Save stores the repeat and
              posts nothing; Send posts what is on screen now and stores
              nothing. Folding them together would mean an operator moving
              next month's send time thereby emails last month's evidence. */}
          <div className="rail-foot">
            <span className="muted small">{shownCount} shown on this page</span>
            <div style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={cancel}
                    disabled={busy || saving}>
              Cancel
            </button>
            {scope === "repeat" && rep?.enabled && (
              <button className="btn btn-sm btn-danger" disabled={saving}
                      onClick={() => saveSchedule(false)}>
                Stop auto
              </button>
            )}
{/* Shown in every mode, disabled where it cannot apply, rather
                than appearing only in Auto. Hidden, there was no way to learn
                it existed or why -- somebody setting a date range looked for
                a Save, found Cancel and Send now, and reasonably concluded
                the dialog had lost their changes. A greyed button that says
                why answers that without anyone being asked. */}
            <button className="btn btn-sm" disabled={!canSave}
                    title={saveWhy}
                    onClick={() => saveSchedule(true)}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button className="btn btn-sm btn-accent" disabled={!canSend}
                    title={scope === "repeat"
                      ? "Send the period this schedule covers, once, now"
                      : undefined}
                    onClick={send}>
              {busy ? "Sending…" : "Send now"}
            </button>
          </div>
        </>)}
      </div>
    </div>,
    document.body);
}
