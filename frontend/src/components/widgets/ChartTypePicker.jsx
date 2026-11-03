// THE CHART TYPE PICKER, its title strip and the in-family "Show as", drawn
// ONCE for the workflow node inspector and both Builders. Definitions and the
// switch rules are chartTypes.js; nothing here decides what is stored.
//
// Flow (Tok Aba, 2026-10-14, mockups 59-64): a NEW chart opens on the picker
// ("Choose a chart type"); Continue opens that type's properties, headed by a
// coloured strip with a "Change chart type" button that comes back here. An
// existing chart opens straight on its properties. Changing type says what
// would be dropped before Continue does it.
//
// Accessibility: the cards are a radio group (arrow keys move and select,
// one tab stop), the filter pills are toggle buttons, the previews are
// decorative inline SVG in the chart palette.
import { useEffect, useRef, useState } from "react";
import { Field as UiField } from "../ui/primitives.jsx";
import { SERIES_COLORS } from "../charts.js";
import {
  CHART_FAMILIES, SHOW_AS, chartType, planTypeSwitch, selectable, typesFor,
} from "./chartTypes.js";

const [C1, C2, C3, , C5, C6] = SERIES_COLORS;
const AXIS = "currentColor";

/** The small drawing of a type: on a card, and as the strip's icon. */
export function ChartTypeIcon({ id, size = 64 }) {
  const w = size;
  const h = Math.round(size * 0.5);
  const axis = (
    <path d={`M4 4 V${h - 4} H${w - 4}`} fill="none" stroke={AXIS} strokeOpacity=".35"
          strokeWidth="1" />
  );
  const xs = [8, 18, 28, 38, 48, 58].map((x) => (x * w) / 64);
  const ys = [0.62, 0.48, 0.55, 0.3, 0.38, 0.2].map((y) => y * h);
  const line = xs.map((x, i) => `${i ? "L" : "M"}${x} ${ys[i]}`).join(" ");
  const bars = (heights, colours) => heights.map((v, i) => (
    <rect key={i} x={xs[i] - 3} y={h - 4 - v * (h - 10)} width="6" height={v * (h - 10)}
          rx="1" fill={colours[i % colours.length]} />
  ));
  let body = null;
  if (id === "line") {
    body = (<>{axis}<path d={line} fill="none" stroke={C1} strokeWidth="2" />
      {xs.map((x, i) => <circle key={i} cx={x} cy={ys[i]} r="1.8" fill={C1} />)}</>);
  } else if (id === "area") {
    body = (<>{axis}<path d={`${line} L${xs[5]} ${h - 4} L${xs[0]} ${h - 4} Z`}
                          fill={C1} fillOpacity=".35" stroke={C1} strokeWidth="1.6" /></>);
  } else if (id === "time_bar") {
    body = (<>{axis}{bars([0.35, 0.6, 0.45, 0.8, 0.55, 0.9], [C3, C6])}</>);
  } else if (id === "compare_bar") {
    body = (<>{axis}{bars([0.85, 0.4, 0.7, 0.3, 0.55], [C3, C6, C3, C6, C3])}</>);
  } else if (id === "pie" || id === "donut") {
    const r = h / 2 - 2;
    const cx = w / 2;
    const cy = h / 2;
    const arc = (a0, a1) => {
      const p = (a) => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
      const [x0, y0] = p(a0);
      const [x1, y1] = p(a1);
      return `M${cx} ${cy} L${x0} ${y0} A${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1} ${y1} Z`;
    };
    const T = Math.PI * 2;
    body = (<>
      <path d={arc(0, T * 0.42)} fill={C3} />
      <path d={arc(T * 0.42, T * 0.72)} fill={C6} />
      <path d={arc(T * 0.72, T)} fill={C2} />
      {id === "donut" && <circle cx={cx} cy={cy} r={r * 0.55} fill="var(--surface, #fff)" />}
    </>);
  } else if (id === "radar") {
    const cx = w / 2;
    const cy = h / 2;
    const r = h / 2 - 2;
    const pts = (k) => [0, 1, 2, 3, 4].map((i) => {
      const a = (i / 5) * Math.PI * 2;
      const rr = r * (Array.isArray(k) ? k[i] : k);
      return `${cx + rr * Math.sin(a)},${cy - rr * Math.cos(a)}`;
    }).join(" ");
    body = (<>
      <polygon points={pts(1)} fill="none" stroke={AXIS} strokeOpacity=".3" />
      <polygon points={pts([0.9, 0.5, 0.75, 0.6, 0.8])} fill={C5} fillOpacity=".3"
               stroke={C5} strokeWidth="1.4" />
    </>);
  } else if (id === "scatter") {
    const dots = [[10, 0.7], [16, 0.5], [22, 0.62], [28, 0.35], [34, 0.45], [40, 0.25],
                  [46, 0.4], [52, 0.2], [20, 0.3], [44, 0.6]];
    body = (<>{axis}{dots.map(([x, y], i) => (
      <circle key={i} cx={(x * w) / 64} cy={y * h} r="1.8" fill={C3} />))}</>);
  }
  return (
    <svg className="ctp-icon" width={w} height={h} viewBox={`0 0 ${w} ${h}`}
         aria-hidden="true" focusable="false">{body}</svg>
  );
}

