// NotificationCenter - the bell in the hub header, and everything behind it.
//
// This replaced a "Platform Update" nav item. An update is not a place you go;
// it is something the box tells you. A sidebar entry made it a page an admin
// had to remember to visit, which meant a fix could sit available for weeks on
// a box nobody thought to check. A bell that marks itself when there is
// something to read inverts that: the box asks, once, wherever you already are.
//
// Two tabs (the Important tab was removed, 2026-10-21):
//
//   System     - the platform's own state. What this box runs, what is
//                available, and the update itself, expanded in place -- with
//                what needs attention (an expired token, a box that cannot
//                take updates, an update that rolled itself back, data not
//                on the NVMe) as alerts at the top. Derived from the same
//                state, and present only when true.
//   Inbox      - messages for THIS account (core/services/inbox.py): a
//                password-reset request waiting for approval (master and the
//                developer, with the two buttons), "your password was
//                changed", "X changed their password". Read state is per
//                reader; a request is answered and gone.
//
// The Inbox is every account's. System is admin-only, because
// every endpoint behind them is (core/routes/update.py) -- so the update poll
// runs only for an admin, and a non-admin's bell shows the one tab it has.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { offersUpdate } from "../lib/version.js";
import { usePoll, useVisibleInterval } from "../api/hooks.js";
import UpdatePanel from "./UpdatePanel.jsx";
import { Button } from "./ui/button.jsx";
import { Badge } from "./ui/badge.jsx";
import {
  Popover, PopoverContent, PopoverTrigger,
} from "./ui/popover.jsx";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./ui/tabs.jsx";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "./ui/dialog.jsx";
import { releaseNoteGroups, roman, noteTag, tagClass } from "./releaseNotes.js";
import { OPEN_EVENT as STORAGE_SETUP_EVENT } from "./NewStoragePrompt.jsx";
import {
  Bell, Inbox, AlertTriangle, Server, KeyRound, MailOpen, Check, X, Trash2,
  HardDrive,
} from "lucide-react";

//: A message longer than this is shown as its first lines plus "Read more",
//: which opens it in a dialog. The update notice is the case: thirty lines
//: of release notes in a 20 rem popover are a scroll nobody finishes, and
//: laid out by area in a dialog they are the same list the Update card drew.
const PREVIEW_CHARS = 160;

/** The first lines of a long body, cut at a line end where possible. */
function preview(body) {
  const text = String(body || "");
  if (text.length <= PREVIEW_CHARS) return text;
  const cut = text.lastIndexOf("\n", PREVIEW_CHARS);
  return text.slice(0, cut > 40 ? cut : PREVIEW_CHARS).trimEnd() + "…";
}

//: How often the bell asks. Slow on purpose: the answer changes when a release
//: is published, which is a thing that happens on the order of weeks, and the
//: page behind the bell refreshes itself the moment it is opened.
const POLL_MS = 120000;

//: What a notice's page and tab are CALLED, for the reader. Names, not ids:
//: "Go to settings/mail" tells somebody nothing they did not already fear.
//: Kept beside the renderer rather than sent from the server, because these
//: are the words already on the rail and the tab strip, and two places
//: spelling the same page differently is how a UI stops being one product.
const PAGE_LABEL = {
  systems: "Systems", control: "System Control", health: "Performance",
  wall: "Live Preview", store: "Template Store", vision: "Vision",
  settings: "Settings",
};
const TAB_LABEL = {
  accounts: "Accounts", system: "System", retention: "Data Retention",
  board: "Board I/O", mail: "Email", telegram: "Telegram",
  network: "Network", mqtt: "MQTT Broker", panel: "Front Panel",
  templates: "System Templates", licenses: "Licenses",
};

function whereLabel(m) {
  if (!m || !PAGE_LABEL[m.page]) return "";
  if (m.page === "settings" && TAB_LABEL[m.tab]) {
    return `Settings → ${TAB_LABEL[m.tab]}`;
  }
  return PAGE_LABEL[m.page];
}

