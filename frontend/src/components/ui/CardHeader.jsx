// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// CardHeader / StateSwitch - the header row a settings card wears, and the
// on/off control that sits at its top right (Tok Aba, 2026-10-14; the Device
// Hotspot card is the template).
//
//   [icon]  Bold title                       [On] (switch)
//           muted line saying what it does
//
// The state label sits immediately LEFT of the switch: green words on a
// faint green tint while on (--ok-text, which flips per theme), plain muted
// words while off. The label is not the only cue: the switch keeps its own
// position and aria-checked, and its aria-label is the caller's. The switch
// itself is the shared ui/switch.jsx, painted in the brand accent while on
// (theme.css .state-switch), so it reads the same on every card.
//
// Only the LOOK is shared. A caller passes its own checked / onCheckedChange /
// disabled / title / aria-label exactly as it did to <Switch>.

// Through the alias, so a page check that shims ui/switch.jsx shims this too.
import { Switch } from "@/components/ui/switch.jsx";

/** The state pill class: "on" green, "warn" amber, anything else plain. */
export function pillClass(kind) {
  return `state-pill${kind === "on" ? " is-on" : kind === "warn" ? " is-warn" : ""}`;
}

/** A label + switch pair. `label` overrides the On/Off words (e.g. "Not
 *  answering"), `kind` the pill colour ("on" | "warn" | "off"), and
 *  `labelProps` lands on the label (a data- attribute a page check reads,
 *  a title with the longer state). */
export function StateSwitch({ checked, onLabel = "On", offLabel = "Off", label, kind,
                              labelProps = {}, ...props }) {
  const on = !!checked;
  return (
    <span className="state-switch" data-state-switch={on ? "on" : "off"}>
      <span {...labelProps} className={pillClass(kind || (on ? "on" : "off"))}
            data-state-label="" aria-hidden="true">
        {label ?? (on ? onLabel : offLabel)}
      </span>
      <Switch checked={on} {...props} />
    </span>
  );
}

/** Icon, bold title, a muted line, and the card's control at the top right.
 *  `title` is drawn in the first <strong> of the card, which is how the
 *  page's own checks find a card by its title. */
export function CardHeader({ icon: Icon, title, subtitle, children, iconSize = 18,
                             className = "", ...rest }) {
  return (
    <div className={`card-head${className ? ` ${className}` : ""}`} {...rest}>
      {Icon && <Icon size={iconSize} aria-hidden="true" className="card-head-icon" />}
      <div className="card-head-text">
        <strong className="card-head-title">{title}</strong>
        {subtitle && <div className="muted small card-head-sub">{subtitle}</div>}
      </div>
      {children && <div className="card-head-right">{children}</div>}
    </div>
  );
}
