// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// SettingsHub - one Settings entry in the account menu, with the choice inside it.
//
// Accounts, Data Retention and Board I/O were three separate rail items under
// "Administration". They are the same KIND of thing - box-wide decisions, made
// once, admin only - and as three siblings they competed with Systems and Live
// Preview for a place people look at constantly. One entry, and the choice is
// made after you have decided you are changing a setting.
//
// The chooser is a side menu, grouped by what a setting is ABOUT (who may get
// in, what the box keeps, how it connects, how it tells you things, its
// hardware), with one line under each entry saying what it is for. Nine tabs
// in one strip wrapped onto two rows and left the reader to guess what
// "System" or "Board I/O" held. On a narrow screen the menu becomes one
// scrolling row. Each entry renders the panel that already existed; they wear
// one page frame (ui/SettingsPage.jsx).
//
import { useEffect, useState } from "react";
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
    { id: "accounts", group: "Access", label: "Accounts", icon: Users, Panel: AccountsPanel,
      hint: "Who can sign in and what they open" },
    // The BOX's own switches -- what this appliance may do at all, as
    // against what one system measures or one account may open. Beside
    // Accounts: the two answer the same question from either end.
    { id: "system", group: "Access", label: "System", icon: SlidersHorizontal, Panel: SystemPanel,
      hint: "What this box may do" },
    { id: "retention", group: "Data", label: "Data Retention", icon: Database, Panel: RetentionPanel,
      hint: "How long recordings and events are kept" },
    // Both belong to the BOX rather than to any system: one network, one
    // broker, however many systems run on top of them.
    { id: "network", group: "Connectivity", label: "Network", icon: Network, Panel: NetworkPanel,
      hint: "Ethernet, Wi-Fi and the hotspot" },
    { id: "mqtt", group: "Connectivity", label: "MQTT Broker", icon: RadioTower, Panel: BrokerPanel,
      hint: "The box's own broker on the plant LAN" },
    // Box-wide on purpose: one mailbox serves every system's Email nodes and
    // Footage sends, so it is declared here rather than four times over.
    { id: "mail", group: "Notifications", label: "Email", icon: Mail, Panel: MailPanel,
      hint: "The mailbox alerts are sent from" },
    // Same reasoning, same shape: a bot token belongs to the box, not to each
    // of the twelve nodes that message the same operations group.
    { id: "telegram", group: "Notifications", label: "Telegram", icon: Send, Panel: TelegramPanel,
      hint: "The bot alerts are sent with" },
    { id: "board", group: "Hardware", label: "Board I/O", icon: CircuitBoard, Panel: BoardPanel,
      hint: "Which terminals the platform drives" },
    { id: "panel", group: "Hardware", label: "Front Panel", icon: MonitorSmartphone, Panel: PanelSettings,
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

  const groups = GROUPS.map((g) => ({ g, items: tabs.filter((t) => t.group === g) }))
                       .filter((x) => x.items.length);

  return (
    <div className="mx-auto grid w-full max-w-7xl gap-6 lg:grid-cols-[250px_minmax(0,1fr)]">
      {/* Narrow screens: one scrolling row of the same entries. */}
      <nav aria-label="Settings" className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 lg:hidden">
        {tabs.map((t) => {
          const on = t.id === active.id;
          return (
            <button key={t.id} type="button" onClick={() => setTab(t.id)}
                    aria-current={on ? "page" : undefined}
                    className={"inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition-colors "
                      + (on ? "border-primary bg-primary/10 text-foreground"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
              <t.icon className="size-4" aria-hidden="true" /> {t.label}
            </button>
          );
        })}
      </nav>

      {/* Wide screens: the grouped menu, beside the panel, and it stays put
          while a long panel scrolls. */}
      <nav aria-label="Settings" className="hidden lg:block">
        <div className="sticky top-4 rounded-xl border bg-card p-2">
          <div className="px-2.5 pb-2 pt-1.5">
            <div className="text-base font-semibold">Settings</div>
            <div className="text-xs text-muted-foreground">For the whole box, every system on it</div>
          </div>
          {groups.map(({ g, items }) => (
            <div key={g} className="mt-1.5">
              <div className="px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {g}
              </div>
              <ul className="space-y-0.5">
                {items.map((t) => {
                  const on = t.id === active.id;
                  return (
                    <li key={t.id}>
                      <button type="button" onClick={() => setTab(t.id)}
                              aria-current={on ? "page" : undefined}
                              className={"group flex w-full items-start gap-2.5 rounded-lg border-l-2 px-2.5 py-2 text-left transition-colors "
                                + (on ? "border-primary bg-muted"
                                      : "border-transparent hover:bg-muted/60")}>
                        <t.icon aria-hidden="true"
                                className={"mt-0.5 size-4 shrink-0 " + (on ? "text-primary" : "text-muted-foreground group-hover:text-foreground")} />
                        <span className="min-w-0">
                          <span className={"block text-sm font-medium " + (on ? "text-foreground" : "text-foreground/90")}>
                            {t.label}
                          </span>
                          <span className="block text-xs leading-snug text-muted-foreground">{t.hint}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      </nav>

      {/* Keyed, so switching remounts the panel: each one polls something,
          and leaving a stopped panel's timer running is how a settings page
          keeps endpoints warm that nobody is looking at. */}
      <div className="min-w-0">
        {Panel ? <Panel key={active.id} /> : null}
      </div>
    </div>
  );
}
