// AccountsPanel - platform-level account management, lives in the hub
// (IRIV Edge AI Studio → Administration). ONE place to manage every account: the
// credential store and session secret are already shared across the hub and
// every master on this box, so a user created here logs into everything their
// systems list allows. Standalone systems deliberately carry no account UI.
//
// Admin-only (the API enforces it; the shell hides the nav item too).
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useVisibleInterval } from "../api/hooks.js";
import { setToken } from "../lib/session.js";
import { useApp } from "../state/app.jsx";
import { useToast } from "./ui/Toast.jsx";
import { useConfirm } from "./ui/ask.jsx";
import { Button } from "./ui/button.jsx";
import { ConfirmDialog, FormDialog } from "./ui/dialogs.jsx";
import { PasswordInput } from "./ui/input.jsx";
import { Checkbox } from "./ui/checkbox.jsx";
import { Switch } from "./ui/switch.jsx";
import { detailText } from "./auditDetail.js";
import { actionLabel, actionDetail, resultLabel, whenText } from "./auditActions.js";
import { SECTIONS } from "./nav-sections.js";
import { MonitorAccess } from "./MonitorPanels.jsx";
import { UserPlus, KeyRound, Trash2, Shapes, Pencil, MoreVertical,
         Plus, Minus, Power } from "lucide-react";

