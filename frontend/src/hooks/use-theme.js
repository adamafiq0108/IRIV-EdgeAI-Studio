// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Dark mode, shared by every shadcn shell (HubShell on the hub, MasterShell on
// the masters) so 5100 and 511x expose the same toggle and honour the same
// stored choice. shadcn's tokens already define a .dark palette - and index.css
// defines the legacy pages' dark palette next to it - this hook just decides
// when the class is on. Persisted. A first visit is LIGHT mode, whatever the
// operating system prefers (Tok Aba, 2026-10-20); the toggle remembers a change.
import { useEffect, useState } from "react";

/** Reactive "is the dark class on <html> right now?".
 *
 * useTheme() OWNS the class (each caller gets its own state seeded from
 * localStorage), so a component that merely needs to FOLLOW the theme cannot
 * use it -- the shell's toggle would not reach a second instance's state.
 * This watches the class itself via MutationObserver, so a follower re-renders
 * the instant the toggle flips it. Reading the class once per render is how
 * the workflow canvas lagged a theme change until its next poll. */
export function useDarkClass() {
  const [dark, setDark] = useState(
    () => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const el = document.documentElement;
    const obs = new MutationObserver(
      () => setDark(el.classList.contains("dark")));
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

export function useTheme() {
  const [dark, setDark] = useState(() => {
    try {
      const saved = localStorage.getItem("cy-theme");
      if (saved) return saved === "dark";
    } catch { /* ignore */ }
    return false;
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("cy-theme", dark ? "dark" : "light"); } catch { /* ignore */ }
  }, [dark]);
  return [dark, setDark];
}