/** "Choose a chart type".
 *
 *  `value`      the type the chart is now ("" for a new chart with none)
 *  `initial`    the card selected when the picker opens (a Builder's dragged
 *               kind); defaults to `value`
 *  `kind`, `model`  the chart as it stands, so a change can say what it drops
 *  `onContinue(typeId)`  the surface applies it (planTypeSwitch)
 *  `onCancel`   shown in change mode only: back to the properties */
export function ChartTypePicker({ value = "", initial, kind = "", model = null, fresh = false,
                                  onContinue, onCancel = null, disabled = false }) {
  const [family, setFamily] = useState("all");
  const [picked, setPicked] = useState(initial ?? value ?? "");
  const cards = typesFor(family);
  const refs = useRef({});
  const focusTo = useRef("");
  useEffect(() => {
    if (focusTo.current && refs.current[focusTo.current]) refs.current[focusTo.current].focus();
    focusTo.current = "";
  });
  const sel = chartType(picked);
  const plan = sel && selectable(sel) && !fresh && value && value !== picked
    ? planTypeSwitch(kind, model, picked) : null;
  const choose = (id) => { if (!disabled) setPicked(id); };
  // One tab stop: the checked card, else the first one that can be chosen.
  const stop = cards.find((t) => t.id === picked && selectable(t))
    || cards.find((t) => selectable(t));
  const onKey = (e, t) => {
    const live = cards.filter(selectable);
    const i = live.findIndex((x) => x.id === t.id);
    let to = null;
    if (["ArrowRight", "ArrowDown"].includes(e.key)) to = live[(i + 1) % live.length];
    else if (["ArrowLeft", "ArrowUp"].includes(e.key)) to = live[(i - 1 + live.length) % live.length];
    else if (e.key === "Home") to = live[0];
    else if (e.key === "End") to = live[live.length - 1];
    else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      if (selectable(t)) choose(t.id);
      return;
    }
    if (to) {
      e.preventDefault();
      choose(to.id);
      focusTo.current = to.id;
    }
  };
  return (
    <section className="ctp" aria-labelledby="ctp-title" data-testid="chart-type-picker">
      <h3 id="ctp-title" className="ctp-title">Choose a chart type</h3>
      <p className="muted small ctp-sub">
        What do you want to understand from your data? Choose the chart that
        best answers your question.
      </p>
      <div className="ctp-filters" role="group" aria-label="Show chart types for">
        <span className="muted small">Show chart types for</span>
        {CHART_FAMILIES.map(([id, words]) => (
          <button key={id} type="button" className="ctp-pill" aria-pressed={family === id}
                  onClick={() => setFamily(id)}>{words}</button>
        ))}
      </div>
      <div className="ctp-grid" role="radiogroup" aria-label="Chart types">
        {cards.map((t) => {
          const can = selectable(t);
          const on = picked === t.id && can;
          return (
            <div key={t.id} ref={(el) => { refs.current[t.id] = el; }}
                 role="radio" aria-checked={on} aria-disabled={!can || disabled || undefined}
                 tabIndex={stop && stop.id === t.id ? 0 : -1}
                 className={`ctp-card${on ? " is-on" : ""}${can ? "" : " is-off"}`}
                 data-type={t.id}
                 aria-label={`${t.name}. ${t.desc} ${t.best}.${can ? "" : " Coming soon."}`}
                 onClick={() => can && choose(t.id)} onKeyDown={(e) => onKey(e, t)}>
              <div className="ctp-preview"><ChartTypeIcon id={t.id} size={96} /></div>
              <div className="ctp-name">
                <strong>{t.name}</strong>
                {on && <span className="ctp-check" aria-hidden="true">&#10003;</span>}
                {!can && <span className="ctp-soon">Coming soon</span>}
              </div>
              <span className="small ctp-desc">{t.desc}</span>
              <span className="small ctp-best">{t.best}</span>
            </div>
          );
        })}
      </div>
      <div className="ctp-foot" aria-live="polite">
        {sel && selectable(sel) ? (
          <>
            <p className="small"><strong>Selected: {sel.name}</strong></p>
            <p className="muted small">Example use: {sel.example}</p>
            <p className="small">{sel.next}</p>
            {plan && plan.dropped.length > 0 && (
              <div className="ctp-drop small" role="note">
                <strong>Changing to {sel.name} will not keep:</strong>
                <ul>{plan.dropped.map((x) => <li key={x}>{x}</li>)}</ul>
                Everything else - title, inputs and their names, Show and
                overrides, Y calculation, date range, unit and thresholds - is kept.
              </div>
            )}
          </>
        ) : (
          <p className="muted small">Select a chart type above to continue.</p>
        )}
        <div className="ctp-actions">
          {onCancel && (
            <button type="button" className="btn" onClick={onCancel}>Cancel</button>
          )}
          <button type="button" className="btn btn-accent ctp-go"
                  disabled={disabled || !sel || !selectable(sel)}
                  onClick={() => onContinue(sel.id)}>
            {sel && selectable(sel) ? `Continue with ${sel.name}` : "Continue"}
          </button>
        </div>
      </div>
    </section>
  );
}

