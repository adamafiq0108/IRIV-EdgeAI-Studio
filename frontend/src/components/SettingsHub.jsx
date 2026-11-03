// SettingsHub - one Settings entry in the rail, with the choice inside it.
//
// Accounts, Data Retention and Board I/O were three separate rail items under
// "Administration". They are the same KIND of thing - box-wide decisions, made
// once, admin only - and as three siblings they competed with Systems and Live
// Preview for a place people look at constantly. One entry, and the choice is
// made after you have decided you are changing a setting.
//
// The chooser is a row of tabs rather than a second sidebar: there are three
// of them (five for a developer), which is a tab strip, not a navigation
// tree. Each tab renders the panel that already existed; they wear one page
// frame now (ui/SettingsPage.jsx) so they stop announcing themselves three
// different ways.
//
// The developer-only tabs are SPREAD AWAY rather than hidden in a customer
// build, exactly as they were in the rail: on an appliance there is no account
// that could reveal them and no endpoint behind them, so they should not be in
// the bundle at all.
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
import { Users, Database, CircuitBoard, Library, KeyRound, Mail, Network,
         MonitorSmartphone, RadioTower, Send, SlidersHorizontal } from "lucide-react";

const DEV_TOOLS = __DEV_TOOLS__;

//: Remembered per browser, because an admin who came here to change one thing
//: usually comes back for the same thing.
const LAST_TAB = "hub.settings.tab";

export default function SettingsHub({ devTools = false, tabs: allowed = null }) {
  const all = [
    { id: "accounts", label: "Accounts", icon: Users, Panel: AccountsPanel },
    // The BOX's own switches -- what this appliance may do at all, as
    // against what one system measures or one account may open. Second,
    // beside Accounts: the two answer the same question from either end.
    { id: "system", label: "System", icon: SlidersHorizontal, Panel: SystemPanel },
    { id: "retention", label: "Data Retention", icon: Database, Panel: RetentionPanel },
    { id: "board", label: "Board I/O", icon: CircuitBoard, Panel: BoardPanel },
    // Box-wide on purpose: one mailbox serves every system's Email nodes and
    // Footage sends, so it is declared here rather than four times over.
    { id: "mail", label: "Email", icon: Mail, Panel: MailPanel },
    // Same reasoning, same shape: a bot token belongs to the box, not to each
    // of the twelve nodes that message the same operations group.
    { id: "telegram", label: "Telegram", icon: Send, Panel: TelegramPanel },
    // Both belong to the BOX rather than to any system: one network, one
    // front panel, however many systems run on top of them.
    { id: "network", label: "Network", icon: Network, Panel: NetworkPanel },
    // The box's MQTT broker, beside Network: one broker per box, and what it
    // exposes on the plant LAN is the same kind of decision.
    { id: "mqtt", label: "MQTT Broker", icon: RadioTower, Panel: BrokerPanel },
    { id: "panel", label: "Front Panel", icon: MonitorSmartphone,
      Panel: PanelSettings },
    ...(DEV_TOOLS && devTools ? [
      { id: "templates", label: "System Templates", icon: Library,
        Panel: null, lazy: () => import("./TemplatesPanel.jsx") },
      { id: "licenses", label: "Licenses", icon: KeyRound,
        Panel: null, lazy: () => import("./LicensesPanel.jsx") },
    ] : []),
  ];

  // WHICH of them this account may open, decided by the server
  // (auth.SETTINGS_TABS) rather than here. A site admin gets their own staff,
  // retention and the ways the box talks out; the hardware -- Board I/O, the
  // front panel -- stays with the box owner. Deciding it in the page would
  // mean the tabs and the endpoints behind them could disagree, and the tab
  // strip is not the thing enforcing it either way.
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

  // A remembered tab that no longer exists (a developer's account demoted
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
  // No tabs at all is not a page. It should be unreachable (the rail entry is
  // gated on the same answer), but a shell that renders an empty strip on a
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

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4">
      <div className="flex flex-wrap items-center gap-1 border-b pb-2">
        {tabs.map((t) => {
          const on = t.id === active.id;
          return (
            <button key={t.id} type="button"
                    onClick={() => setTab(t.id)}
                    aria-current={on ? "page" : undefined}
                    className={"inline-flex items-center gap-2 rounded-md px-3 py-1.5 "
                      + "text-sm font-medium transition-colors "
                      + (on ? "bg-muted text-foreground"
                            : "text-muted-foreground hover:bg-muted/60 hover:text-foreground")}>
              <t.icon className="size-4" /> {t.label}
            </button>
          );
        })}
      </div>
      {/* Keyed, so switching tabs remounts the panel: each one polls
          something, and leaving a stopped panel's timer running is how a
          settings page keeps three endpoints warm nobody is looking at. */}
      {Panel ? <Panel key={active.id} /> : null}
    </div>
  );
}