export default function NotificationCenter({ admin = false, onGo = null }) {
  const [open, setOpen] = useState(false);
  // The message opened with "Read more", drawn in full in a dialog.
  const [reading, setReading] = useState(null);
  // The release notes for the version an update notice names, fetched when
  // the notice itself carries none (the box announced before its check
  // cache knew the release). The Update state still holds the latest
  // release's notes, and after an update that release is the one running.
  const [fetchedNotes, setFetchedNotes] = useState("");
  useEffect(() => {
    setFetchedNotes("");
    if (!reading || reading.kind !== "update" || releaseNoteGroups(reading.body).length) return;
    api.get("/api/hub/update?channel=stable")
      .then((d) => {
        const st = d?.update || {};
        const want = String(reading.version || "").replace(/^v/i, "");
        const got = String(st.latest || "").replace(/^v/i, "");
        if (st.notes && (!want || got === want)) setFetchedNotes(st.notes);
      })
      .catch(() => {});
  }, [reading]);
  const [tab, setTab] = useState(admin ? "system" : "inbox");
  const [channel, setChannel] = useState("stable");
  // The dev channel's state, when this account and this box both have one.
  const [devState, setDevState] = useState(null);
  // The platform update state -- polled only for an admin (UpdateWatch),
  // since the endpoint 403s everyone else.
  const [data, setData] = useState(null);
  // Where the device data lives (/api/hub/retention `storage`), for the one
  // notice that depends on it. Null until read.
  const [storage, setStorage] = useState(null);
  const [updateTick, setUpdateTick] = useState(0);
  const reload = useCallback(async () => setUpdateTick((t) => t + 1), []);
  // This account's inbox, and the open reset requests where the account may
  // answer them. Polled for everyone: it is the one tab everyone has.
  const inbox = usePoll("/api/inbox", POLL_MS);
  const messages = inbox.data?.notices || [];
  const requests = inbox.data?.requests || [];
  const inboxUnread = inbox.data?.unread || 0;

  const stable = data?.update || null;
  const otaReady = data?.ota_ready !== false;
  const devAvailable = !!data?.dev_available;

  // A closed bell polls; an opened one refreshes at once, so the list is never
  // up to two minutes stale at the moment someone actually reads it.
  useEffect(() => {
    if (open) { reload().catch(() => {}); inbox.reload().catch(() => {}); }
  }, [open, reload]);            // eslint-disable-line react-hooks/exhaustive-deps

  // Opening the Inbox tab reads what is on it. Requests are not "read" --
  // they are answered -- so they keep the dot until somebody does.
  useEffect(() => {
    if (open && tab === "inbox" && messages.some((m) => !m.read)) {
      api.post("/api/inbox/read", {}).then(() => inbox.reload()).catch(() => {});
    }
  }, [open, tab, messages]);       // eslint-disable-line react-hooks/exhaustive-deps

  async function answer(id, verb) {
    try {
      await api.post(`/api/auth/reset-requests/${id}/${verb}`, {});
    } catch (e) {
      // The list re-reads either way: a 404 here means somebody else
      // answered it first, and the row should simply go.
      if (!String(e.message || "").includes("gone")) alert(e.message);
    }
    inbox.reload().catch(() => {});
  }

  async function dismiss(id) {
    try { await api.del(`/api/inbox/${id}`); } catch { /* re-read below */ }
    inbox.reload().catch(() => {});
  }

  // Falling back to the customer channel is not cosmetic: a developer account
  // signed into a customer box would otherwise keep asking for a channel the
  // server answers 404 to, once per open.
  useEffect(() => {
    if (!devAvailable && channel === "dev") setChannel("stable");
  }, [devAvailable, channel]);

  const notices = buildNotices({ stable, dev: devState, otaReady, storage });
  const updates = [
    // offersUpdate, not the flag alone: a state the box wrote before it
    // updated must not keep the dot lit on the version it now runs.
    offersUpdate(stable) ? { channel: "stable", state: stable } : null,
    offersUpdate(devState) ? { channel: "dev", state: devState } : null,
  ].filter(Boolean);
  // The dot means "there is something here you have not acted on". An update
  // waiting counts; so does a problem. A box that is simply up to date does
  // not decorate its own header.
  const count = (admin ? updates.length + notices.length : 0) + inboxUnread;

  return (
    <>
      {admin && <UpdateWatch tick={updateTick} onState={setData} />}
      {admin && <StorageWatch tick={updateTick} onState={setStorage} />}
      {admin && devAvailable && (
        <ChannelWatch channel="dev" onState={setDevState} />
      )}
      <Popover open={open} onOpenChange={setOpen}>
        {/* `render`, not `asChild`: Base UI composes through a render prop
            and ignores `asChild`, which left a button nested inside a button
            -- invalid markup, and two focus stops for one bell. */}
        <PopoverTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              className="relative"
              aria-label={count ? `Notifications (${count} unread)` : "Notifications"}
              title="Notifications"
            />
          }
        >
          <>
            <Bell />
            {count > 0 && (
              <span
                aria-hidden="true"
                className="absolute right-0.5 top-0.5 flex size-2 items-center justify-center"
              >
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary opacity-60" />
                <span className="relative inline-flex size-2 rounded-full bg-primary" />
              </span>
            )}
          </>
        </PopoverTrigger>
        {/* Wide, because the update lives IN here: version, the release's own
            description, the button and the progress bar. A narrow list that
            sent you somewhere else to act would just be the nav item again. */}
        <PopoverContent align="end" className="w-[min(30rem,calc(100vw-2rem))] p-0">
          <Tabs value={tab} onValueChange={setTab}>
            {/* EVERY tab at full `foreground`, chosen or not. The muted
                pairing is the correct one on paper and too quiet in practice
                -- three tabs where two are half-visible reads as one tab and
                some decoration. Which tab is chosen is said by the lifted
                background and the shadow, not by dimming the other two.
                `foreground`, not white: in dark mode that token IS near-white
                (oklch 0.985), and in light mode it stays dark instead of
                turning white-on-white.
                Set on each TRIGGER with ordinary variants -- an earlier
                attempt used arbitrary selectors on the list and an opacity
                modifier, neither of which Tailwind ever compiled, so the
                classes sat in the markup with no rule behind them. */}
            <div className="border-b px-3 pt-3">
              <TabsList className="w-full bg-muted">
                <TabsTrigger value="inbox" className="flex-1 gap-1.5 text-foreground data-active:bg-background data-active:shadow-sm">
                  <Inbox className="size-3.5" /> Inbox
                  {inboxUnread > 0 && (
                    <Badge variant="secondary" className="ml-1 px-1.5 py-0 text-[10px]">
                      {inboxUnread}
                    </Badge>
                  )}
                </TabsTrigger>
                {admin && (<>
                <TabsTrigger value="system" className="flex-1 gap-1.5 text-foreground data-active:bg-background data-active:shadow-sm">
                  <Server className="size-3.5" /> System
                  {updates.length + notices.length > 0 && (
                    <Badge variant="secondary" className="ml-1 px-1.5 py-0 text-[10px]">
                      {updates.length + notices.length}
                    </Badge>
                  )}
                </TabsTrigger>
                </>)}
              </TabsList>
            </div>

            <TabsContent value="inbox" className="m-0">
              {requests.length === 0 && messages.length === 0 ? (
                <Empty icon={MailOpen} title="No messages">
                  Password-reset requests and notices about your account appear here.
                </Empty>
              ) : (
                <ul className="max-h-[70vh] divide-y overflow-y-auto">
                  {/* Requests first: each is a decision waiting on the reader,
                      and it stays until somebody answers it. */}
                  {requests.map((r) => (
                    <li key={`req-${r.id}`} className="flex items-start gap-3 p-3">
                      <KeyRound className="mt-0.5 size-4 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium">
                          Password reset requested: {r.user}
                        </div>
                        <div className="mt-0.5 text-xs text-muted-foreground">
                          Asked {new Date(r.at * 1000).toLocaleString()}
                          {r.ip ? ` from ${r.ip}` : ""}. Approve to email a reset link
                          to {r.email}; nothing is sent until you do.
                        </div>
                        <div className="mt-2 flex gap-2">
                          <Button size="sm" onClick={() => answer(r.id, "approve")}>
                            <Check className="size-3.5" /> Approve and send link
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => answer(r.id, "reject")}>
                            <X className="size-3.5" /> Reject
                          </Button>
                        </div>
                      </div>
                    </li>
                  ))}
                  {messages.map((m) => (
                    <li key={m.id}
                        className={"flex items-start gap-3 p-3" + (m.read ? " opacity-70" : "")}>
                      <MailOpen className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium"
                             title={m.detail || undefined}>{m.title}</div>
                        {/* pre-line: the update notice carries the release
                            notes as they were written, one change per line
                            under its area heading, and squashed onto one
                            line they are a paragraph nobody reads. */}
                        {m.body && (
                          <div className="mt-0.5 whitespace-pre-line text-xs text-muted-foreground">
                            {preview(m.body)}
                            {(m.body.length > PREVIEW_CHARS || m.kind === "update") && (
                              <>
                                {" "}
                                <button type="button"
                                        className="font-medium text-primary hover:underline"
                                        onClick={() => setReading(m)}>
                                  Read more
                                </button>
                              </>
                            )}
                          </div>
                        )}
                        {/* Where to go and do something about it. A notice
                            that says a mailbox failed, on a box with nine
                            settings tabs, leaves the reader to find the page
                            themselves -- which is most of the work. */}
                        {m.kind !== "update" && whereLabel(m) && onGo && (
                          <Button variant="link" size="sm"
                                  className="mt-1 h-auto p-0 text-xs"
                                  onClick={() => { setOpen(false); onGo(m.page, m.tab); }}>
                            Go to {whereLabel(m)}
                          </Button>
                        )}
                        <div className="mt-0.5 text-[11px] text-muted-foreground">
                          {new Date(m.at * 1000).toLocaleString()}
                        </div>
                      </div>
                      <Button variant="ghost" size="icon-sm" title="Remove"
                              aria-label="Remove message" onClick={() => dismiss(m.id)}>
                        <Trash2 className="size-3.5" />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </TabsContent>

            <TabsContent value="system" className="m-0">
              {/* The developer channel is offered only when the server says
                  both locks are open -- a developer account AND a box declared
                  a dev box. An admin on an appliance never sees this switch,
                  and forging one changes nothing: every endpoint re-checks
                  both locks, and the applier refuses a dev manifest on a
                  stable-channel job before unpacking a byte. */}
              {devAvailable && (
                <div className="flex gap-1 border-b px-3 py-2">
                  <ChannelTab active={channel === "stable"} label="Customer build"
                              onClick={() => setChannel("stable")} />
                  <ChannelTab active={channel === "dev"} label="Developer build"
                              onClick={() => setChannel("dev")} />
                </div>
              )}
              {/* What needs attention (an expired token, a box that cannot take
                  updates, data not on the NVMe) is shown here, above the
                  update, as alerts: there is no separate Important tab. */}
              {notices.length > 0 && (
                <ul className="divide-y border-b" data-notices="1">
                  {notices.map((n) => (
                    <li key={n.id} role="alert" className="flex items-start gap-3 bg-amber-500/5 p-3">
                      <n.icon className="mt-0.5 size-4 shrink-0 text-destructive" />
                      <div className="min-w-0">
                        <div className="text-sm font-medium">{n.title}</div>
                        <div className="mt-0.5 text-xs text-muted-foreground">{n.body}</div>
                        {n.action && (
                          <Button variant="link" size="sm" className="mt-1 h-auto p-0 text-xs"
                                  onClick={() => {
                                    if (n.run) { setOpen(false); n.run(); return; }
                                    setChannel(n.channel || "stable"); setTab("system");
                                  }}>
                            {n.action}
                          </Button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <div className="max-h-[70vh] overflow-y-auto p-3">
                <UpdatePanel
                  embedded
                  channel={channel}
                  onStateChange={channel === "dev" ? setDevState : undefined}
                  onDone={() => reload().catch(() => {})}
                />
              </div>
            </TabsContent>
          </Tabs>
        </PopoverContent>
      </Popover>

      {/* A long message in full. An update notice is release notes, and they
          are laid out the way the Update card lays them out -- a heading per
          area, a short list under each -- rather than as one long paragraph;
          any other long message keeps its own lines. */}
      <Dialog open={!!reading} onOpenChange={(o) => { if (!o) setReading(null); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{reading?.title || ""}</DialogTitle>
            <DialogDescription>
              {reading ? new Date(reading.at * 1000).toLocaleString() : ""}
            </DialogDescription>
          </DialogHeader>
          {reading && (
            reading.kind === "update" && releaseNoteGroups(fetchedNotes || reading.body).length > 0 ? (
              <div className="max-h-[60vh] space-y-3 overflow-auto text-sm leading-relaxed">
                {releaseNoteGroups(fetchedNotes || reading.body).map((g, gi) => (
                  <div key={gi}>
                    {g.area && <div className="mb-0.5 font-semibold">{roman(gi + 1)}. {g.area}</div>}
                    <ul className="list-disc space-y-1 pl-5 marker:text-muted-foreground">
                      {g.items.map((item, i) => <li key={i}>{(() => { const { tag, text } = noteTag(item); return (<>{tag && <span className={"mr-1.5 inline-block rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide " + tagClass(tag)}>{tag}</span>}{text}</>); })()}</li>)}
                    </ul>
                  </div>
                ))}
              </div>
            ) : (
              <div className="max-h-[60vh] overflow-auto whitespace-pre-line text-sm leading-relaxed">
                {reading.body}
              </div>
            )
          )}
          {reading && reading.kind !== "update" && whereLabel(reading) && onGo && (
            <Button variant="link" size="sm" className="h-auto self-start p-0 text-xs"
                    onClick={() => { setReading(null); setOpen(false); onGo(reading.page, reading.tab); }}>
              Go to {whereLabel(reading)}
            </Button>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

// Watch one channel's state without rendering it. Mounted only when that
// channel exists for this account, which is why it is a component and not a
// hook call -- hooks cannot be conditional, and asking the server about a
// channel it has already said no to is how a 404 becomes a heartbeat.
function UpdateWatch({ tick, onState }) {
  // The platform update poll, as a child so it mounts only for an admin --
  // hooks cannot be conditional, components can. `tick` is the parent's
  // "read now" (the bell was opened, an update finished).
  const read = useCallback(async () => {
    try { onState(await api.get("/api/hub/update")); } catch { onState(null); }
  }, [onState]);
  useEffect(() => { read(); }, [read, tick]);
  useVisibleInterval(read, POLL_MS);
  return null;
}

function StorageWatch({ tick, onState }) {
  // Where the data is stored, as a child so it mounts only for an admin. Slow
  // like the update poll: a drive is fitted or the data moved once, not hourly.
  const read = useCallback(async () => {
    try { onState((await api.get("/api/hub/retention"))?.storage || null); }
    catch { onState(null); }
  }, [onState]);
  useEffect(() => { read(); }, [read, tick]);
  useVisibleInterval(read, POLL_MS);
  return null;
}

function ChannelWatch({ channel, onState }) {
  const read = useCallback(async () => {
    try {
      const d = await api.get(`/api/hub/update?channel=${encodeURIComponent(channel)}`);
      onState(d.update || null);
    } catch {
      onState(null);
    }
  }, [channel, onState]);
  useEffect(() => { read(); }, [read]);
  useVisibleInterval(read, POLL_MS);
  return null;
}

function ChannelTab({ active, label, onClick }) {
  // The chosen one carries a border and the card colour, not a muted fill:
  // muted-on-muted was the pairing that vanished in light mode, where the
  // strip behind it is already the muted tone.
  return (
    <button
      type="button"
      onClick={onClick}
      className={"rounded-md border px-2.5 py-1 text-xs font-medium transition-colors "
        + (active
          ? "border-border bg-card text-foreground shadow-sm"
          : "border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground")}
    >
      {label}
    </button>
  );
}

// Everything on the Important tab is derived, never stored: it is a reading of
// the state the update service already publishes. Nothing is "dismissed" here
// because nothing here goes away by being read -- an expired token is still
// expired tomorrow, and the only thing that clears it is fixing it.
function buildNotices({ stable, dev, otaReady, storage }) {
  const out = [];
  // Device data (events, snapshots, clips) is held to 60 days unless EVERY
  // system's data is on an NVMe, then 12 months. Present only while some of it
  // is not: no NVMe fitted, or one fitted that the data is not on. The server
  // words the reason; the action opens the same setup the NVMe prompt does.
  if (storage && (!storage.extended || storage.differs)) {
    const days = storage.min_days || storage.limit_days || 60;
    out.push({
      id: "storage", icon: HardDrive,
      title: storage.nvme_fitted
        ? `Data is not on the NVMe: kept up to ${days} days`
        : `No NVMe drive: data is kept up to ${days} days`,
      body: storage.reason + ((storage.off_nvme || []).length
        ? ` Not on the NVMe: ${storage.off_nvme.map((s) => s.where
            ? `${s.system} (${s.where})` : s.system).join(", ")}.` : ""),
      ...(storage.nvme_fitted
        ? { action: "Configure Storage",
            run: () => window.dispatchEvent(new Event(STORAGE_SETUP_EVENT)) }
        : {}),
    });
  }
  if (!otaReady) {
    out.push({
      id: "ota", icon: AlertTriangle,
      title: "This box cannot take over-the-air updates",
      body: "It was installed before the release layout. Re-run the installer "
          + "on it once; after that, updates arrive here.",
    });
  }
  for (const [channel, st] of [["stable", stable], ["dev", dev]]) {
    if (!st) continue;
    const where = channel === "dev" ? " (developer build)" : "";
    if (st.error === "token_expired") {
      out.push({
        id: `tok-${channel}`, channel, icon: KeyRound,
        title: `The update token has expired${where}`,
        body: "Until it is replaced this box cannot see new releases. Nothing "
            + "else about the box changes.",
        action: "Paste a new token",
      });
    }
    // A check that could not reach GitHub is NOT here any more. It was a
    // derived line nothing could clear, raised by a single Wi-Fi blip; it is
    // now an ordinary, dismissable notice in Messages, posted only after two
    // failed checks in a row and withdrawn by the next one that gets through
    // (core/services/update.py _check_notice).
  }
  return out;
}

function Empty({ icon: Icon, title, children }) {
  return (
    <div className="flex flex-col items-center gap-1 px-6 py-10 text-center">
      <Icon className="size-6 text-muted-foreground" />
      <div className="text-sm font-medium">{title}</div>
      <div className="max-w-xs text-xs text-muted-foreground">{children}</div>
    </div>
  );
}
