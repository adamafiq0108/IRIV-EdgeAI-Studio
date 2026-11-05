// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// "Page guides" switch, at the bottom of the sidebar: when on, each page opens its
// guide the first time (and until its own tick box is ticked); when off, none opens
// by itself. A page's Guide button always works either way.
import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { BookOpen } from "lucide-react";
import { Switch } from "@/components/ui/switch.jsx";
import { api } from "../api/client.js";
import {
  GUIDES_CHANGED_EVENT, SETUP_CHANGED_EVENT, guidesEnabled, setGuidesEnabled,
} from "../workflow/guideState.js";

/** The same Page guides switch, as the row at the foot of every guide: it is how a
 *  guide that has opened tells the person they can turn guides off (or back on). */
export function GuideSwitchRow() {
  const [on, setOn] = useState(guidesEnabled());
  useEffect(() => {
    const sync = () => setOn(guidesEnabled());
    window.addEventListener(GUIDES_CHANGED_EVENT, sync);
    return () => window.removeEventListener(GUIDES_CHANGED_EVENT, sync);
  }, []);
  return (
    <label className="flex items-center gap-3 text-sm cursor-pointer" data-guide-switch-row>
      <BookOpen size={16} aria-hidden="true" />
      <span className="font-medium">Page guides</span>
      <Switch size="sm" checked={on} aria-label="Page guides"
              onCheckedChange={(v) => { setOn(v); setGuidesEnabled(v); }} />
      <span className="text-xs text-muted-foreground">
        {on ? "On: guides open by themselves. Switch off to stop them."
            : "Off: guides stay closed. Switch on to see them again."}
      </span>
    </label>
  );
}

export default function GuideSwitch() {
  const [on, setOn] = useState(guidesEnabled());
  // While the setup guide is going its entry holds this spot (above the account
  // button); the switch takes it once the guide is finished or skipped.
  const [going, setGoing] = useState(false);
  const { pathname } = useLocation();
  const loadGoing = useCallback(() => {
    api.get("/api/detector/tutorial").then((s) => setGoing(!!s && !s.done)).catch(() => setGoing(false));
  }, []);
  useEffect(loadGoing, [loadGoing, pathname]);
  useEffect(() => {
    const sync = () => setOn(guidesEnabled());
    window.addEventListener(GUIDES_CHANGED_EVENT, sync);
    window.addEventListener(SETUP_CHANGED_EVENT, loadGoing);
    return () => {
      window.removeEventListener(GUIDES_CHANGED_EVENT, sync);
      window.removeEventListener(SETUP_CHANGED_EVENT, loadGoing);
    };
  }, [loadGoing]);
  if (going) return null;
  return (
    <div className="flex items-center gap-2 px-2 py-1.5 text-sm group-data-[collapsible=icon]:justify-center
                    group-data-[collapsible=icon]:px-0" data-guide-switch
         title={"Page guides: " + (on ? "on" : "off") + ". Show a short guide the first time each page opens"}>
      {/* Collapsed to icons the label and the switch go, and the book itself is the switch. */}
      <button type="button" aria-label={"Page guides " + (on ? "on" : "off")}
              className={"inline-flex size-6 shrink-0 items-center justify-center rounded-md outline-none "
                         + "focus-visible:ring-2 focus-visible:ring-ring "
                         + (on ? "text-primary" : "text-muted-foreground")}
              onClick={() => { setOn(!on); setGuidesEnabled(!on); }}>
        <BookOpen size={16} aria-hidden="true" />
      </button>
      <span className="flex-1 group-data-[collapsible=icon]:hidden">Page guides</span>
      <span className="group-data-[collapsible=icon]:hidden">
        <Switch size="sm" checked={on} aria-label="Page guides"
                onCheckedChange={(v) => { setOn(v); setGuidesEnabled(v); }} />
      </span>
    </div>
  );
}
