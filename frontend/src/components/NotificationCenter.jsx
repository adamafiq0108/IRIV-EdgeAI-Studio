// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// NotificationCenter - the bell in the hub header, and everything behind it.
//
// The box tells you what needs attention, wherever you already are, instead
// of a page an admin has to remember to visit.
//
// Two tabs (the Important tab was removed, 2026-10-21):
//
//   System     - what needs attention on the box (data not on the NVMe) as
//                alerts, derived from live state and present only when true.
//   Inbox      - messages for THIS account (core/services/inbox.py): a
//                password-reset request waiting for approval (master, with
//                the two buttons), "your password was
//                changed", "X changed their password". Read state is per
//                reader; a request is answered and gone.
//
// The Inbox is every account's. System is admin-only, because the endpoint
// behind it is -- so a non-admin's bell shows the one tab it has.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { usePoll, useVisibleInterval } from "../api/hooks.js";
import { Button } from "./ui/button.jsx";
import { Badge } from "./ui/badge.jsx";
import {
  Popover, PopoverContent, PopoverTrigger,
} from "./ui/popover.jsx";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./ui/tabs.jsx";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "./ui/dialog.jsx";
import { OPEN_EVENT as STORAGE_SETUP_EVENT } from "./NewStoragePrompt.jsx";
import {
  Bell, Inbox, Server, KeyRound, MailOpen, Check, X, Trash2,
  HardDrive,
} from "lucide-react";

//: A message longer than this is shown as its first lines plus "Read more",
//: which opens it in a dialog.
const PREVIEW_CHARS = 160;

/** The first lines of a long body, cut at a line end where possible. */
function preview(body) {
  const text = String(body || "");
  if (text.length <= PREVIEW_CHARS) return text;
  const cut = text.lastIndexOf("\n", PREVIEW_CHARS);
  return text.slice(0, cut > 40 ? cut : PREVIEW_CHARS).trimEnd() + "…";
}

//: How often the bell asks. Slow on purpose: the page behind the bell
//: refreshes itself the moment it is opened.
const POLL_MS = 120000;

//: What a notice's page and tab are CALLED, for the reader. Names, not ids:
//: "Go to settings/mail" tells somebody nothing they did not already fear.
//: Kept beside the renderer rather than sent from the server, because these
//: are the words already on the rail and the tab strip, and two places
//: spelling the same page differently is how a UI stops being one product.
const PAGE_LABEL = {
  systems: "Systems", control: "System Control", health: "Performance",
  wall: "Live Preview", vision: "Vision",
  settings: "Settings",
};
const TAB_LABEL = {
  accounts: "Accounts", system: "System", retention: "Data Retention",
  board: "Board I/O", mail: "Email", telegram: "Telegram",
  network: "Network", mqtt: "MQTT Broker", panel: "Front Panel",
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
  const [tab, setTab] = useState(admin ? "system" : "inbox");
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

  const notices = buildNotices({ storage });
  // The dot means "there is something here you have not acted on": a problem
  // or an unread message. A box with nothing to say does not decorate its
  // own header.
  const count = (admin ? notices.length : 0) + inboxUnread;

  return (
    <>
      {admin && <StorageWatch tick={updateTick} onState={setStorage} />}
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
                  {notices.length > 0 && (
                    <Badge variant="secondary" className="ml-1 px-1.5 py-0 text-[10px]">
                      {notices.length}
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
                        {m.body && (
                          <div className="mt-0.5 whitespace-pre-line text-xs text-muted-foreground">
                            {preview(m.body)}
                            {m.body.length > PREVIEW_CHARS && (
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
                        {whereLabel(m) && onGo && (
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
              {/* What needs attention (data not on the NVMe) is shown here
                  as alerts: there is no separate Important tab. */}
              {notices.length > 0 ? (
                <ul className="divide-y border-b" data-notices="1">
                  {notices.map((n) => (
                    <li key={n.id} role="alert" className="flex items-start gap-3 bg-amber-500/5 p-3">
                      <n.icon className="mt-0.5 size-4 shrink-0 text-destructive" />
                      <div className="min-w-0">
                        <div className="text-sm font-medium">{n.title}</div>
                        <div className="mt-0.5 text-xs text-muted-foreground">{n.body}</div>
                        {n.action && (
                          <Button variant="link" size="sm" className="mt-1 h-auto p-0 text-xs"
                                  onClick={() => { setOpen(false); if (n.run) n.run(); }}>
                            {n.action}
                          </Button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty icon={Server} title="Nothing needs attention">
                  Problems with this box, such as data not stored on the NVMe, appear here.
                </Empty>
              )}
            </TabsContent>
          </Tabs>
        </PopoverContent>
      </Popover>

      {/* A long message in full, with its own lines kept. */}
      <Dialog open={!!reading} onOpenChange={(o) => { if (!o) setReading(null); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{reading?.title || ""}</DialogTitle>
            <DialogDescription>
              {reading ? new Date(reading.at * 1000).toLocaleString() : ""}
            </DialogDescription>
          </DialogHeader>
          {reading && (
            <div className="max-h-[60vh] overflow-auto whitespace-pre-line text-sm leading-relaxed">
              {reading.body}
            </div>
          )}
          {reading && whereLabel(reading) && onGo && (
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

function StorageWatch({ tick, onState }) {
  // Where the data is stored, as a child so it mounts only for an admin (hooks
  // cannot be conditional, components can). Slow: a drive is fitted or the
  // data moved once, not hourly.
  const read = useCallback(async () => {
    try { onState((await api.get("/api/hub/retention"))?.storage || null); }
    catch { onState(null); }
  }, [onState]);
  useEffect(() => { read(); }, [read, tick]);
  useVisibleInterval(read, POLL_MS);
  return null;
}

// Everything on the System tab is derived, never stored: it is a reading of
// live state. Nothing is "dismissed" here because nothing here goes away by
// being read -- the only thing that clears it is fixing it.
function buildNotices({ storage }) {
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