/** The coloured strip heading a type's properties, with the way back to
 *  the picker. */
export function ChartTypeStrip({ typeId, onChange, disabled = false }) {
  const t = chartType(typeId);
  if (!t) return null;
  return (
    <div className={`ctp-strip ctp-strip-${t.id}`} data-testid="chart-type-strip">
      <ChartTypeIcon id={t.id} size={44} />
      <div className="ctp-strip-text">
        <strong>{t.heading}</strong>
        <span className="small">{t.panel}</span>
      </div>
      {onChange && (
        <button type="button" className="btn btn-sm ctp-change" disabled={disabled}
                onClick={onChange}>Change chart type</button>
      )}
    </div>
  );
}

/** "Show as" inside a type's properties: the types of its family (a switch
 *  that keeps every field), the Smooth line option on a Line Chart, and --
 *  on a hand-made Builder card -- the card shapes it could still become. */
export function ChartShowAs({ field: F = UiField, typeId, kind, onType, onKind,
                              otherShapes = [], disabled = false }) {
  const t = chartType(typeId);
  if (!t) return null;
  // A type that is "Coming soon" is offered to nobody but a chart that
  // already is one (a saved radar keeps its own value here).
  const list = (SHOW_AS[t.family] || [[t.id, t.name]])
    .filter(([v]) => v === t.id || selectable(chartType(v)));
  const OTHER = "shape:";
  return (
    <>
      <F label="Show as" hint="Another chart of the same family - every setting carries over.">
        <select className="input" value={t.id} disabled={disabled} aria-label="Show as"
                onChange={(e) => {
                  const v = e.target.value;
                  if (v.startsWith(OTHER)) onKind(v.slice(OTHER.length));
                  else onType(v);
                }}>
          {list.map(([v, w]) => <option key={v} value={v}>{w}</option>)}
          {otherShapes.length > 0 && (
            <optgroup label="Not a chart">
              {otherShapes.map(([k, w]) => <option key={k} value={`${OTHER}${k}`}>{w}</option>)}
            </optgroup>
          )}
        </select>
      </F>
      {t.id === "line" && (
        <label className="wf-check" style={{ margin: "0 0 6px" }}>
          <input type="checkbox" checked={kind === "spline"} disabled={disabled}
                 aria-label="Smooth line"
                 onChange={(e) => onKind(e.target.checked ? "spline" : "line")} />
          <span className="small">Smooth line</span>
        </label>
      )}
    </>
  );
}
