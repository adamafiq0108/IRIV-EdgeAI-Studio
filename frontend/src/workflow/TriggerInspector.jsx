// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// TriggerInspector - the Trigger node's settings (output.trigger), laid out
// the way Tok Aba's mockup has it (2026-10-14):
//
//   1. Trigger identity      the Output name
//   2. When should it fire?  ONE firing choice
//   3. What should happen?   opt-in rows - Raise an alert, Highlight objects,
//                            Highlight the zone, Payload options (with "Keep
//                            the value on": how long the output value stays
//                            on after it fires) - each
//                            revealing its own settings when ticked, each
//                            saying in one line what it is set to
//   Advanced settings        (collapsed) the duplicate guard and object
//                            tracking notes
//
// A CANVAS change only. Every control writes the same stored field it always
// wrote, so every saved Trigger compiles exactly as before
// (tests/smoke_triggergolden.py) and the node keeps the same ports under the
// same conditions (nodes.jsx takesZone / takesOid / takesCurOid /
// takesCurValue read d.zone_color, d.draw, d.box_in_zone, d.alert,
// d.repeat, d.rate, d.dup_guard, untouched here).
//
// THE FIRING CHOICE. There used to be two overlapping questions: the alarm's
// "Raise again" (d.repeat: every cooldown / only for a new object) and the
// Advanced "How often it can fire" (d.rate: every time / at most once every
// N s / once per object), the second overriding the first. They are one
// radio list now (Firing), read from and written to those same fields:
//
//   Every time               d.rate ""             (d.repeat not new_object)
//   At most once every N s   d.rate "cooldown"     d.rate_s
//   Once per object          d.rate "once_per_oid"
//   When a new object joins  d.rate ""  + d.alert + d.repeat "new_object"
//
// The rate wins when it is set, as it always did on the server, so a Trigger
// saved with both opens on its rate. The alert's own cooldown (d.cooldown_s,
// "raise again every N s while it holds") belongs to Every time and sits in
// the alert row.
import { useState } from "react";
import { Activity, Bell, ChevronDown, CircleCheck, Clock, Database, RefreshCw, Scan,
         SlidersHorizontal, SquareDashed, Timer, UserPlus } from "lucide-react";
import { InfoHint } from "../components/ui/InfoHint.jsx";
import { TypedInput } from "./TypedInput.jsx";
import { holdMode, triggerWatchdog } from "./nodes.jsx";

const DEFAULT_BOX_COLOR = "#FF0000";

/** Label + control, the inspector's own shape (Workflow.jsx Field). */
function Field({ label, hint, children }) {
  return (
    <label className="wf-field">
      <span className="wf-field-label">
        {label}
        {hint ? <InfoHint label={`About ${label}`}>{hint}</InfoHint> : null}
      </span>
      {children}
    </label>
  );
}

//: The firing choices, in Tok Aba's words (2026-10-14 reference): a card
//: each, an icon, a title and one line.
export const FIRING = [
  { id: "every", label: "Every time", icon: CircleCheck,
    about: "Fire whenever the condition triggers." },
  { id: "rate", label: "At most once every N seconds", icon: Clock,
    about: "Limit how frequently new trigger events can fire." },
  { id: "per_object", label: "Once per object id (OID)", icon: Scan,
    about: "Fire only once for each tracked object." },
  { id: "new_object", label: "When a new object id (OID) detected.", icon: UserPlus,
    about: "Fire when a newly detected object enters the tracked set." },
];

/** Which firing choice a saved Trigger is - the same reading the server
 *  makes: a rate wins; without one, an alert set to "only for a new object"
 *  is the watchdog; anything else fires every time. */
export function triggerFiring(d) {
  d = d || {};
  if (d.rate === "cooldown") return "rate";
  if (d.rate === "once_per_oid") return "per_object";
  if (!d.rate && d.alert && d.repeat === "new_object") return "new_object";
  return "every";
}

/** The fields a firing choice writes - only the ones the old two controls
 *  wrote (d.rate, d.repeat), nothing renamed. */
export function firingPatch(choice, d) {
  d = d || {};
  if (choice === "rate") return { rate: "cooldown" };
  if (choice === "per_object") return { rate: "once_per_oid" };
  if (choice === "new_object") return { rate: "", repeat: "new_object" };
  // Every time: the rate off, and an alarm that was a watchdog back on its
  // timer (otherwise it would read as the watchdog again).
  return d.repeat === "new_object" ? { rate: "", repeat: "cooldown" } : { rate: "" };
}

