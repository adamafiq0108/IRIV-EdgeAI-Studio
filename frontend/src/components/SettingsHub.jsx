// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// SettingsHub - one Settings entry in the account menu, with the choice inside it.
//
// Accounts, Data Retention and Board I/O were three separate rail items under
// "Administration". They are the same KIND of thing - box-wide decisions, made
// once, admin only - and as three siblings they competed with Systems and Live
// Preview for a place people look at constantly. One entry, and the choice is
// made after you have decided you are changing a setting.
//
// The chooser is a side menu in the macOS System Settings manner: a search
// box, coloured icon tiles, a filled selection, and groups set apart by
// space. It is grouped by what a setting is ABOUT (who may get
// in, what the box keeps, how it connects, how it tells you things, its
// hardware), with one line under each entry saying what it is for. Nine tabs
// in one strip wrapped onto two rows and left the reader to guess what
// "System" or "Board I/O" held. On a narrow screen the menu becomes one
// scrolling row. Each entry renders the panel that already existed; they wear
// one page frame (ui/SettingsPage.jsx).
//
import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { IconTile, SettingsTone } from "./ui/SettingsPage.jsx";
import AccountsPanel from "./AccountsPanel.jsx";
import SystemPanel from "./SystemPanel.jsx";
import RetentionPanel from "./RetentionPanel.jsx";
import BoardPanel from "./BoardPanel.jsx";
import MailPanel from "./MailPanel.jsx";
import TelegramPanel from "./TelegramPanel.jsx";
import NetworkPanel from "./NetworkPanel.jsx";
import BrokerPanel from "./BrokerPanel.jsx";
import PanelSettings from "./PanelSettings.jsx";
import { Users, Database, CircuitBoard, Mail, Network,
         MonitorSmartphone, RadioTower, Send, SlidersHorizontal } from "lucide-react";

//: Remembered per browser, because an admin who came here to change one thing
//: usually comes back for the same thing.
const LAST_TAB = "hub.settings.tab";

//: The groups, in the order the menu shows them.
const GROUPS = ["Access", "Data", "Connectivity", "Notifications", "Hardware"];

