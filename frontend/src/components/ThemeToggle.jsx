// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// Floating light/dark toggle for pages OUTSIDE the shells (login and the
// other auth pages) - the shells render their own in the header. Mounting it
// is also what APPLIES the saved choice on these standalone pages: useTheme
// sets the .dark class on load, so a dark-mode operator hitting /login no
// longer gets flashed with a light page.
import { useTheme } from "../hooks/use-theme.js";
import { Moon, Sun } from "lucide-react";

export default function ThemeToggle() {
  const [dark, setDark] = useTheme();
  return (
    <button
      type="button"
      className="theme-toggle"
      title={dark ? "Light mode" : "Dark mode"}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setDark((d) => !d)}
    >
      {dark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}