//: How the output behaves after the trigger fires ("Output behavior"):
//: d.out_hold (+ d.out_hold_s), as before.
export const HOLDS = [
  { id: "realtime", label: "Follow the condition (live)", icon: Activity,
    about: "Output stays active while the condition is true, then turns off." },
  { id: "interval", label: "Re-check every N seconds", icon: RefreshCw,
    about: "Re-evaluate the condition at a specified interval." },
  { id: "hold", label: "Stay active for N seconds", icon: Timer,
    about: "Keep the output active for a set duration, then turn it off." },
];

/** A radio list drawn as cards: a bordered group, one row per option (icon,
 *  bold title, one line, a reason under it when it cannot be chosen). Real
 *  radio semantics: role radiogroup / radio, aria-checked, one Tab stop (the
 *  chosen option), arrow keys move AND choose, skipping disabled ones.
 *  `extra(id)` is drawn directly under the chosen option (its N-seconds
 *  field); it sits outside the radio element so it can hold an input. */
export function RadioCards({ id, title, sub, options, value, onPick, editable,
                             extra = null }) {
  const enabled = options.filter((o) => !o.disabled);
  const move = (from, step, e) => {
    if (!enabled.length) return;
    e.preventDefault();
    // Held now: React clears currentTarget once the handler returns.
    const host = e.currentTarget.closest("[role=radiogroup]");
    const at = enabled.findIndex((o) => o.id === from);
    const next = enabled[(at + step + enabled.length) % enabled.length];
    onPick(next.id);
    requestAnimationFrame(() => host?.querySelector(`[data-radio="${next.id}"]`)?.focus());
  };
  return (
    <div className="rc" data-rc={id}>
      <div className="rc-head" id={`rc-${id}`}>
        <strong className="small">{title}</strong>
        {sub && <span className="muted small" style={{ display: "block" }}>{sub}</span>}
      </div>
      <div role="radiogroup" aria-labelledby={`rc-${id}`} className="rc-group">
        {options.map((o) => {
          const on = value === o.id;
          const Icon = o.icon;
          return (
            <div key={o.id} className={"rc-item" + (on ? " is-on" : "")}>
              <div role="radio" aria-checked={on} data-radio={o.id}
                   aria-disabled={o.disabled || !editable || undefined}
                   tabIndex={on && editable ? 0 : -1} className="rc-opt"
                   onClick={() => { if (!o.disabled && editable && !on) onPick(o.id); }}
                   onKeyDown={(e) => {
                     if (!editable) return;
                     if (e.key === "ArrowDown" || e.key === "ArrowRight") move(value, 1, e);
                     else if (e.key === "ArrowUp" || e.key === "ArrowLeft") move(value, -1, e);
                     else if ((e.key === " " || e.key === "Enter") && !o.disabled && !on) {
                       e.preventDefault(); onPick(o.id);
                     }
                   }}>
                <Icon size={16} aria-hidden="true" className="rc-icon" />
                <span className="rc-text">
                  <span className="rc-title">{o.label}</span>
                  <span className="muted small rc-about">{o.about}</span>
                  {o.disabled && o.reason && (
                    <span className="small rc-reason">{o.reason}</span>
                  )}
                </span>
              </div>
              {on && extra && extra(o.id)}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const SEVERITY = { info: "note", warn: "alert", crit: "critical" };

const secsOk = (v, low) => {
  const n = Number(v);
  return v !== "" && Number.isFinite(n) && n >= low && n <= 86400;
};

/** One "What should happen?" row: an opt-in tick, a title, one line that is
 *  the description while off and the SUMMARY of what is set while on, an
 *  "Optional" badge, and the row's own settings beneath it when opened. */
function OptRow({ id, icon: Icon, title, about, summary, on, onTick, editable,
                  defaultOpen = false, children }) {
  // `defaultOpen`: a saved setting that changes behaviour and has no tick of
  // its own (a payload duration) opens the row, so it is never hidden.
  const [open, setOpen] = useState(defaultOpen);
  const tickable = typeof onTick === "function";
  const showBody = tickable ? on && open : open;
  return (
    <div className={"trg-row" + (on ? " is-on" : "")} data-trg-row={id}>
      <div className="trg-row-head">
        <Icon size={16} aria-hidden="true" className="trg-row-icon" />
        {tickable && (
          <input type="checkbox" checked={!!on} disabled={!editable}
                 aria-label={title}
                 onChange={(e) => { onTick(e.target.checked); setOpen(e.target.checked); }} />
        )}
        <div className="trg-row-text">
          <span className="trg-row-title">
            <strong className="small">{title}</strong>
            <span className="trg-badge">Optional</span>
          </span>
          <span className="muted small trg-row-line" data-trg-line="">
            {on && summary ? summary : about}
          </span>
        </div>
        {(!tickable || on) && (
          <button type="button" className="trg-row-more" aria-expanded={showBody}
                  aria-label={`${showBody ? "Hide" : "Show"} ${title} settings`}
                  onClick={() => setOpen((v) => !v)}>
            <ChevronDown size={16} aria-hidden="true" />
          </button>
        )}
      </div>
      {showBody && <div className="trg-row-body">{children}</div>}
    </div>
  );
}

/** The duplicate guard (Tok Aba, 2026-10-13), as it was: skip pushing an
 *  object the Data Table already holds. Ticking it puts the Current OID port
 *  on the node (and, on a timer, Current value); the compile reads the WIRES. */
function GuardFields({ d, editable, onChange, wired }) {
  const watchdog = triggerWatchdog(d);
  const on = !!d.dup_guard || wired("cur_oid") || wired("cur_value");
  const limit = d.dup_threshold ?? 0;
  const limitOk = limit !== "" && Number.isFinite(Number(limit)) && Number(limit) >= 0;
  return (
    <>
      <label className="wf-check" style={{ marginTop: 6 }}>
        <input type="checkbox" checked={!!d.dup_guard} disabled={!editable}
               onChange={(e) => onChange({ dup_guard: e.target.checked })} />
        <span className="small">Skip an object the table already holds</span>
        <InfoHint label="About skipping a stored object">
          Wire the object ID column of your Data Table (the dot on the right
          of the column) into <strong>Current OID</strong>. When the object
          that sets this Trigger off is the one already saved there, it is
          not sent again.
          {watchdog
            ? " In this mode (each object once) it is never sent again, whatever its value."
            : " Also wire the value column into Current value: the same object is sent again only when its value has changed by more than the threshold below."}
        </InfoHint>
      </label>
      {on && (
        <p className="muted small" style={{ marginTop: -4 }}>
          {wired("cur_oid")
            ? (watchdog
              ? <>The object already saved in the table is not sent again.</>
              : wired("cur_value")
                ? <>The object already saved is sent again only when its value
                    moves by more than the threshold.</>
                : <>The object already saved is not sent again. Wire the
                    value column into <strong>Current value</strong> to send
                    it again when its value changes.</>)
            : <span className="c-warn">
                Wire your table&apos;s object ID column into the
                <strong> Current OID</strong> port on the node.
              </span>}
        </p>
      )}
      {on && !watchdog && (
        <Field label="Total difference threshold"
               hint="How much the value must change, up or down, before the same object is sent again. 0 means any change at all counts; the same value is never sent twice.">
          <input className="input" type="number" min="0" step="any"
                 value={limit} disabled={!editable}
                 aria-label="Total difference threshold"
                 onChange={(e) => onChange({ dup_threshold: e.target.value === ""
                   ? "" : +e.target.value })} />
        </Field>
      )}
      {on && !watchdog && !limitOk && (
        <p className="small c-warn">The threshold must be a number, 0 or more.</p>
      )}
    </>
  );
}

/** The Trigger's settings. `oidTracked`: a rule upstream has OID ticked (or
 *  the OID port is wired) - what Once per object needs. `oidWired`: a wire
 *  is on the Trigger's OID port - what the watchdog needs. `boxZone` /
 *  `triggerZone`: the zone the box option and the zone colour would use. */
export default function TriggerFields({ d, editable, onChange, node, edges,
                                        oidTracked, oidWired, boxZone, triggerZone }) {
  const wired = (h) => (edges || []).some((e) => e.target === node.id
    && (e.targetHandle || "") === h);
  const firing = triggerFiring(d);
  const hold = holdMode(d);
  const holdLow = String(d.out_hold || "").trim() ? 1 : 0;
  const name = String(d.name || "").trim();
  const shown = (v) => String(v ?? "").slice(0, 16);

  const alertSummary = `Alert: ${SEVERITY[d.severity || "warn"] || d.severity}, `
    + `'${d.message || name || "no message"}'`
    + (firing === "every" ? `, again every ${d.cooldown_s ?? 30} s` : "");
  const boxSummary = `Boxes: ${(d.box_color || DEFAULT_BOX_COLOR).toUpperCase()}`
    + (d.box_in_zone && boxZone ? `, inside ${boxZone.name}` : ", every object of the rule's classes");
  const zoneSummary = `Zone: ${String(d.zone_color || "").toUpperCase()}`
    + (triggerZone ? `, ${triggerZone.name}` : ", no zone wired yet");
  const payloadSummary = (d.payload_type || d.idle_type)
    ? `Sends ${d.payload_type ? (d.payload_type === "timestamp" ? "the time" : shown(d.payload)) : "1"}`
      + ` when triggered, ${d.idle_type ? (d.idle_type === "timestamp" ? "the time" : shown(d.idle_payload)) : "0"} when not triggered`
    : "Sends 1 when triggered, 0 when not triggered";
  // ...and how long it stays on, so a duration is never set unseen.
  const holdSummary = hold === "interval" ? `re-checked every ${d.out_hold_s ?? 0} s`
    : hold === "hold" ? `stays active for ${d.out_hold_s ?? 0} s` : "live";
  const payloadSet = !!(d.payload_type || d.idle_type) || hold !== "realtime";

  // `label` names the value field (and its aria-label); the tick says what
  // it does. The "otherwise" is what is sent while unticked: 1 when
  // triggered, 0 when not (core/services/trigger_out.py defaults).
  const typed = (tkey, vkey, label, tick, start) => (
    <>
      <label className="wf-check">
        <input type="checkbox" checked={!!d[tkey]} disabled={!editable}
               onChange={(e) => onChange(e.target.checked
                 ? { [tkey]: "number", [vkey]: start }
                 : { [tkey]: "", [vkey]: "" })} />
        <span className="small">{tick} (otherwise {start})</span>
      </label>
      {!!d[tkey] && (
        <div className="wf-field">
          <span className="wf-field-label">{label}</span>
          <TypedInput type={d[tkey]} value={d[vkey] ?? ""} disabled={!editable}
                      label={label}
                      onChange={({ type, value }) => onChange({ [tkey]: type, [vkey]: value })} />
        </div>
      )}
    </>
  );

  return (
    <div className="trg" data-trigger-inspector="">
      <p className="muted small trg-sub">Create a named event when a condition is met.</p>

      {/* 1 ---------------------------------------------------------------- */}
      <section className="trg-sec" aria-labelledby={`trg1-${node.id}`}>
        <h4 id={`trg1-${node.id}`} className="trg-h">1. Trigger identity</h4>
        <Field label="Output name">
          <input className="input" value={d.name || ""} disabled={!editable}
                 placeholder="e.g. Overcrowded, Door left open"
                 onChange={(e) => onChange({ name: e.target.value })} />
        </Field>
        <p className="muted small trg-hint">
          Used to identify this trigger in the workflow and shown in alerts.
        </p>
        {!name && (
          <p className="small c-warn">
            A Trigger needs a name - it is the one thing here nothing else
            can work out for you.
          </p>
        )}
      </section>

      {/* 2 ---------------------------------------------------------------- */}
      <section className="trg-sec" aria-labelledby={`trg2-${node.id}`}>
        <h4 id={`trg2-${node.id}`} className="trg-h">2. When should it fire?</h4>
        <RadioCards id={`fire-${node.id}`} title="Firing" editable={editable}
                    sub="Controls how often a trigger event can happen."
                    value={firing} onPick={(id) => onChange(firingPatch(id, d))}
                    options={FIRING.map((f) => ({
                      ...f,
                      disabled: (f.id === "per_object" && !oidTracked && firing !== "per_object")
                        || (f.id === "new_object" && !d.alert && firing !== "new_object"),
                      reason: f.id === "per_object" ? "Needs OID on the rule."
                        : f.id === "new_object" ? "Needs Raise an alert." : "",
                    }))}
                    extra={(id) => (
                      <div className="rc-extra">
                        {id === "rate" && (
                          <>
                            <Field label="Seconds between two fires">
                              <input className="input" type="number" min="1" max="86400" step="1"
                                     value={d.rate_s ?? 10} disabled={!editable} data-trg-rate-s=""
                                     onChange={(e) => onChange({ rate_s: e.target.value === ""
                                       ? "" : +e.target.value })} />
                            </Field>
                            {!secsOk(d.rate_s ?? 10, 1) && (
                              <p className="small c-warn">
                                The time between two fires must be between 1 and 86400 seconds.
                              </p>
                            )}
                            <p className="muted small trg-hint">
                              Applies to the alert and the payload alike.
                            </p>
                          </>
                        )}
                        {id === "per_object" && (oidTracked
                          ? <p className="muted small trg-hint">
                              An object is remembered while it is in view and for 10
                              minutes after it leaves; the app starting again numbers
                              objects afresh.
                            </p>
                          : <p className="small c-warn">
                              Once per object id (OID) needs to know which object - tick{" "}
                              <strong>OID</strong> on the detection rule this Trigger watches.
                            </p>)}
                        {id === "new_object" && (
                          <p className={oidWired ? "muted small trg-hint" : "small c-warn"}>
                            {oidWired
                              ? "Raises the alert once, then again only for an object not already in it. The rule's OID is wired in, so it knows who is new."
                              : <>Needs to know who: tick <strong>OID</strong> on the rule this
                                  Trigger watches and wire its OID output into this
                                  Trigger&apos;s <strong> OID</strong> port (it appears on the
                                  node while this is chosen).</>}
                          </p>
                        )}
                      </div>
                    )} />
      </section>

      {/* 3 ---------------------------------------------------------------- */}
      <section className="trg-sec" aria-labelledby={`trg3-${node.id}`}>
        <h4 id={`trg3-${node.id}`} className="trg-h">3. What should happen?</h4>
        <p className="muted small trg-hint">
          Any, all or none. With none ticked it is still a named moment a Data
          Table write or a Snapshot can hang off.
        </p>

        <OptRow id="alert" icon={Bell} title="Raise an alert" editable={editable}
                about="Choose the alert severity and optional message."
                summary={alertSummary} on={!!d.alert}
                onTick={(on) => onChange({ alert: on })}>
          <Field label="Severity">
            <select className="input" value={d.severity || "warn"} disabled={!editable}
                    onChange={(e) => onChange({ severity: e.target.value })}>
              <option value="info">note (info)</option>
              <option value="warn">alert (warn)</option>
              <option value="crit">violation (critical)</option>
            </select>
          </Field>
          <Field label="Message (optional)">
            <input className="input" value={d.message || ""} disabled={!editable}
                   placeholder={d.name || "e.g. too many people in the bay"}
                   onChange={(e) => onChange({ message: e.target.value })} />
          </Field>
          {firing === "every" ? (
            <Field label="Raise again every (s)"
                   hint="While the moment keeps holding, the alert is raised again this often. Choose When a new object joins above to raise it per object instead.">
              <input className="input" type="number" min="0" step="1"
                     value={d.cooldown_s ?? 30} disabled={!editable}
                     onChange={(e) => onChange({ cooldown_s: +e.target.value })} />
            </Field>
          ) : (
            <p className="muted small trg-hint">
              How often it raises follows <strong>When should it fire?</strong>
            </p>
          )}
        </OptRow>

        {/* Visual behaviour, kept apart from the event's: which THING is
            wrong (its bounding box) and WHERE (the zone). Ticking the box
            CHOOSES the colour, so a ticked box never carries an empty one. */}
        <OptRow id="boxes" icon={Scan} title="Highlight objects" editable={editable}
                about="Configure bounding-box colour and zone behavior."
                summary={boxSummary} on={!!d.draw}
                onTick={(on) => onChange({ draw: on,
                  box_color: on ? (d.box_color || DEFAULT_BOX_COLOR) : d.box_color })}>
          <Field label="Bounding box colour"
                 hint="What the objects of the rule's classes are drawn in, while it holds - every one of them in view, unless Only boxes inside the zone is ticked. Other classes keep their own colour.">
            <input type="color" value={d.box_color || DEFAULT_BOX_COLOR} disabled={!editable}
                   onChange={(e) => onChange({ box_color: e.target.value.toUpperCase() })} />
          </Field>
          {boxZone && (
            <>
              <label className="wf-check">
                <input type="checkbox" checked={!!d.box_in_zone} disabled={!editable}
                       onChange={(e) => onChange({ box_in_zone: e.target.checked })} />
                <span className="small">Only boxes inside the zone</span>
              </label>
              <p className="muted small" style={{ marginTop: -4 }}>
                {d.box_in_zone
                  ? <>Only the objects inside <strong>{boxZone.name}</strong>
                      {" "}take this colour; the rest keep their own.</>
                  : <>Every object of the rule&apos;s classes in view takes this
                      colour while it holds. Tick to keep it to{" "}
                      <strong>{boxZone.name}</strong>.</>}
              </p>
            </>
          )}
        </OptRow>

        <OptRow id="zone" icon={SquareDashed} title="Highlight the zone" editable={editable}
                about="Colour the zone while the rule holds."
                summary={zoneSummary} on={!!d.zone_color}
                onTick={(on) => onChange({ zone_color: on ? DEFAULT_BOX_COLOR : "" })}>
          <Field label="Zone colour"
                 hint="The zone goes this colour while the rule holds, and back to its own the moment it stops.">
            <input type="color" value={d.zone_color || DEFAULT_BOX_COLOR} disabled={!editable}
                   onChange={(e) => onChange({ zone_color: e.target.value.toUpperCase() })} />
          </Field>
          <p className="muted small" style={{ marginTop: -4 }}>
            {triggerZone
              ? <>Paints <strong>{triggerZone.name}</strong>. Wire a different
                  Zone into this Trigger&apos;s <strong>Zone</strong> port to
                  paint that one instead.</>
              : <span className="c-warn">
                  Nothing to paint yet - wire a Zone into this Trigger&apos;s
                  <strong> Zone</strong> port (it appears on the node while
                  this is ticked).
                </span>}
          </p>
        </OptRow>

        <OptRow id="payload" icon={Database} title="Payload options" editable={editable}
                about="Configure triggered and non-triggered values."
                summary={`${payloadSummary}, ${holdSummary}`} on={payloadSet}
                defaultOpen={hold !== "realtime"}>
          {typed("payload_type", "payload", "Payload when triggered",
                 "Send payload when triggered", "1")}
          {typed("idle_type", "idle_payload", "Payload when not triggered",
                 "Send payload when not triggered", "0")}
          {/* How the output behaves after it fires: under the payloads it is
              about (Tok Aba, 2026-10-14), the same out_hold / out_hold_s writes. */}
          <RadioCards id={`hold-${node.id}`} title="Output behavior" editable={editable}
                      sub="Controls how the output behaves after the trigger fires."
                      value={hold} options={HOLDS}
                      onPick={(id) => onChange(id === "realtime"
                        ? { out_hold: "realtime" }
                        : { out_hold: id,
                            out_hold_s: Number(d.out_hold_s) > 0 ? d.out_hold_s : 10 })}
                      extra={(id) => id === "realtime" ? null : (
                        <div className="rc-extra">
                          <Field label={id === "interval" ? "Re-check every (s)" : "Stay active for (s)"}>
                            <input className="input" type="number" min={holdLow} max="86400"
                                   step="1" value={d.out_hold_s ?? 0} disabled={!editable}
                                   data-trg-hold-s=""
                                   onChange={(e) => onChange({ out_hold_s: e.target.value === ""
                                     ? "" : +e.target.value })} />
                          </Field>
                          {!secsOk(d.out_hold_s ?? 0, holdLow) && (
                            <p className="small c-warn">
                              The hold {id === "interval" ? "interval" : "time"} must be
                              between {holdLow} and 86400 seconds.
                            </p>
                          )}
                          <p className="muted small trg-hint">
                            {id === "interval"
                              ? `Checked ${d.out_hold_s || "N"} s after it fires and every ${d.out_hold_s || "N"} s after that: still true with the same value stays active, anything else turns it off.`
                              : "Turns off after that time even if the condition is still true; a new trigger starts the time again."}
                          </p>
                        </div>
                      )} />
        </OptRow>
      </section>

      {/* Advanced ---------------------------------------------------------- */}
      <details className="trg-adv" data-trg-advanced="">
        <summary>
          <SlidersHorizontal size={16} aria-hidden="true" />
          <span style={{ flex: 1, minWidth: 0 }}>
            <strong className="small">Advanced settings</strong>
            <span className="muted small" style={{ display: "block" }}>
              {d.dup_guard ? "Skips an object the table already holds. " : ""}
              Deduplication, object tracking, timing intervals, and other
              technical options.
            </span>
          </span>
          <ChevronDown size={16} aria-hidden="true" className="trg-adv-chev" />
        </summary>
        <div className="trg-adv-body">
          <GuardFields d={d} editable={editable} onChange={onChange} wired={wired} />
          <p className="muted small trg-hint" style={{ marginTop: 8 }}>
            {oidTracked
              ? "Object tracking: the rule this Trigger watches has OID ticked, so Once per object id (OID) is available."
              : "Once per object id (OID) is offered when the detection rule this Trigger watches has OID ticked."}
          </p>
        </div>
      </details>
    </div>
  );
}