export default function AccountsPanel() {
  const toast = useToast();
  const { user } = useApp();
  const confirm = useConfirm();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);

  // Create form.
  const [nu, setNu] = useState({ username: "", email: "", password: "", role: "operator", systems: [], admin: false });
  // Master-detail: the list picks, the pane edits. A page of stacked cards
  // put every account's full controls on screen at once -- twelve accounts
  // was twelve chip lists to scroll past to reach the one being changed.
  const [tab, setTab] = useState("accounts");   // accounts | roles
  const [picked, setPicked] = useState("");
  const [detail, setDetail] = useState("access"); // access | role
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  // Whose password the dialog is about, "" = closed. An admin changes their
  // OWN here as well as anyone else's -- it is the same route, and "where do
  // I change my password" had no answer on this page before.
  const [pwFor, setPwFor] = useState("");
  const [editing, setEditing] = useState("");   // whose Edit dialog is open
  const [menuFor, setMenuFor] = useState("");   // whose ⋮ menu is open
  const [renaming, setRenaming] = useState(false); // admin rename dialog
  const [emailFor, setEmailFor] = useState("");    // whose recovery address is being changed
  const [addingRole, setAddingRole] = useState(false);
  const [showNew, setShowNew] = useState(false);   // reveal the create form's password
  const [removingRole, setRemovingRole] = useState("");  // "" = closed

  const load = useCallback(() => {
    api.get("/api/users").then(setData).catch((e) => toast.error("Could not load users", e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(load, [load]);

  // The Monitor account is switched on and configured in its own tab, never
  // listed or edited as an ordinary account.
  const users = (data?.users || []).filter((u) => !u.monitor);
  const monitorOn = !!(data?.users || []).some((u) => u.monitor && u.enabled);
  const systems = data?.systems || [];
  // The name each system goes by NOW. The allowlist is keyed on the permanent
  // slug, so the chip has to be LABELLED from this and VALUED by the slug --
  // otherwise a renamed system keeps showing the name it was created with.
  const names = data?.names || {};
  const nameOf = (slug) => names[slug] || slug;
  const owners = data?.owners || {};
  // Systems THIS admin created. Granting access to a new account is about
  // handing on your own work, so the chip list offers that rather than every
  // system on the box -- someone else's systems are not yours to give away.
  // A system with no recorded owner (made before ownership was tracked)
  // stays listed: hiding it would make an existing system ungrantable with
  // no way to tell why.
  const myName = String(user?.name || "").toLowerCase();
  const mySystems = systems.filter((s) => {
    const owner = String(owners[s] || "").toLowerCase();
    return !owner || owner === myName;
  });
  const roles = data?.roles || ["engineer", "operator", "viewer"];
  const builtinRoles = data?.builtin_roles || ["engineer", "operator", "viewer"];

  // A role is a LABEL - access comes from the systems allowlist and the admin
  // account - so an admin can name the ones their site actually uses without
  // that granting anybody anything.
  //
  // This was a window.prompt: browser chrome, in the browser's own styling,
  // announcing the host and port above a wall of text with a bare box under
  // it. It could not show the rules the server enforces, could not validate
  // anything before submitting, and could not say a name was already taken
  // until after the attempt failed.
  async function addRole({ name }) {
    const clean = String(name || "").trim().toLowerCase();
    if (!clean) return;
    // Checked here as well as on the server, because the useful moment to
    // learn a name is taken is before pressing the button.
    if (roles.includes(clean)) {
      return toast.error("That role already exists", `"${clean}" is in the list.`);
    }
    setBusy(true);
    try {
      await api.post("/api/roles", { name: clean });
      setAddingRole(false);
      toast.success(`Role "${roleName(clean)}" created`,
                    "Assign it on a user's Role tab.");
      load();
    } catch (e) { toast.error("Could not create the role", e.message); }
    finally { setBusy(false); }
  }

  async function removeRole(name) {
    setBusy(true);
    try {
      await api.del(`/api/roles/${encodeURIComponent(name)}`);
      setRemovingRole("");
      toast.info(`Role "${roleName(name)}" deleted`);
      load();
    } catch (e) { toast.error("Could not delete the role", e.message); }
    finally { setBusy(false); }
  }

  // Take the recovery address off an account. Confirmed, because it removes
  // the emailed way back in: the master still has the front panel, and
  // everybody else has whoever owns the box.
  async function removeEmail() {
    const who = active?.username;
    if (!who) return;
    // `builtin` is the MASTER. Not `admin`, which is the site-admin tier, and
    // not `is_admin`, which no row carries at all -- the pair of them had this
    // backwards, so the master was told to go and ask its owner (it is the
    // owner) and a site admin was pointed at a front-panel gesture that resets
    // somebody else's account.
    const master = !!active.builtin;
    // ...and the panel only answers on a box where it is RUNNING. It ships
    // disabled, because on a stock board the factory flow drives the same I2C
    // bus -- so promising it unconditionally would talk an operator into
    // removing the last way into their own box.
    const panel = !!data?.panel_reset;
    // Said precisely rather than "features may stop working": the address
    // is used for exactly two things, and both are named. A reset link
    // already sent is NOT recalled (it is keyed on the password, valid for
    // its half hour), which is why the line says "can no longer be sent".
    if (!await confirm({
      title: "Remove this email address?",
      description: "The email address will be removed from this account. "
        + "Password reset links can no longer be sent to it"
        + (master
            ? ", and password reset requests from other users will only "
              + "reach the Inbox."
              + (panel
                  ? " This account can still be reset at the device: hold the "
                    + "USER button for 5 seconds and the panel shows a "
                    + "single-use code."
                  : " The front panel reset is switched off on this device, "
                    + "so there will be NO way to reset this account from the "
                    + "network or the hardware. Recovering it would mean "
                    + "editing the config over a shell.")
            : ". An administrator can still set a new password for this user."),
      // A link already sent is not recalled by this: a reset token in flight
      // stays valid for its half hour, because it is keyed on the password
      // rather than on the address. Change the password to close that.
      confirmLabel: "Remove Email Address",
      // The one case that deserves a speed bump: the master, on a box whose
      // panel is off, is removing the last way back in that is not a shell.
      destructive: master && !panel,
      requireText: master && !panel ? who : null,
      requireHint: master && !panel
        ? "Type the user name to confirm there is no way back in."
        : undefined,
    })) return;
    try {
      // The master's address lives with the master account (the
      // account-setup store); everyone else's on their row. Same split the
      // Set/Change dialog makes.
      if (active.builtin) await api.post("/api/auth/account-setup", { email: "" });
      else await api.patch(`/api/users/${who}`, { email: "" });
      toast.success("Email address removed");
      load();
    } catch (e) { toast.error("Could not remove the email address", e.message); }
  }

  async function createUser() {
    if (!nu.username || !nu.password) return toast.error("Username and password required");
    // Where a reset link for this account will go, if it has one. OPTIONAL:
    // a box on a closed network has nothing to send a link through, and an
    // address that cannot be reached reads as a way back in without being
    // one. Left blank, the account is reset by whoever owns the box -- and
    // the master itself by the front panel, which needs no network at all.
    if (nu.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(nu.email.trim())) {
      return toast.error("That does not look like an email address",
                         "Leave it empty for a user with no recovery address.");
    }
    if (!nu.systems.length) return toast.error("Grant at least one system (or All Systems)");
    setBusy(true);
    try {
      await api.post("/api/users", { username: nu.username, password: nu.password,
                                     email: nu.email.trim(),
                                     role: nu.role, systems: nu.systems.join(","),
                                     admin: !!nu.admin });
      toast.success(`${nu.username} created`,
                    nu.admin
                      ? "An administrator: they manage their own users, and "
                        + "open the systems granted here."
                      : "One sign-in works on the hub and every allowed system.");
      // Land on the account just made: creating one is almost always the
      // first half of "…and now give it access to X".
      setPicked(nu.username);
      setNu({ username: "", email: "", password: "", role: "operator", systems: [], admin: false });
      setCreating(false);
      load();
    } catch (e) { toast.error("Could not create the user", e.message); }
    finally { setBusy(false); }
  }

  async function patch(username, fields, note) {
    try {
      await api.patch(`/api/users/${username}`, fields);
      if (note) toast.success(note);
      load();
    } catch (e) { toast.error("Could not update the user", e.message); }
  }

  // A password change is a FORM, not a window.prompt: a prompt cannot mask
  // what is typed, cannot ask twice, and cannot say what the rule is until
  // after the attempt has already failed.
  async function savePassword({ current, password, confirm }) {
    const who = pwFor;
    const mine = who === user?.name;
    if (String(password).length < 8) {
      return toast.error("Too short", "A password is at least 8 characters.");
    }
    if (password !== confirm) {
      return toast.error("They do not match", "Type the same password twice.");
    }
    setBusy(true);
    try {
      // Your OWN goes through the self-service route: it asks for the current
      // password, and it is the only one that works for the administrator
      // account, whose credential lives in the auth config rather than in the
      // user store -- the per-user reset route has no row to find.
      if (mine) await api.post("/api/auth/password", { current, password });
      else await api.post(`/api/users/${who}/password`, { password });
      setPwFor("");
      toast.success(
        who === user?.name ? "Your password is changed" : `${who}'s password is changed`,
        // Their OWN session survives (the cookie is already signed); anyone
        // else has to sign in again, which is the point of changing it.
        who === user?.name ? "Other browsers signed in as you were signed out."
                           : `${who} must sign in again.`);
    } catch (e) { toast.error("Could not change the password", e.message); }
    finally { setBusy(false); }
  }

  // The administrator ships as `admin` -- a default, not a name anybody
  // chose -- so renaming it is ordinary setup rather than an exotic operation.
  // The reload is not cosmetic: the name is in the session cookie, in the
  // shell's greeting and in every ownership check, and the server re-signs the
  // cookie under the new one, so the page has to come back as the new account.
  async function saveUsername({ username, current }) {
    setBusy(true);
    try {
      const d = await api.post("/api/auth/username", { username, current });
      // The token carries the username: this tab has to adopt the re-signed
      // one BEFORE the reload, or the reload arrives with a dead identity.
      if (d?.token) setToken(d.token);
      setRenaming(false);
      toast.success(`You are now "${String(username).trim().toLowerCase()}"`,
                    "Reloading with the new name…");
      setTimeout(() => window.location.reload(), 900);
    } catch (e) { toast.error("Could not change the username", e.message); }
    finally { setBusy(false); }
  }

  async function remove(username) {
    // Exact, not "may affect its assigned access": every grant (systems,
    // categories, pages, capabilities) lives on the account's own row, so it
    // goes with it, and delete_user ends its sessions. The audit rows stay.
    if (!await confirm({ title: "Delete this user?",
                         description: `This will delete ${username}, `
                           + "remove all of its system access and sign it "
                           + "out everywhere. Its Activity Log entries are "
                           + "kept. This action cannot be undone.",
                         confirmLabel: "Delete User",
                         destructive: true })) return;
    try {
      await api.del(`/api/users/${username}`);
      toast.info(`${username} deleted`);
      load();
    } catch (e) { toast.error("Could not delete the user", e.message); }
  }

  function toggleSystem(username, current, slug) {
    const slugs = current === "*" ? ["*"]
      : String(current || "").split(",").map((s) => s.trim()).filter(Boolean);
    let next;
    if (slug === "*") next = slugs.includes("*") ? [] : ["*"];
    else {
      const base = slugs.filter((s) => s !== "*");
      next = base.includes(slug) ? base.filter((s) => s !== slug) : [...base, slug];
    }
    if (!next.length) return void toast.error("A user needs at least one system");
    patch(username, { systems: next.join(",") });
  }

  // The account the right-hand pane is about. Falls back to the first, so
  // the pane is never empty while accounts exist.
  const shown = users.filter((u) => !search.trim()
    || u.username.toLowerCase().includes(search.trim().toLowerCase()));
  const active = users.find((u) => u.username === picked) || shown[0] || users[0] || null;
  const activeSlugs = String(active?.systems || "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  const activeAll = activeSlugs.includes("*");

  return (
    <div className="acct">
      {/* Accounts and Roles are two different objects, so they are two tabs
          rather than two cards down one page: an admin renaming a role is
          not part-way through editing an account. */}
      <div className="acct-tabs">
        <button type="button"
                className={"acct-tab" + (tab === "accounts" ? " is-active" : "")}
                onClick={() => setTab("accounts")}>Users</button>
        <button type="button"
                className={"acct-tab" + (tab === "roles" ? " is-active" : "")}
                onClick={() => setTab("roles")}>Roles</button>
        <button type="button"
                className={"acct-tab" + (tab === "sessions" ? " is-active" : "")}
                onClick={() => setTab("sessions")}>Login Sessions</button>
        {/* The same log the Activity tab shows one account at a time, with
            nothing filtered out. Per-account answers "what has this account
            been doing"; this answers "what has been happening on this box",
            which is the question you have before you know whose row to open.
            Scoped server-side: an admin sees this deployment's accounts, a
            developer sees those and its own. */}
        <button type="button"
                className={"acct-tab" + (tab === "all" ? " is-active" : "")}
                onClick={() => setTab("all")}>Activity Log</button>
        {monitorOn && (
          <button type="button"
                  className={"acct-tab" + (tab === "monitor" ? " is-active" : "")}
                  onClick={() => setTab("monitor")}>Monitor</button>
        )}
      </div>

      {tab === "sessions" ? (
        <SessionsPane me={user?.name || ""} confirm={confirm} />
      ) : tab === "monitor" && monitorOn ? (
        <MonitorAccess />
      ) : tab === "all" ? (
        <div className="acct-pane">
          <div className="acct-pane-head">
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="acct-h2">Activity Log</div>
              {/* "recorded changes", not "changes": account create, edit
                  and delete do not write an audit row today, so the plain
                  word would promise a completeness the log does not have. */}
              <p className="acct-sub">
                Review sign-ins, failed login attempts, and recorded changes
                made by users on this device. The newest activities appear
                first.
              </p>
            </div>
          </div>
          <Activity username="" nameOf={nameOf} dev={!!user?.is_developer} />
        </div>
      ) : tab === "roles" ? (
        <div className="acct-pane">
          <div className="acct-pane-head">
            {/* The text takes the row and the button is pushed to the end of
                it. Without this the button sat wherever the paragraph left
                off, and on a narrow pane it wrapped underneath -- an action
                that belongs to the heading, floating below the text it
                belongs to. */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="acct-h2">Roles</div>
              {/* Not "define what users are allowed to do": a role is a
                  LABEL (auth.BUILTIN_ROLES) and grants nothing. What a user
                  may open and change is their System Access tree. */}
              <p className="acct-sub">
                Describe what kind of user each account is. A role does not
                grant access by itself; System Access is assigned separately
                for each user.
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => setAddingRole(true)}
                    disabled={busy} style={{ marginLeft: "auto", flex: "none" }}>
              <UserPlus className="size-3.5" /> Create Role
            </Button>
          </div>
          {/* The one thing this page is asked for that is NOT here.
              "admin" is a TIER, not a role: roles are labels and grant
              nothing, while the tier decides who may manage whom. Somebody
              looking for "admin" comes to the Roles list first, finds three
              labels, and concludes the feature is missing -- so the list says
              where it actually lives. */}
          <p className="acct-sub" style={{ marginTop: -4, marginBottom: 10 }}>
            <strong>Need administrator access?</strong>{" "}
            Administrator access is assigned to a user account, not through a
            role. Administrators can manage their users and settings.
          </p>
          <div className="acct-rolelist">
            {roles.map((r) => {
              const builtin = builtinRoles.includes(r);
              const inUse = users.filter((u) => (u.role || "operator") === r).length;
              return (
                <div key={r} className="acct-role">
                  <span className="acct-role-mark" aria-hidden><Shapes className="size-4" /></span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="acct-role-name">{roleName(r)}</div>
                    <div className="acct-sub">
                      {builtin ? "Built-in role" : "Custom role"}
                      {ROLE_NOTES[r] ? ` · ${ROLE_NOTES[r]}` : ""}
                    </div>
                  </div>
                  {/* How many accounts carry it, which is the only real fact
                      this page has about a role -- a permission COUNT would
                      be a number invented for the look of it. */}
                  <span className="acct-pill">
                    {inUse} user{inUse === 1 ? "" : "s"} assigned
                  </span>
                  {/* Every role is removable, built-in included: those three
                      names are one industry's vocabulary, and a site that
                      uses none of them should not be stuck showing all
                      three. A role grants nothing, so removing one narrows a
                      dropdown rather than anyone's access. The server keeps
                      the two guards that matter -- a role still assigned to
                      an account, and the last role left. */}
                  <button type="button" className="acct-x"
                          disabled={busy || inUse > 0 || roles.length <= 1}
                          title={inUse > 0
                            ? `${inUse} user${inUse === 1 ? " is" : "s are"} still assigned to this role`
                            : roles.length <= 1 ? "The last role cannot be deleted"
                            : "Delete this role"}
                          aria-label={`Delete the role ${roleName(r)}`}
                          onClick={() => setRemovingRole(r)}>×</button>
                </div>
              );
            })}
          </div>
          {/* The built-ins are always listed above, so "empty" here means
              no role of the site's own yet. */}
          {!roles.some((r) => !builtinRoles.includes(r)) && (
            <div style={{ padding: "12px 2px" }}>
              <div className="acct-h3" style={{ marginTop: 0 }}>No custom roles</div>
              <p className="acct-sub">
                Create a role to label a group of users. It does not grant
                access by itself; System Access is assigned for each user.
              </p>
              <Button variant="outline" size="sm" style={{ marginTop: 8 }}
                      onClick={() => setAddingRole(true)} disabled={busy}>
                <UserPlus className="size-3.5" /> Create Role
              </Button>
            </div>
          )}
        </div>
      ) : (<>
        {/* The tab's own heading, as the other three tabs have: what the
            page is for, said once above the list rather than in it. */}
        <div style={{ marginBottom: 10 }}>
          <div className="acct-h2">Users</div>
          <p className="acct-sub">
            Manage user accounts, sign-in details, and access to systems on
            this device.
          </p>
        </div>
        <div className="acct-split">
          {/* ---------------------------------------------- the list */}
          <aside className="acct-list">
            <input className="input" placeholder="Search users..." aria-label="Search users"
                   value={search} onChange={(e) => setSearch(e.target.value)} />
            <Button className="acct-new" onClick={() => { setCreating(true); setPicked(""); }}>
              <UserPlus className="size-4" /> Add User
            </Button>
            {/* No "change my password" shortcut here any more: your own
                account is a row in this list like everyone else's, and it
                carries the button. Two ways to reach one dialog, one of them
                floating over the list, only raised the question of whether
                they did the same thing. */}
            <div className="acct-count">
              {users.length} user{users.length === 1 ? "" : "s"}
            </div>
            {shown.map((u) => (
              <button key={u.username} type="button"
                      className={"acct-item"
                        + (!creating && active?.username === u.username ? " is-active" : "")}
                      onClick={() => { setPicked(u.username); setCreating(false); }}>
                <Avatar name={u.username} enabled={u.enabled !== false} />
                <span style={{ minWidth: 0, flex: 1 }}>
                  <span className="acct-item-top">
                    <span className="acct-item-name">{u.username}</span>
                    <RoleBadge user={u} />
                  </span>
                  <span className="acct-sub acct-item-sub">
                    {u.enabled === false ? "Frozen"
                      : u.builtin || String(u.systems) === "*" ? "All Systems"
                      : systemCount(u.systems)}
                  </span>
                </span>
              </button>
            ))}
            {!shown.length && (
              <p className="acct-sub" style={{ padding: "8px 2px" }}>
                {users.length ? "No users match your search." : "No users yet."}
              </p>
            )}
          </aside>

          {/* ---------------------------------------------- the pane */}
          <div className="acct-pane">
            {creating ? (
              <>
                <div className="acct-pane-head">
                  <div>
                    <div className="acct-h2">Add User</div>
                    <p className="acct-sub">
                      One sign-in works on the hub and on every system granted below.
                    </p>
                  </div>
                  <Button variant="ghost" size="sm"
                          onClick={() => setCreating(false)}>Cancel</Button>
                </div>
                <div className="acct-form">
                  <label className="acct-field">
                    <span>Username</span>
                    <input className="input" value={nu.username}
                           onChange={(e) => setNu({ ...nu, username: e.target.value })} />
                  </label>
                  <label className="acct-field">
                    <span>Email Address <em className="acct-sub">optional - where a reset link goes</em></span>
                    <input className="input" type="email" value={nu.email}
                           placeholder="name@example.com" autoComplete="off"
                           onChange={(e) => setNu({ ...nu, email: e.target.value })} />
                  </label>
                  <label className="acct-field">
                    <span>Password <em className="acct-sub">min 8 characters</em></span>
                    {/* Same reveal as the change-password dialog: this is the
                        one field on the form whose typo is only discovered by
                        somebody else failing to sign in. */}
                    <PasswordInput className="input" value={nu.password}
                                   shown={showNew}
                                   onToggle={() => setShowNew((v) => !v)}
                                   onChange={(e) => setNu({ ...nu, password: e.target.value })} />
                  </label>
                  <label className="acct-field">
                    <span>Role</span>
                    <select className="input" value={nu.role}
                            onChange={(e) => setNu({ ...nu, role: e.target.value })}>
                      {roles.map((r) => <option key={r} value={r}>{roleName(r)}</option>)}
                    </select>
                  </label>
                  {/* The TIER, at creation. It can be set afterwards on the
                      account's own panel, but "make this person an admin" is
                      usually known while the account is being made -- and a
                      setting only reachable after the fact reads as one that
                      does not exist. Offered only to whoever may grant it. */}
                  {data?.can_set_tier && (
                    <label className="acct-field">
                      <span>Account Type</span>
                      <select className="input" value={nu.admin ? "admin" : "user"}
                              onChange={(e) => setNu({ ...nu,
                                admin: e.target.value === "admin" })}>
                        <option value="user">User - opens the systems granted below</option>
                        <option value="admin">Administrator - also manages their own users</option>
                      </select>
                      <em className="acct-sub">
                        An administrator adds and manages their own users, and
                        sets retention, email, Telegram and network. They
                        cannot create systems or change the board.
                      </em>
                    </label>
                  )}
                </div>
                <div className="acct-h3">System Access</div>
                <div className="acct-chips">
                  <SystemChip label="All Systems" on={nu.systems.includes("*")}
                              onClick={() => setNu({ ...nu,
                                systems: nu.systems.includes("*") ? [] : ["*"] })} />
                  {mySystems.map((s) => (
                    <SystemChip key={s} label={nameOf(s)} title={s}
                                on={nu.systems.includes("*") || nu.systems.includes(s)}
                                dim={nu.systems.includes("*")}
                                onClick={() => setNu({
                                  ...nu,
                                  systems: nu.systems.includes(s)
                                    ? nu.systems.filter((x) => x !== s)
                                    : [...nu.systems.filter((x) => x !== "*"), s],
                                })} />
                  ))}
                  {!mySystems.length && (
                    <span className="acct-sub">
                      {systems.length ? "none of your own systems yet" : "no systems yet"}
                      {" "}- create one first, or grant “All Systems”.
                    </span>
                  )}
                </div>
                <div style={{ marginTop: 14 }}>
                  <Button onClick={createUser} disabled={busy}>Add User</Button>
                </div>
              </>
            ) : !active ? (
              // The built-in `admin` is not a row in this list -- it is the
              // platform administrator, its password lives in the auth
              // config, and create_user refuses the name. So an empty list
              // is the NORMAL state of a fresh box, not a fault, and it says
              // where the account you are signed in as actually is.
              <div style={{ padding: 8 }}>
                <div className="acct-h3" style={{ marginTop: 0 }}>No users yet</div>
                <p className="acct-sub">
                  Add a user to grant someone access to this device.
                </p>
                <Button style={{ marginTop: 10 }}
                        onClick={() => { setCreating(true); setPicked(""); }}>
                  <UserPlus className="size-4" /> Add User
                </Button>
              </div>
            ) : (<>
              <div className="acct-pane-head">
                <div className="acct-who">
                  <Avatar name={active.username} big
                          enabled={active.enabled !== false} />
                  <div style={{ minWidth: 0 }}>
                    <div className="acct-name-row">
                      <span className="acct-h2">{active.username}</span>
                      <RoleBadge user={active} />
                    </div>
                    {/* Where a reset link for this account goes, when it has
                        one. Shown to everyone who can see the account;
                        changed by master or the developer only (menu below).
                        No address is a CHOICE, not a gap -- so the line says
                        what happens instead rather than reading as a fault:
                        the master is reset at the box itself, and anyone else
                        by whoever owns it. */}
                    <div className="acct-sub" style={{ marginBottom: 2 }}>
                      {active.email
                        ? active.email
                        : active.developer ? null
                        : active.builtin
                          ? (data?.panel_reset
                              ? <span>No email address - reset at the box: hold the USER
                                  button for 5 seconds and the panel shows a code</span>
                              : <span className="c-warn">No email address, and the front
                                  panel is off - this account has no way back in
                                  but a shell on the box</span>)
                          : <span>No email address - ask whoever owns the box to set a
                              new password for this user</span>}
                    </div>
                    <div className="acct-sub">
                      {active.developer
                        ? "Managed on the box - scripts/dev/make_dev_account.py"
                        : active.builtin
                        ? "Master Administrator"
                        : (active.enabled === false
                            ? "Frozen - cannot sign in"
                            : "Active - can sign in to the hub and its systems")}
                    </div>
                  </div>
                </div>
                {/* A developer row carries no controls at all: every one of
                    them is refused by the API. An admin who could reset this
                    password would just log in as it and hand themselves a
                    tier meant to require shell access; delete and disable are
                    the same hole pointed the other way. */}
                {/* One primary action and a menu for the rest: enable,
                    password and delete were three buttons of equal weight,
                    two of which end a session. Edit is the one an admin
                    reaches for; the other two are deliberate enough to be
                    worth a second click. */}
                {/* The administrator's own two controls, and only those: its
                    name and its password. Both ship as defaults, both are
                    self-service (the server asks for the current password),
                    and neither is available to anyone but the account
                    itself -- a second admin renaming the first would be a
                    takeover, not an edit. */}
                {active.builtin && (
                  <div className="acct-actions">
                    {active.username === user?.name ? (<>
                      <Button variant="outline" size="sm"
                              onClick={() => setRenaming(true)}>
                        <Pencil className="size-3.5" /> Change Username
                      </Button>
                      <Button variant="outline" size="sm"
                              onClick={() => setPwFor(active.username)}>
                        <KeyRound className="size-3.5" /> Change Password
                      </Button>
                      {/* The master's own recovery address: where its reset
                          link goes. Same store the first-run setup writes. */}
                      <Button variant="outline" size="sm"
                              onClick={() => setEmailFor(active.username)}>
                        <Pencil className="size-3.5" />
                        {active.email ? "Change Email Address" : "Add Email Address"}
                      </Button>
                      {/* Off again. Being able to SET the address is already
                          the authority to take the account over, so being
                          able to clear it grants nothing new -- and a box on
                          a closed network needs the way out. */}
                      {active.email && (
                        <Button variant="outline" size="sm" onClick={removeEmail}>
                          <Pencil className="size-3.5" /> Remove Email Address
                        </Button>
                      )}
                    </>) : (
                      <span className="acct-sub">
                        Only this account can change its own name or password.
                      </span>
                    )}
                  </div>
                )}
                {!active.developer && !active.builtin && (
                  <div className="acct-actions">
                    <Button variant="outline" size="sm"
                            onClick={() => setEditing(active.username)}>
                      <Pencil className="size-3.5" /> Edit User
                    </Button>
                    <div className="acct-menu-wrap">
                      <Button variant="ghost" size="icon-sm" aria-haspopup="menu"
                              aria-expanded={menuFor === active.username}
                              title="More actions"
                              onClick={() => setMenuFor(
                                menuFor === active.username ? "" : active.username)}>
                        <MoreVertical className="size-4" />
                      </Button>
                      {menuFor === active.username && (
                        <>
                          {/* Click-away closes it: a menu that only closes on
                              its own items is a menu you cannot dismiss. */}
                          <div className="acct-menu-backdrop"
                               onClick={() => setMenuFor("")} />
                          <div className="acct-menu" role="menu">
                            <button type="button" role="menuitem"
                                    onClick={() => { setMenuFor(""); setPwFor(active.username); }}>
                              <KeyRound className="size-3.5" />
                              Change Password
                            </button>
                            {/* Master and the developer only: the address is
                                where a reset link goes, so whoever sets it
                                can take the account over. can_set_tier is
                                the same "master or above" the server checks. */}
                            {/* ...and nothing to set on a box that does not
                                use email: the server refuses it, and a menu
                                item that always fails is worse than one that
                                is not there. Removing one stays offered
                                below, so turning email off never traps an
                                address on a row. */}
                            {data?.can_set_tier && data?.email !== false && (
                              <button type="button" role="menuitem"
                                      onClick={() => { setMenuFor(""); setEmailFor(active.username); }}>
                                <Pencil className="size-3.5" />
                                {active.email ? "Change Email Address" : "Add Email Address"}
                              </button>
                            )}
                            {/* ...and off again. Being able to SET the address
                                is already the authority to take the account
                                over, so removing it grants nothing new -- and
                                without this the Remove button existed only on
                                the master's own card, which is the one account
                                that has another way in. */}
                            {data?.can_set_tier && active.email && (
                              <button type="button" role="menuitem"
                                      onClick={() => { setMenuFor(""); removeEmail(); }}>
                                <Pencil className="size-3.5" /> Remove Email Address
                              </button>
                            )}
                            <button type="button" role="menuitem"
                                    onClick={() => {
                                      setMenuFor("");
                                      patch(active.username,
                                            { enabled: active.enabled === false },
                                            active.enabled === false
                                              ? `${active.username} unfrozen`
                                              : `${active.username} frozen - session ends now`);
                                    }}>
                              <Power className="size-3.5" />
                              {active.enabled === false ? "Unfreeze User" : "Freeze User"}
                            </button>
                            <button type="button" role="menuitem" className="is-danger"
                                    onClick={() => { setMenuFor(""); remove(active.username); }}>
                              <Trash2 className="size-3.5" /> Delete User
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {active.builtin && (<>
                {/* No Role tab and no editable chips: the administrator's
                    badge is deployment branding from auth.toml, and its access
                    is not a list anyone maintains -- it is every system, by
                    definition. Showing empty versions of both would suggest
                    otherwise. Activity is the one that still means something. */}
                <div className="acct-subtabs">
                  <button type="button"
                          className={"acct-tab" + (detail !== "activity" ? " is-active" : "")}
                          onClick={() => setDetail("access")}>System Access</button>
                  <button type="button"
                          className={"acct-tab" + (detail === "activity" ? " is-active" : "")}
                          onClick={() => setDetail("activity")}>Account Activity</button>
                </div>
                {detail === "activity" ? (
                  <Activity username={active.username} nameOf={nameOf}
                            dev={!!user?.is_developer} />
                ) : (
                  <>
                    <div className="acct-chips">
                      <SystemChip label="All Systems" on dim={false} onClick={() => {}} />
                    </div>
                    {/* Both promises are the server's: auth.allowed answers
                        True for this account on ANY slug, so a system made
                        later is included, and _refuse_builtin turns away
                        every edit (enabled included) and delete of it. */}
                    <p className="acct-sub" style={{ marginTop: 8 }}>
                      Has full access to all systems on this device, including
                      systems added in the future. This account cannot be
                      disabled or deleted.
                    </p>
                  </>
                )}
              </>)}

              {!active.developer && !active.builtin && (<>
                <div className="acct-subtabs">
                  <button type="button"
                          className={"acct-tab" + (detail === "access" ? " is-active" : "")}
                          onClick={() => setDetail("access")}>System Access</button>
                  <button type="button"
                          className={"acct-tab" + (detail === "role" ? " is-active" : "")}
                          onClick={() => setDetail("role")}>Role</button>
                  {/* What this account actually DID. It was a whole nav page
                      of everyone's entries mixed together, which answered
                      "what happened on this box" but never "what has this
                      account been doing" without reading past everyone
                      else's. Same log, filtered to the account already on
                      screen. */}
                  <button type="button"
                          className={"acct-tab" + (detail === "activity" ? " is-active" : "")}
                          onClick={() => setDetail("activity")}>Account Activity</button>
                </div>

                {detail === "activity" ? (
                  <Activity username={active.username} nameOf={nameOf}
                            dev={!!user?.is_developer} />
                ) : detail === "access" ? (<>
                  {/* `manageable` is the server's answer to "may the viewer
                      act on this row", by the same rule the routes enforce --
                      False on their OWN row, which is listed so they can SEE
                      their access, not widen it. */}
                  <div className="acct-chips">
                    <SystemChip label="All Systems" on={activeAll}
                                disabled={active.manageable === false}
                                onClick={() => toggleSystem(active.username, active.systems, "*")} />
                    {systems.map((s) => (
                      <SystemChip key={s} title={s}
                                  label={owners[s] ? `${nameOf(s)} · ${owners[s]}` : nameOf(s)}
                                  on={activeAll || activeSlugs.includes(s)} dim={activeAll}
                                  disabled={active.manageable === false}
                                  onClick={() => toggleSystem(active.username, active.systems, s)} />
                    ))}
                  </div>
                  {/* Only reachable when the viewer sees none of the systems
                      this user holds (the server keeps at least one grant). */}
                  {!activeAll && !systems.some((s) => activeSlugs.includes(s)) && (
                    <div style={{ marginTop: 10 }}>
                      <div className="acct-h3" style={{ marginTop: 0 }}>No systems assigned</div>
                      <p className="acct-sub">
                        This user cannot access any systems until access is granted.
                      </p>
                    </div>
                  )}
                  {/* One tree, three levels, in the order they apply:
                      category, page, and what may be changed on it. */}
                  <AccessTree active={active} systems={systems} data={data}
                              nameOf={nameOf} patch={patch} toast={toast} />
                  <TierControls active={active} busy={busy} patch={patch}
                                canSet={!!data?.can_set_tier} />
                </>) : (
                  <label className="acct-field" style={{ maxWidth: 280 }}>
                    <span>Role</span>
                    <select className="input" value={active.role || "operator"}
                            onChange={(e) => patch(active.username, { role: e.target.value },
                                                   `${active.username} is now ${e.target.value}`)}>
                      {roles.map((r) => <option key={r} value={r}>{roleName(r)}</option>)}
                    </select>
                    <em className="acct-sub">
                      A label for what kind of user this is. Access comes from
                      System Access, not from here.
                    </em>
                  </label>
                )}
              </>)}
            </>)}
          </div>
        </div>
      </>)}

      {/* Typed twice, masked, with the rule stated before the attempt rather
          than after it. Whether it is a change or a reset is only a matter of
          whose row it was opened from -- the endpoint is the same one. */}
      <FormDialog
        open={!!pwFor}
        onOpenChange={(o) => !o && setPwFor("")}
        title={pwFor === user?.name ? "Change your password?"
                                    : "Change this user's password?"}
        description={pwFor === user?.name
          // Said BEFORE the change (UAT-108): the server ends every other
          // session of the account, which drops a colleague mid-task on
          // another laptop with no word as to why. NOT "sessions may remain
          // signed in": both routes revoke (change_own_password keeps only
          // this one, reset_user_password keeps none), whatever the policy.
          ? "The new password will be used for future sign-ins. You stay "
            + "signed in here; every other browser signed in as you is "
            + "signed out."
          : `The new password will be used for future sign-ins. ${pwFor} is `
            + "signed out everywhere and must sign in again with it."}
        submitLabel="Change Password"
        busy={busy}
        fields={[
          // Your own change proves it is you: a session is something an
          // unattended browser also has, and this is the action that locks
          // everyone else out of the account. Resetting SOMEBODY ELSE's is
          // an admin act and asks for no password of theirs -- that is the
          // point of a reset.
          ...(pwFor === user?.name
            ? [{ name: "current", label: "Current password", type: "password",
                 required: true, autoComplete: "current-password" }]
            : []),
          { name: "password", label: "New password", type: "password",
            required: true, autoComplete: "new-password",
            hint: "At least 8 characters." },
          { name: "confirm", label: "Confirm new password", type: "password",
            required: true, autoComplete: "new-password" },
        ]}
        onSubmit={savePassword} />

      {/* Creating a role, in the product's own dialog rather than the
          browser's. The rules the server enforces are stated BEFORE the
          attempt: a prompt() could only report them afterwards, as a failure. */}
      <FormDialog
        open={addingRole}
        onOpenChange={(o) => !o && setAddingRole(false)}
        title="Create a role"
        description={"A role describes what kind of user an account is. It "
                     + "does not grant access by itself; System Access is "
                     + "assigned for each user."}
        submitLabel="Create Role"
        busy={busy}
        fields={[
          { name: "name", label: "Role name", required: true,
            placeholder: "shift lead",
            hint: "Letters, numbers, spaces, - and _ · up to 30 characters. "
                  + "Stored in lower case." },
        ]}
        onSubmit={addRole} />

      {/* Removing one narrows a dropdown; it takes nobody's access away. Said
          plainly, because "remove" on an accounts page reads like it might.
          Not "users assigned may be affected": a role still assigned cannot
          be deleted at all (auth.delete_role refuses, and the button is off),
          so this dialog only ever opens on a role nobody holds. Nor "cannot
          be undone": creating the same name again restores it exactly. */}
      <ConfirmDialog
        open={!!removingRole}
        onOpenChange={(o) => !o && setRemovingRole("")}
        title="Delete this role?"
        description={`${roleName(removingRole)} will be removed from the role `
                     + "list. No users are assigned to it, so no one's access "
                     + "changes. You can create it again later."}
        confirmLabel="Delete Role"
        destructive
        busy={busy}
        onConfirm={() => removeRole(removingRole)} />

      {/* Renaming the account you are signed in as, so it asks for the
          password: the name is half the credential, and the server re-signs
          the session cookie under the new one. */}
      <FormDialog
        open={!!emailFor}
        onOpenChange={(o) => !o && setEmailFor("")}
        title={emailFor ? `Email address for ${emailFor}` : "Email address"}
        description={"Where a password-reset link for this account is sent. One "
                     + "address, one account."}
        submitLabel="Save Email Address"
        busy={busy}
        fields={[
          { name: "email", label: "Email address", type: "email", required: true,
            defaultValue: (data?.users || []).find((u) => u.username === emailFor)?.email || "",
            autoComplete: "off" },
        ]}
        onSubmit={async (v) => {
          const who = emailFor;
          setBusy(true);
          try {
            // The master's address lives with the master account (the
            // account-setup store); everyone else's on their row.
            if (users.find((u) => u.username === who)?.builtin) {
              await api.post("/api/auth/account-setup", { email: v.email });
            } else {
              await api.patch(`/api/users/${who}`, { email: v.email });
            }
            toast.success(`Email address saved for ${who}`);
            setEmailFor("");
            load();
          } catch (e) { toast.error("Could not save the email address", e.message); }
          finally { setBusy(false); }
        }} />

      <FormDialog
        open={renaming}
        onOpenChange={(o) => !o && setRenaming(false)}
        title="Change your username"
        description={"“admin” is the name the box ships with, not one you "
                     + "chose. You stay signed in - the page reloads under the "
                     + "new name."}
        submitLabel="Change Username"
        busy={busy}
        fields={[
          { name: "username", label: "New username", required: true,
            defaultValue: user?.name || "",
            hint: "Letters, digits and _ - one word, no spaces." },
          { name: "current", label: "Current password", type: "password",
            required: true, autoComplete: "current-password" },
        ]}
        onSubmit={saveUsername} />

      {/* Role and enabled in one place. They were inline controls that
          applied the instant they were touched -- fine for a checkbox, less
          so for the one that ends someone's session. */}
      <FormDialog
        open={!!editing}
        onOpenChange={(o) => !o && setEditing("")}
        title={editing ? `Edit ${editing}` : "Edit user"}
        description="Role is a label; access comes from System Access."
        submitLabel="Save Changes"
        busy={busy}
        fields={[
          { name: "role", label: "Role", required: true,
            defaultValue: users.find((u) => u.username === editing)?.role || "operator",
            options: roles.map((r) => ({ value: r, label: roleName(r) })) },
          // "Account status" with "Freeze", not "Sign-in" with "Disabled":
          // the account is not being switched off, it is being held. Nothing
          // it owns is touched and turning it back on restores it exactly,
          // which is the distinction "disabled" fails to make on a page where
          // the neighbouring action is Delete.
          { name: "enabled", label: "Account status", required: true,
            defaultValue: users.find((u) => u.username === editing)?.enabled === false
              ? "no" : "yes",
            options: [{ value: "yes", label: "Enabled - can sign in" },
                      { value: "no", label: "Freeze - cannot sign in" }],
            hint: "Freezing ends any session this account has open. Nothing "
                  + "is deleted; unfreeze and it is exactly as it was." },
        ]}
        onSubmit={async (v) => {
          const who = editing;
          setEditing("");
          await patch(who, { role: v.role, enabled: v.enabled === "yes" },
                      `${who} saved`);
        }} />
    </div>
  );
}

/** One account's entries from the platform audit log.
 *
 *  Its own fetch rather than a slice of a page-wide load: the log is
 *  unbounded and only ever looked at one account at a time, so pulling
 *  everyone's on every visit to Account Management would cost the most on the
 *  boxes with the most history. */
//: Rows per page. 1000 is the server's own ceiling (core/routes/audit.py
//: clamps to it), so the largest choice here is the largest answer there is
//: rather than a number the page would silently not get.
const ACTIVITY_SIZES = [100, 250, 500, 1000];

// How long ago an epoch was, in the words an operator scans a list with.
function agoText(epoch) {
  if (!epoch) return "-";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (s < 90) return "Active now";
  if (s < 3600) return `Last active ${Math.round(s / 60)} min ago`;
  if (s < 86400) return `Last active ${Math.round(s / 3600)} h ago`;
  return `Last active ${Math.round(s / 86400)} d ago`;
}

// Login Sessions - REGISTERED DEVICES, each with the sessions it holds.
//
// A device is a browser (core/services/auth.py, "registered sign-in
// devices"): one operator signing in eleven times from one laptop is ONE
// device with its sessions under it, not eleven rows. An account may
// register MAX_DEVICES of them; one unused for 30 days is removed and must
// sign in again. Two different things can be ended here, and the words keep
// them apart:
//   * Sign Out ends ONE session; the device stays registered.
//   * Remove Device unregisters the browser AND ends all its sessions; it
//     has to sign in and register again, and counts toward the limit again.
// An admin sees every account's devices, grouped by account; anyone else
// sees their own. No device id, token or session id is ever rendered.
//
// The MAC is extra information the box may or may not be able to see (its
// own LAN only, and a phone's private Wi-Fi address is random), so it is
// shown when there is one and "-" with the reason otherwise -- never called
// verified.
const MAC_HIDDEN = "Not visible from another network or a phone with a private Wi-Fi address";

function exactTime(epoch) {
  if (!epoch) return "";
  try { return new Date(epoch * 1000).toLocaleString(); } catch { return ""; }
}

export function SessionsPane({ me, confirm }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState({});

  const load = useCallback(() => {
    api.get("/api/auth/sessions").then(setData)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);
  // Paused while the tab is hidden, refetched on return (useVisibleInterval).
  useVisibleInterval(load, 30000);

  async function revoke(sid, who) {
    // The route ends that one session id and nothing else: the account and
    // the device's registration are untouched.
    if (!await confirm({
      title: "Sign out this session?",
      description: "This will end the selected login. The device stays "
                   + "registered, and the user can sign in again if they "
                   + "still have access.",
      confirmLabel: "Sign Out",
    })) return;
    setBusy(true);
    try {
      await api.del(`/api/auth/sessions/${encodeURIComponent(sid)}`);
      toast.info(who === me ? "Session signed out" : `Signed out a session of ${who}`);
      load();
    } catch (e) { toast.error("Could not sign out the session", e.message); }
    finally { setBusy(false); }
  }

  async function revokeOthers() {
    // MY account's other sessions, not the device's: revoke-others calls
    // revoke_sessions_of(me, keep_sid=this one). Registrations stay.
    if (!await confirm({
      title: "Sign out other sessions?",
      description: "This will end every other session signed in as your "
                   + "account. Your current session will remain signed in, "
                   + "and every device stays registered.",
      confirmLabel: "Sign Out Other Sessions",
    })) return;
    setBusy(true);
    try {
      const d = await api.post("/api/auth/sessions/revoke-others");
      const n = d.revoked || 0;
      toast.success(`Signed out ${n} other session${n === 1 ? "" : "s"}`,
                    "This one stays signed in.");
      load();
    } catch (e) { toast.error("Could not sign out the other sessions", e.message); }
    finally { setBusy(false); }
  }

  async function removeDevice(dev) {
    if (!await confirm({
      title: "Remove this device?",
      description: `${dev.name || "This device"} will be signed out and `
                   + "unregistered. To use this box again it must sign in "
                   + "and register again, which counts toward the limit of "
                   + `${data?.device_limit || 10} devices.`,
      confirmLabel: "Remove Device",
      destructive: true,
    })) return;
    setBusy(true);
    try {
      await api.del(`/api/auth/devices/${encodeURIComponent(dev.id)}`);
      toast.info(dev.user === me ? "Device removed" : `Removed a device of ${dev.user}`);
      load();
    } catch (e) { toast.error("Could not remove the device", e.message); }
    finally { setBusy(false); }
  }

  async function removeOthers() {
    if (!await confirm({
      title: "Remove other devices?",
      description: "Every other device registered to your account will be "
                   + "signed out and unregistered. Each one must sign in and "
                   + "register again to use this box. This device stays "
                   + "signed in.",
      confirmLabel: "Remove Other Devices",
      destructive: true,
    })) return;
    setBusy(true);
    try {
      const d = await api.post("/api/auth/devices/remove-others");
      const n = d.removed || 0;
      toast.success(`Removed ${n} other device${n === 1 ? "" : "s"}`,
                    "This one stays signed in.");
      load();
    } catch (e) { toast.error("Could not remove the other devices", e.message); }
    finally { setBusy(false); }
  }

  const rows = data?.sessions || [];
  const devices = data?.devices || [];
  const others = rows.filter((s) => s.user === me && !s.current).length;
  const otherDevices = devices.filter((d) => d.user === me && !d.current).length;
  const limit = data?.device_limit || 10;
  const days = data?.device_expiry_days || 30;
  // Grouped by account: mine first, then the others by name. A non-admin's
  // list only ever has their own.
  const owners = [...new Set(devices.map((d) => d.user))]
    .sort((a, b) => (a === me ? -1 : b === me ? 1 : a.localeCompare(b)));
  if (data && !owners.includes(me)) owners.unshift(me);
  const counter = (who) => {
    const n = data?.device_counts?.[who] || 0;
    return who === me && data && !data.device_capped
      ? `Registered devices: ${n} (no limit for this account)`
      : `Registered devices: ${n} of ${limit}`;
  };
  return (
    <div className="acct-pane">
      {/* No Auto logout switch here. It is a box-wide decision, and box-wide
          decisions live on Settings > System now -- one page for them, rather
          than one control on whichever page happened to use it. The line below
          says where it went, because this is where it used to be. */}
      <div className="acct-pane-head">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="acct-h2">Login Sessions</div>
          <p className="acct-sub">
            {data?.admin
              ? "View the devices registered to sign in to this box and end "
                + "sessions that are no longer needed. Signing out a session "
                + "does not delete the user account."
              : "View the devices registered to your account and end sessions "
                + "that are no longer needed. Signing out a session does not "
                + "delete the user account."}
          </p>
          <p className="acct-sub">
            {`An account can register up to ${limit} devices. A device that `
             + `has not been active for ${days} days is removed automatically.`}
          </p>
          {data?.admin && (
            <p className="acct-sub">
              {data.auto_logout
                ? `A sign-in ends ${data.idle_minutes} minutes after it starts.`
                : "A sign-in stays signed in."}
              {" "}Automatic Sign-out is on Settings &gt; System.
            </p>
          )}
        </div>
        <div style={{ marginLeft: "auto", flex: "none", display: "flex",
                      gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
          {others > 0 && (
            <Button variant="outline" size="sm" onClick={revokeOthers} disabled={busy}>
              <Power className="size-3.5" /> Sign Out Other Sessions
            </Button>
          )}
          {otherDevices > 0 && (
            <Button variant="outline" size="sm" onClick={removeOthers} disabled={busy}>
              <Trash2 className="size-3.5" /> Remove Other Devices
            </Button>
          )}
        </div>
      </div>
      {error && <p className="acct-sub" role="alert">{error}</p>}
      {data?.unlisted && (
        <p className="acct-sub">
          This sign-in predates the session list - it works as before, but
          only sign-ins made from now on appear here.
        </p>
      )}
      {data && !error && !devices.length && (
        <div style={{ padding: "8px 2px" }}>
          <div className="acct-h3" style={{ marginTop: 0 }}>No registered devices</div>
          <p className="acct-sub">A device is registered when it signs in.</p>
        </div>
      )}
      {data && owners.map((who) => {
        const list = devices.filter((d) => d.user === who);
        if (!list.length && who !== me) return null;
        return (
          <section key={who} aria-label={`Registered devices of ${who}`}>
            <div className="acct-h3" style={{ marginBottom: 0 }}>
              {data.admin ? `${who} - ` : ""}{counter(who)}
            </div>
            <ul className="acct-rolelist" style={{ listStyle: "none", padding: 0 }}>
              {list.map((d) => (
                <li key={d.id} className="acct-role acct-device">
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="acct-role-name">
                      {d.name || "Unknown device"}
                      {d.current && (
                        <span className="acct-pill" style={{ marginLeft: 8 }}>
                          This device
                        </span>
                      )}
                      {d.legacy && (
                        <span className="acct-pill" style={{ marginLeft: 8 }}
                              title="Signed in before device registration. It registers at its next sign-in and does not count toward the limit.">
                          Not registered yet
                        </span>
                      )}
                    </div>
                    <div className="acct-sub acct-device-line">
                      <span className={`acct-status-dot${d.online ? " is-online" : ""}`}
                            aria-hidden="true" />
                      <span>{d.online ? "Online" : "Offline"}</span>
                      <span> · IP {d.ip || "-"}</span>
                      <span> · MAC </span>
                      {d.mac
                        ? <span>{d.mac}</span>
                        : <span title={MAC_HIDDEN}>-<span className="sr-only">{` (${MAC_HIDDEN})`}</span></span>}
                      <span> · </span>
                      <time dateTime={d.seen ? new Date(d.seen * 1000).toISOString() : undefined}
                            title={exactTime(d.seen)}>
                        {agoText(d.seen)}
                      </time>
                    </div>
                    {d.sessions_count > 0 && (
                      <button type="button" className="link-btn acct-sessions-toggle"
                              aria-expanded={!!open[d.id]}
                              onClick={() => setOpen((o) => ({ ...o, [d.id]: !o[d.id] }))}>
                        {open[d.id] ? "Hide Sessions" : "Show Sessions"}
                        {` (${d.sessions_count})`}
                      </button>
                    )}
                    {open[d.id] && (
                      <ul className="acct-sessions" style={{ listStyle: "none", padding: 0 }}>
                        {d.sessions.map((s) => (
                          <li key={s.sid} className="acct-session">
                            <span className="acct-sub" style={{ flex: 1 }}>
                              {`Signed in ${exactTime(s.login)}`}
                              {s.ip ? ` · ${s.ip}` : ""} · {agoText(s.seen)}
                            </span>
                            {s.current
                              ? <span className="acct-pill">Current Session</span>
                              : (
                                <Button variant="outline" size="sm" disabled={busy}
                                        aria-label={`Sign out a session of ${d.user} on ${d.name || "this device"}`}
                                        onClick={() => revoke(s.sid, d.user)}>
                                  Sign Out
                                </Button>)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  {!d.current && (
                    <Button variant="outline" size="sm" disabled={busy}
                            aria-label={`Remove device ${d.name || ""} of ${d.user}`.replace("  ", " ")}
                            onClick={() => removeDevice(d)}>
                      Remove Device
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}


// `nameOf` turns a system slug in a detail into the name it goes by now;
// `dev` puts the raw action id and detail beside the customer wording
// (components/auditActions.js), which nobody else is shown.
function Activity({ username, nameOf, dev }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [size, setSize] = useState(100);
  const [page, setPage] = useState(0);
  // Whether a NEXT page exists, from the server. Not a total: the log is
  // append-only and can be very long, and counting all of it on every page
  // turn buys a number nobody acts on.
  const [more, setMore] = useState(false);

  // A different account, or a different page size, starts at the top again --
  // page 3 of the old query is not page 3 of the new one.
  useEffect(() => { setPage(0); }, [username, size]);

  useEffect(() => {
    let alive = true;
    setRows(null); setError("");
    // An empty username is not a filter for "" -- it is no filter at all,
    // which is what the Activity Log tab wants.
    api.get(`/api/audit?${username ? `username=${encodeURIComponent(username)}&` : ""}`
            + `limit=${size}&offset=${page * size}`)
      .then((d) => {
        if (!alive) return;
        setRows(d.entries || []);
        setMore(!!d.has_more);
      })
      .catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [username, size, page]);

  if (error) return <p className="acct-sub">Could not load the activity log: {error}</p>;
  if (!rows) return <p className="acct-sub">Loading…</p>;
  if (!rows.length && !page) {
    return (
      <div style={{ padding: "8px 2px" }}>
        <div className="acct-h3" style={{ marginTop: 0 }}>No activity recorded</div>
        <p className="acct-sub">
          {username ? "Activity for this user will appear here when available."
                    : "Sign-ins and other recorded actions will appear here "
                      + "when available."}
        </p>
      </div>
    );
  }
  const from = page * size + 1;
  return (<>
    <div className="acct-activity">
      {rows.map((e, i) => {
        // Raw for a developer (every key, as stored); the customer wording
        // for everyone else, which drops a detail the name already says.
        const raw = detailText(e.detail);
        const shown = dev ? raw : actionDetail(e, raw, nameOf);
        const outcome = resultLabel(e.result);
        return (
          <div key={i} className="acct-event">
            <span className="acct-event-when" title={String(e.ts || "")}>
              {whenText(e.ts)}
            </span>
            {/* WHO, when the list is not already about one account. Without it
                every row of the unfiltered log reads as the same anonymous
                event. */}
            {!username ? <span className="acct-badge">{e.username || "-"}</span> : null}
            <span className="acct-event-what">{actionLabel(e)}</span>
            {dev ? <code className="acct-sub">{e.action}</code> : null}
            {/* A FAILED entry is the one somebody comes to this tab looking for
                -- three failed logins on an account is the thing worth seeing at
                a glance, and it read identically to a successful one. */}
            {outcome ? <span className="acct-badge is-fail">{outcome}</span> : null}
            {shown ? <span className="acct-sub">{shown}</span> : null}
            {e.source_ip ? <span className="acct-sub">{e.source_ip}</span> : null}
          </div>
        );
      })}
    </div>
    {/* Which rows these are, then the two ways to move. "1–100" rather than
        "page 1 of ?": the page number is an artefact of the size picker, and
        the row numbers are what the reader is actually looking at. */}
    <div className="acct-pager">
      <span className="acct-sub">
        {rows.length ? `${from}–${from + rows.length - 1}` : "none"} on this page
      </span>
      <select className="input" style={{ width: 110, flex: "none" }}
              value={size} onChange={(e) => setSize(Number(e.target.value))}>
        {ACTIVITY_SIZES.map((n) => (
          <option key={n} value={n}>{n} per page</option>
        ))}
      </select>
      <Button variant="outline" size="sm" disabled={!page}
              onClick={() => setPage((p) => Math.max(0, p - 1))}>Previous</Button>
      <Button variant="outline" size="sm" disabled={!more}
              onClick={() => setPage((p) => p + 1)}>Next</Button>
    </div>
  </>);
}

/** The initial in a coloured disc, with a dot for enabled. Derived from the
 *  NAME -- there are no avatars to upload, and a photo nobody set is a photo
 *  that would have to be invented. */
function Avatar({ name, big, enabled }) {
  const n = String(name || "?");
  // Stable per name: the same account is the same colour on every visit.
  const hue = [...n].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
  return (
    <span className={"acct-avatar" + (big ? " is-big" : "")}
          style={{ background: `hsl(${hue} 45% 32%)`, color: `hsl(${hue} 90% 88%)` }}>
      {n.slice(0, 1).toUpperCase()}
      <i className={"acct-dot" + (enabled ? " is-on" : "")} aria-hidden />
    </span>
  );
}

/** A developer has no role and cannot be given one: roles describe the people
 *  who USE a deployment, and this account is the one that built it. */
function RoleBadge({ user }) {
  if (user.developer) return <span className="acct-badge is-dev">Developer</span>;
  // The TIER, when it is one, in place of the role. They are different
  // things -- a role is a label, a tier grants -- and an account that can
  // manage other accounts should say so where the eye already looks for what
  // kind of account it is, not only inside its own detail panel.
  if (user.builtin) return <span className="acct-badge is-dev">Master Admin</span>;
  if (user.admin) return <span className="acct-badge is-dev">Administrator</span>;
  return <span className="acct-badge">{roleName(user.role || "operator")}</span>;
}

/** A stored role name as it is shown: roles are stored in lower case
 *  ("shift lead"), and a label reads in title case ("Shift Lead"). Display
 *  only -- the value sent back is always the stored one. */
function roleName(name) {
  return String(name || "").replace(/(^|[\s_-])(\w)/g,
                                    (m, sep, c) => sep + c.toUpperCase());
}

/** What the three built-in roles are FOR, said as labels. Not the spec's
 *  "for users who need technical and configuration access": a role grants
 *  nothing (auth.BUILTIN_ROLES), so a sentence that reads as a permission
 *  set would promise access the role does not give. A removed or custom
 *  role has no note. */
const ROLE_NOTES = {
  engineer: "A label for users who do technical and configuration work.",
  operator: "A label for users who operate and monitor the system.",
  viewer: "A label for users who only view system information.",
};

/** "1 system" / "3 systems" from a stored allowlist. */
function systemCount(systems) {
  const n = String(systems || "").split(",").filter(Boolean).length;
  return `${n} system${n === 1 ? "" : "s"}`;
}

const ALL_SECTION_KEYS = SECTIONS.map((s) => s.key);

/** ONE tree for everything an account may reach, per granted system.
 *
 *  There were three lists here: a row of category chips, a row of page chips,
 *  and nothing at all for what the account could DO once a page was open --
 *  opening a page WAS the permission, so the only two settings were "can
 *  change everything on it" and "cannot see it". Reading them meant holding
 *  two flat rows of chips in your head and knowing which narrowed which.
 *
 *  So: the shape the permissions actually have. Category, the pages inside it,
 *  and under each page the things that page can change (core/services/caps.py,
 *  sent by the server so a checkbox cannot outlive what enforces it). A
 *  checkbox on the right of every row, at every level.
 *
 *  Who may tick what does not change: the CATEGORY is the admin's to narrow
 *  for their own staff, and the page and its capabilities are master's --
 *  an admin who could tick those would be handing themselves what master
 *  withheld. Rows they may not set are still SHOWN, and disabled, because
 *  "why can this account not delete a zone" is a question the page should
 *  answer rather than hide.
 */
function AccessTree({ active, systems, nameOf, patch, toast, data }) {
  const slugs = String(active.systems || "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  // Scoped to systems THIS admin can see (the payload's list): an "all
  // systems" grant on the account must not leak other owners' systems here.
  const shown = slugs.includes("*") ? systems
    : systems.filter((s) => slugs.includes(s));
  // Every level of the tree is master's now, and none of it is settable on a
  // row the viewer does not outrank -- your OWN row included, which is the one
  // the page used to offer controls on that the route would then refuse.
  const mine = active.manageable !== false;
  const canSections = !!data?.can_set_sections && mine;
  const canPages = !!data?.can_set_pages && mine;
  const canCaps = !!data?.can_set_caps && mine;
  const pageList = data?.pages || [];
  const pageLabel = Object.fromEntries(pageList.map((p) => [p.page, p.label]));
  const allPages = pageList.map((p) => p.page);
  const sections = data?.sections
    || SECTIONS.map((s) => ({ key: s.key, label: s.label, pages: s.pages }));
  const capsFor = Object.fromEntries(
    (data?.capabilities || []).map((e) => [e.page, e.caps]));
  const defaultCaps = data?.default_caps || [];
  // Capabilities only mean anything on the tier that holds any: an ordinary
  // account cannot change these pages at all, so ticking one would promise
  // something the routes would still refuse.
  const capsApply = !!active.admin;
  // Which branches are open. Closed by default: the whole tree expanded is
  // sixty rows, and the question is almost always about one page.
  const [open, setOpen] = useState(() => new Set());
  const isOpen = (key) => open.has(key);
  const toggle = (key) => setOpen((cur) => {
    const next = new Set(cur);
    if (!next.delete(key)) next.add(key);
    return next;
  });

  if (!shown.length) return null;

  const listOf = (held, slug, fallback) => {
    const raw = String((held || {})[slug] || "")
      .split(",").map((k) => k.trim()).filter(Boolean);
    return raw.length ? raw : fallback;
  };
  const sectionsOf = (slug) => listOf(active.sections, slug, ALL_SECTION_KEYS);
  const pagesOf = (slug) => listOf(active.pages, slug, allPages);
  // ABSENCE is the tier's defaults, and an empty stored value is a real
  // answer ("changes nothing here") -- so this asks whether the key exists
  // rather than whether it is truthy.
  const capsOf = (slug) => (
    slug in (active.caps || {})
      ? String(active.caps[slug]).split(",").map((c) => c.trim()).filter(Boolean)
      : defaultCaps);

  const toggleSection = (slug, key) => {
    const cur = sectionsOf(slug);
    const next = cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key];
    if (!next.length) {
      return void toast.error("Leave at least one category visible",
        "To take the whole system away, remove it from System Access.");
    }
    patch(active.username, { sections: {
      [slug]: next.length === ALL_SECTION_KEYS.length ? "*" : next } });
  };
  const togglePage = (slug, page) => {
    const cur = pagesOf(slug);
    const next = cur.includes(page) ? cur.filter((k) => k !== page) : [...cur, page];
    if (!next.length) {
      return void toast.error("Leave at least one page",
        "To take the whole system away, remove it from System Access.");
    }
    patch(active.username, { pages: {
      [slug]: next.length === allPages.length ? "*" : next } });
  };
  const toggleCap = (slug, cap) => {
    const cur = capsOf(slug);
    // Always sent as a LIST, never as "*": once master has touched this the
    // stored answer is exact, so adding a capability to the product later
    // does not hand it to everyone who was ticked "all" a year ago.
    patch(active.username, { caps: {
      [slug]: cur.includes(cap) ? cur.filter((c) => c !== cap) : [...cur, cap] } });
  };

  return (
    <div style={{ marginTop: 14 }}>
      <div className="text-xs font-semibold" style={{ marginBottom: 6 }}>
        Access
      </div>
      <div className="acct-tree">
        {shown.map((slug) => {
          const secOn = sectionsOf(slug);
          const pgOn = pagesOf(slug);
          const capOn = capsOf(slug);
          const sysOpen = isOpen(slug);
          return (
            <div key={slug}>
              <Row level={0} label={nameOf(slug)} title={slug} folder
                   open={sysOpen} onToggle={() => toggle(slug)} />
              {sysOpen && sections.map((sec) => {
                // Only the pages this product has a nav entry for: a category
                // may claim one that is not grantable (Zones is a TAB of
                // Camera Setup), and its row would store nothing.
                const pages = sec.pages.filter((p) => allPages.includes(p));
                if (!pages.length) return null;
                const secKey = `${slug}/${sec.key}`;
                const secShown = secOn.includes(sec.key) && isOpen(secKey);
                return (
                  <div key={sec.key}>
                    <Row level={1} label={sec.label} folder
                         open={secShown} onToggle={() => toggle(secKey)}
                         checked={secOn.includes(sec.key)}
                         disabled={!canSections}
                         onChange={() => toggleSection(slug, sec.key)} />
                    {secShown && pages.map((page) => {
                      const pageKey = `${slug}/${page}`;
                      const caps = capsApply && pgOn.includes(page)
                        ? (capsFor[page] || []) : [];
                      // BOOLEAN, not a count: `caps.length && ...` is 0 when
                      // there are none, and React draws a 0 rather than
                      // nothing.
                      const pageShown = caps.length > 0 && isOpen(pageKey);
                      return (
                        <div key={page}>
                          <Row level={2} label={pageLabel[page] || page}
                               title={page}
                               folder={!!caps.length} open={!!pageShown}
                               onToggle={() => toggle(pageKey)}
                               checked={pgOn.includes(page)}
                               disabled={!canPages}
                               onChange={() => togglePage(slug, page)} />
                          {pageShown && caps.map((cap) => (
                            <Row key={cap.id} level={3} label={cap.label}
                                 title={cap.id}
                                 checked={capOn.includes(cap.id)}
                                 disabled={!canCaps}
                                 onChange={() => toggleCap(slug, cap.id)} />
                          ))}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** One row: its toggle, its checkbox, its name. Indentation is the only thing
 *  saying which level it belongs to, so the label is what grows. A folder with
 *  nothing under it yet (a page whose capabilities are hidden) keeps the
 *  toggle's WIDTH and not its glyph, or the labels would not line up. */
function Row({ level, label, title, folder, open, onToggle,
               checked, onChange, disabled }) {
  const has = onChange !== undefined;
  return (
    <div className="acct-tree-row" style={{ paddingLeft: level * 24 }}
         title={title || ""}>
      {folder ? (
        <button type="button" className="acct-tree-toggle" onClick={onToggle}
                aria-expanded={!!open}
                aria-label={open ? `Collapse ${label}` : `Expand ${label}`}>
          {open ? <Minus size={11} /> : <Plus size={11} />}
        </button>
      ) : <span className="acct-tree-toggle" />}
      {has ? (
        <Checkbox checked={!!checked} disabled={!!disabled}
                  className="size-3.5"
                  onCheckedChange={() => onChange()} />
      ) : <span style={{ width: 14 }} />}
      <span className="acct-tree-label"
            onClick={() => (folder ? onToggle?.() : has && !disabled && onChange())}>
        {label}
      </span>
    </div>
  );
}

/** The TIER, and the cap that only means anything on it.
 *
 *  Master and developer only (`can_set_tier`). An admin who could promote
 *  another admin has made a tier with no ranking in it: either of them could
 *  then delete the other.
 */
function TierControls({ active, canSet, patch, busy }) {
  if (!canSet || active.builtin || active.developer) return null;
  const isAdmin = !!active.admin;
  return (
    <div style={{ marginTop: 14 }}>
      <div className="text-xs font-semibold" style={{ marginBottom: 2 }}>
        Account Type
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={isAdmin} disabled={busy}
               onChange={(e) => patch(active.username,
                                      { admin: e.target.checked })} />
        <span className="text-sm">Administrator - may manage their own users</span>
      </label>
      {isAdmin && (
        <label style={{ display: "flex", alignItems: "center", gap: 8,
                        marginTop: 8 }}>
          <span className="text-sm">Systems they may hold</span>
          <input className="input" type="number" min="0" style={{ width: 90 }}
                 defaultValue={active.max_systems || 0} disabled={busy}
                 onBlur={(e) => patch(active.username,
                                      { max_systems: Number(e.target.value) || 0 })} />
          <span className="acct-sub">0 = no limit</span>
        </label>
      )}
    </div>
  );
}

function SystemChip({ label, on, dim, onClick, title, disabled }) {
  return (
    // `title` carries the permanent slug: the label shows the name the system
    // goes by now, and the slug is what the allowlist is actually stored
    // under, so an admin debugging a grant can still see it.
    <button type="button" onClick={onClick} title={title} disabled={!!disabled}
            className={"rounded-full border px-2.5 py-0.5 text-xs transition-colors " +
              (on ? "border-primary bg-primary/15 text-primary" : "text-muted-foreground hover:border-primary/50") +
              (dim ? " opacity-50" : "") +
              (disabled ? " cursor-not-allowed opacity-60" : "")}>
      {label}
    </button>
  );
}