export default function SettingsHub({ tabs: allowed = null }) {
  const all = [
    { id: "accounts", color: "#007aff", group: "Access", label: "Accounts", icon: Users, Panel: AccountsPanel,
      hint: "Who can sign in and what they open" },
    // The BOX's own switches -- what this appliance may do at all, as
    // against what one system measures or one account may open. Beside
    // Accounts: the two answer the same question from either end.
    { id: "system", color: "#8e8e93", group: "Access", label: "System", icon: SlidersHorizontal, Panel: SystemPanel,
      hint: "What this box may do" },
    { id: "retention", color: "#34c759", group: "Data", label: "Data Retention", icon: Database, Panel: RetentionPanel,
      hint: "How long recordings and events are kept" },
    // Both belong to the BOX rather than to any system: one network, one
    // broker, however many systems run on top of them.
    { id: "network", color: "#0a84ff", group: "Connectivity", label: "Network", icon: Network, Panel: NetworkPanel,
      hint: "Ethernet, Wi-Fi and the hotspot" },
    { id: "mqtt", color: "#ff9500", group: "Connectivity", label: "MQTT Broker", icon: RadioTower, Panel: BrokerPanel,
      hint: "The box's own broker on the plant LAN" },
    // Box-wide on purpose: one mailbox serves every system's Email nodes and
    // Footage sends, so it is declared here rather than four times over.
    { id: "mail", color: "#30b0c7", group: "Notifications", label: "Email", icon: Mail, Panel: MailPanel,
      hint: "The mailbox alerts are sent from" },
    // Same reasoning, same shape: a bot token belongs to the box, not to each
    // of the twelve nodes that message the same operations group.
    { id: "telegram", color: "#32ade6", group: "Notifications", label: "Telegram", icon: Send, Panel: TelegramPanel,
      hint: "The bot alerts are sent with" },
    { id: "board", color: "#af52de", group: "Hardware", label: "Board I/O", icon: CircuitBoard, Panel: BoardPanel,
      hint: "Which terminals the platform drives" },
    { id: "panel", color: "#5856d6", group: "Hardware", label: "Front Panel", icon: MonitorSmartphone, Panel: PanelSettings,
      hint: "The OLED display and its button" },
  ];

  // WHICH of them this account may open, decided by the server
  // (auth.SETTINGS_TABS) rather than here. A site admin gets their own staff,
  // retention and the ways the box talks out; the hardware -- Board I/O, the
  // front panel -- stays with the box owner. Deciding it in the page would
  // mean the tabs and the endpoints behind them could disagree, and the menu
  // is not the thing enforcing it either way.
  const tabs = allowed ? all.filter((t) => allowed.includes(t.id)) : all;

  const [tab, setTab] = useState(() => {
    try {
      const saved = localStorage.getItem(LAST_TAB);
      return saved && tabs.some((t) => t.id === saved) ? saved : "accounts";
    } catch { return "accounts"; }
  });
  useEffect(() => {
    try { localStorage.setItem(LAST_TAB, tab); } catch { /* private window */ }
  }, [tab]);

  // A remembered tab that no longer exists (an account demoted
  // between visits, or a tier that never had it) must not render nothing.
  const active = tabs.find((t) => t.id === tab) || tabs[0] || null;
  const [query, setQuery] = useState("");
  const [Lazy, setLazy] = useState(null);
  useEffect(() => {
    let dead = false;
    if (active?.lazy) {
      active.lazy().then((m) => { if (!dead) setLazy(() => m.default); })
                   .catch(() => { if (!dead) setLazy(null); });
    } else {
      setLazy(null);
    }
    return () => { dead = true; };
  }, [active]);

  const Panel = active?.Panel || Lazy;
  // No tabs at all is not a page. It should be unreachable (the menu entry is
  // gated on the same answer), but a shell that renders an empty menu on a
  // stale session is worse than one that says what happened.
  if (!active) {
    return (
      <div className="mx-auto w-full max-w-6xl">
        <p className="text-sm text-muted-foreground">
          There are no settings for this account.
        </p>
      </div>
    );
  }

  const q = query.trim().toLowerCase();
  const hit = (x) => !q || x.label.toLowerCase().includes(q) || x.hint.toLowerCase().includes(q);
  const groups = GROUPS.map((g) => ({ g, items: tabs.filter((x) => x.group === g && hit(x)) }))
                       .filter((x) => x.items.length);

  return (
    <div className="mx-auto grid w-full max-w-7xl gap-8 lg:grid-cols-[260px_minmax(0,1fr)]">
      {/* Narrow screens: one scrolling row of the same entries. */}
      <nav aria-label="Settings" className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 lg:hidden">
        {tabs.map((x) => {
          const on = x.id === active.id;
          return (
            <button key={x.id} type="button" onClick={() => setTab(x.id)}
                    aria-current={on ? "page" : undefined}
                    className={"inline-flex shrink-0 items-center gap-2 rounded-full py-1 pl-1 pr-3 text-sm font-medium transition-colors "
                      + (on ? "text-white" : "bg-card text-foreground hover:bg-muted")}
                    style={on ? { background: "#007aff" } : undefined}>
              <IconTile icon={x.icon} size={22} active={on} /> {x.label}
            </button>
          );
        })}
      </nav>

      {/* Wide screens: the menu beside the pane; it stays put while a long
          pane scrolls. */}
      <nav aria-label="Settings" className="hidden lg:block">
        <div className="sticky top-4 space-y-4">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                   placeholder="Search" aria-label="Search settings"
                   className="h-8 w-full rounded-lg border-0 bg-black/[.05] pl-8 pr-2 text-sm outline-none ring-0 placeholder:text-muted-foreground focus:bg-black/[.07] focus:ring-2 focus:ring-[#007aff]/40 dark:bg-white/[.08]" />
          </div>
          {groups.length === 0 && (
            <p className="px-2 text-sm text-muted-foreground">No setting matches "{query}".</p>
          )}
          {groups.map(({ g, items }) => (
            <ul key={g} className="space-y-0.5" aria-label={g}>
              {items.map((x) => {
                const on = x.id === active.id;
                return (
                  <li key={x.id}>
                    <button type="button" onClick={() => setTab(x.id)} title={x.hint}
                            aria-current={on ? "page" : undefined}
                            className={"flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm transition-colors "
                              + (on ? "font-medium text-white" : "text-foreground hover:bg-black/[.05] dark:hover:bg-white/[.06]")}
                            style={on ? { background: "#007aff" } : undefined}>
                      <IconTile icon={x.icon} size={26} active={on} />
                      <span className="truncate">{x.label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ))}
        </div>
      </nav>

      {/* Keyed, so switching remounts the pane: each one polls something,
          and leaving a stopped pane's timer running is how a settings page
          keeps endpoints warm that nobody is looking at. */}
      <div className="min-w-0 pb-10">
        <SettingsTone.Provider value={active.color}>
          {Panel ? <Panel key={active.id} /> : null}
        </SettingsTone.Provider>
      </div>
    </div>
  );
}
