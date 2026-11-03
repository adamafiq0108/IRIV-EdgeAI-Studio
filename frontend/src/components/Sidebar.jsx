// File-explorer sidebar (v2.0): folder tree + devices + Unassigned. Ported from
// _sidebar.html + sidebar.js. Admins get folder/device management; operators a
// read-only tree. Mutations hit the folder/device APIs, then reload the shell.
//
// Moving a device uses a real folder picker (not a "type the numeric id" prompt),
// devices can be renamed inline, and folder rename uses PATCH (POST creates).
//
// Naming and deleting go through real dialogs rather than prompt()/confirm():
// those cannot be styled, cannot mark a delete as destructive, and are
// suppressed outright by some browsers - which turns "New folder" into a button
// that silently does nothing. Failures report as toasts, not alert().
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { useApp } from "../state/app.jsx";
import { ConfirmDialog, FormDialog } from "./ui/dialogs.jsx";
import { useToast } from "./ui/Toast.jsx";

// Flatten the folder tree to {id, name} with indentation, for the move picker.
function flattenFolders(tree, depth = 0, out = []) {
  for (const n of tree || []) {
    out.push({ id: n.id, name: "  ".repeat(depth) + n.name });
    flattenFolders(n.children, depth + 1, out);
  }
  return out;
}

// --- Optimistic sidebar edits ---------------------------------------------
// Moving/renaming a device rebuilds the tree locally so the UI updates the
// instant you click, instead of waiting for the POST + bootstrap round-trip.
// The API call reconciles in the background; a failure re-syncs from the server.
function extractDevice(sidebar, host) {
  let device = null;
  const walk = (nodes) =>
    (nodes || []).map((n) => ({
      ...n,
      children: walk(n.children),
      devices: (n.devices || []).filter((d) => {
        if (d.host_serial === host) { device = d; return false; }
        return true;
      }),
    }));
  const tree = walk(sidebar.tree);
  const unassigned = (sidebar.unassigned || []).filter((d) => {
    if (d.host_serial === host) { device = device || d; return false; }
    return true;
  });
  return { tree, unassigned, device };
}

function insertDevice(tree, unassigned, device, folderId) {
  if (folderId === null || folderId === undefined) {
    return { tree, unassigned: [...unassigned, device] };
  }
  const walk = (nodes) =>
    (nodes || []).map((n) =>
      n.id === folderId
        ? { ...n, children: walk(n.children), devices: [...(n.devices || []), device] }
        : { ...n, children: walk(n.children) });
  return { tree: walk(tree), unassigned };
}

function moveDeviceInSidebar(sidebar, host, folderId) {
  const { tree, unassigned, device } = extractDevice(sidebar, host);
  if (!device) return sidebar;
  const next = insertDevice(tree, unassigned, device, folderId);
  return { ...sidebar, tree: next.tree, unassigned: next.unassigned };
}

function renameDeviceInSidebar(sidebar, host, name) {
  const patch = (d) => (d.host_serial === host ? { ...d, display_name: name } : d);
  const walk = (nodes) =>
    (nodes || []).map((n) => ({ ...n, children: walk(n.children), devices: (n.devices || []).map(patch) }));
  return { ...sidebar, tree: walk(sidebar.tree), unassigned: (sidebar.unassigned || []).map(patch) };
}

function DeviceRow({ d, onOpenMenu }) {
  return (
    <li className="fx-device" data-host={d.host_serial}>
      <span className={"fx-dot " + (d.online ? "on" : "off")} />
      <button className="fx-dev-name" onClick={(e) => onOpenMenu(d, e)} title="Open device actions">
        {d.display_name}
      </button>
    </li>
  );
}

function FolderNode({ node, isAdmin, actions, activeFolderId }) {
  const [open, setOpen] = useState(true);
  const active = activeFolderId === node.id;
  return (
    <li className="fx-folder" data-folder-id={node.id}>
      <div className={"fx-row" + (active ? " scoped" : "")}>
        <button className="fx-toggle" type="button" aria-label="expand"
                onClick={() => setOpen((v) => !v)}>{open ? "▾" : "▸"}</button>
        <button className="fx-name fx-name-btn" title="Show only this folder"
                onClick={() => actions.scopeFolder(node)}>{node.name}</button>
        {isAdmin && (
          <span className="fx-actions">
            <button className="fx-act" title="New subfolder" onClick={() => actions.add(node.id)}>+</button>
            <button className="fx-act" title="Rename" onClick={() => actions.rename(node.id, node.name)}>&#9998;</button>
            <button className="fx-act" title="Delete" onClick={() => actions.remove(node.id, node.name)}>&times;</button>
          </span>
        )}
      </div>
      {open && (
        <ul className="fx-children">
          {(node.children || []).map((c) => (
            <FolderNode key={c.id} node={c} isAdmin={isAdmin} actions={actions} activeFolderId={activeFolderId} />
          ))}
          {(node.devices || []).map((d) => (
            <DeviceRow key={d.host_serial} d={d} onOpenMenu={actions.openMenu} />
          ))}
        </ul>
      )}
    </li>
  );
}

// `embedded`: rendered inside MasterShell's shadcn sidebar, which owns
// geometry and collapsing - skip the standalone rail's collapse affordances.
export default function Sidebar({ embedded = false }) {
  const { sidebar, user, reloadShell, patchSidebar, scope, setScope } = useApp();
  const toast = useToast();
  const isAdmin = user.is_admin;
  const navigate = useNavigate();
  const [move, setMove] = useState(null);       // {host, name} being moved
  const [moveTarget, setMoveTarget] = useState(""); // "" = Unassigned
  const [menu, setMenu] = useState(null);       // {d, x, y} device action menu
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("fx-collapsed") === "1");
  // One slot for every naming/deleting dialog. kind decides which renders:
  // "add" | "newRoot" | "renameFolder" | "deleteFolder" | "renameDevice".
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);

  function toggleCollapsed() {
    setCollapsed((v) => { const nv = !v; localStorage.setItem("fx-collapsed", nv ? "1" : "0"); return nv; });
  }

  // Close the device menu on Escape.
  useEffect(() => {
    if (!menu) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setMenu(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);

  if (!sidebar) return null;

  if (collapsed && !embedded) {
    return (
      <aside className="fx-sidebar collapsed" id="fx-sidebar">
        <button className="fx-collapse-btn" title="Show devices" onClick={toggleCollapsed}>&#187;</button>
      </aside>
    );
  }

  const folderOpts = flattenFolders(sidebar.tree || []);
  const activeFolderId = scope?.kind === "folder" ? scope.id : null;
  function go(path) { setMenu(null); navigate(path); }

  // Structural folder ops: rare, so a full re-sync afterwards is fine. Returns
  // whether it succeeded so a dialog only closes on success - a failed rename
  // that closed the dialog would lose what the operator typed.
  async function run(fn, failure = "Action failed") {
    setBusy(true);
    try {
      await fn();
      await reloadShell();
      return true;
    } catch (e) {
      toast.error(failure, e.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  // Optimistic device ops: patch the local tree first, fire the API in the
  // background, and only re-sync from the server if the call fails.
  function optimistic(patch, call, failure = "Action failed") {
    patchSidebar(patch);
    Promise.resolve().then(call).catch((e) => {
      toast.error(failure, e.message);
      reloadShell();
    });
  }

  const actions = {
    add: (parentId) => setDialog({ kind: "add", parentId }),
    rename: (id, current) => setDialog({ kind: "renameFolder", id, current }),
    remove: (id, name) => setDialog({ kind: "deleteFolder", id, name }),
    move: (d) => { setMoveTarget(""); setMove({ host: d.host_serial, name: d.display_name }); },
    renameDevice: (d) => setDialog({ kind: "renameDevice", d }),
    newRoot: () => setDialog({ kind: "newRoot" }),
    // Clicking a folder scopes every page to just its devices. Clicking the
    // already-active folder clears the scope (back to Overall).
    scopeFolder: (node) => {
      setScope((cur) => (cur?.kind === "folder" && cur.id === node.id
        ? { kind: "all" }
        : { kind: "folder", id: node.id, name: node.name }));
    },
    // Clicking a device opens a small action menu anchored to the row. Fixed
    // positioning keeps it out of the scrollable rail's clip region.
    openMenu: (d, e) => {
      const r = e.currentTarget.getBoundingClientRect();
      const MENU_W = 200, MENU_H = 250;
      let x = r.right + 6;
      if (x + MENU_W > window.innerWidth) x = Math.max(8, r.left - MENU_W - 6);
      let y = Math.min(r.top, window.innerHeight - MENU_H - 8);
      if (y < 8) y = 8;
      setMenu({ d, x, y });
    },
  };

  function confirmMove(rawVal) {
    const host = move.host;
    const folderId = rawVal === "" ? null : Number(rawVal);
    setMove(null);
    optimistic(
      (sb) => moveDeviceInSidebar(sb, host, folderId),
      () => api.post(`/api/devices/${host}/folder`, { folder_id: folderId }),
      "Could not move the device");
  }

  // What the one FormDialog shows, derived from which action opened it. Keeping
  // it as data means adding a fifth naming action is one case, not a fifth
  // dialog rendered into the tree.
  const formSpec = (() => {
    if (!dialog) return null;
    switch (dialog.kind) {
      case "add":
        return { title: "New subfolder", submitLabel: "Create",
                 description: "Created inside the folder you clicked.",
                 fields: [{ name: "name", label: "Folder name", required: true, placeholder: "e.g. Line 2" }] };
      case "newRoot":
        return { title: "New folder", submitLabel: "Create",
                 description: "A new top-level folder.",
                 fields: [{ name: "name", label: "Folder name", required: true, placeholder: "e.g. Building A" }] };
      case "renameFolder":
        return { title: "Rename folder", submitLabel: "Rename",
                 fields: [{ name: "name", label: "Folder name", required: true, defaultValue: dialog.current }] };
      case "renameDevice":
        return { title: "Rename device", submitLabel: "Rename",
                 description: "This is the label operators see. The host serial never changes.",
                 fields: [{ name: "display_name", label: "Display name", required: true,
                            defaultValue: dialog.d.display_name || "" }] };
      default:
        return null;      // deleteFolder uses ConfirmDialog, not this one
    }
  })();

  async function submitDialog(values) {
    const d = dialog;
    if (!d) return;
    if (d.kind === "add" || d.kind === "newRoot") {
      const parent_id = d.kind === "add" ? d.parentId : null;
      if (await run(() => api.post("/api/folders", { name: values.name, parent_id }),
                    "Could not create the folder")) setDialog(null);
      return;
    }
    if (d.kind === "renameFolder") {
      if (values.name === d.current) { setDialog(null); return; }
      if (await run(() => api.patch(`/api/folders/${d.id}`, { name: values.name }),
                    "Could not rename the folder")) setDialog(null);
      return;
    }
    if (d.kind === "renameDevice") {
      const name = values.display_name;
      setDialog(null);
      if (name === d.d.display_name) return;
      optimistic(
        (sb) => renameDeviceInSidebar(sb, d.d.host_serial, name),
        () => api.patch(`/api/devices/${d.d.host_serial}`, { display_name: name }),
        "Could not rename the device");
    }
  }

  async function confirmDeleteFolder() {
    if (await run(() => api.del(`/api/folders/${dialog.id}`),
                  "Could not delete the folder")) setDialog(null);
  }

  return (
    <aside className={"fx-sidebar" + (embedded ? " embedded" : "")} id="fx-sidebar">
      <div className="fx-head">
        <span>Devices</span>
        <span className="fx-head-actions">
          {isAdmin && <button className="fx-act" title="New folder" onClick={actions.newRoot}>+ Folder</button>}
          {!embedded && (
            <button className="fx-min-btn" title="Minimize sidebar" onClick={toggleCollapsed}>&#171;</button>
          )}
        </span>
      </div>
      <ul className="fx-tree">
        {(sidebar.tree || []).map((node) => (
          <FolderNode key={node.id} node={node} isAdmin={isAdmin} actions={actions}
                      activeFolderId={activeFolderId} />
        ))}
        <li className="fx-folder fx-unassigned" data-folder-id="">
          <div className={"fx-row" + (activeFolderId === "unassigned" ? " scoped" : "")}>
            <button className="fx-toggle" type="button">&#9662;</button>
            <button className="fx-name fx-name-btn" title="Show only unassigned devices"
                    onClick={() => actions.scopeFolder({ id: "unassigned", name: "Unassigned" })}>Unassigned</button>
          </div>
          <ul className="fx-children">
            {(sidebar.unassigned || []).map((d) => (
              <DeviceRow key={d.host_serial} d={d} onOpenMenu={actions.openMenu} />
            ))}
          </ul>
        </li>
      </ul>

      {/* Move a device. The selection is React state rather than a
          document.getElementById() read of a portalled node - that lookup broke
          silently if the node was ever rendered twice or renamed. */}
      <ConfirmDialog
        open={!!move}
        onOpenChange={(o) => !o && setMove(null)}
        title={move ? `Move “${move.name}”` : ""}
        description="Choose the folder this device belongs to."
        confirmLabel="Move"
        onConfirm={() => confirmMove(moveTarget)}
      >
        <label className="grid gap-1.5">
          <span className="text-sm font-medium">Folder</span>
          <select
            className="h-8 w-full rounded-md border bg-background px-2 text-sm"
            value={moveTarget}
            onChange={(e) => setMoveTarget(e.target.value)}
          >
            <option value="">Unassigned</option>
            {folderOpts.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
      </ConfirmDialog>

      {/* Naming dialogs: new folder / new subfolder / rename folder / rename device. */}
      <FormDialog
        open={!!formSpec}
        onOpenChange={(o) => !o && setDialog(null)}
        title={formSpec?.title || ""}
        description={formSpec?.description}
        fields={formSpec?.fields || []}
        submitLabel={formSpec?.submitLabel || "Save"}
        busy={busy}
        onSubmit={submitDialog}
      />

      <ConfirmDialog
        open={dialog?.kind === "deleteFolder"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={dialog?.kind === "deleteFolder" ? `Delete “${dialog.name}”?` : ""}
        description="The folder and its subfolders are removed. Devices inside are not deleted - they move to Unassigned."
        confirmLabel="Delete folder"
        destructive
        busy={busy}
        onConfirm={confirmDeleteFolder}
      />

      {menu && createPortal((() => {
        const d = menu.d;
        const host = d.host_serial;
        const caps = d.capabilities || [];
        return (
          <div className="fx-menu-backdrop" onClick={() => setMenu(null)}>
            <div className="fx-menu" style={{ top: menu.y, left: menu.x }}
                 onClick={(e) => e.stopPropagation()}>
              <div className="fx-menu-head" title={host}>{d.display_name}</div>
              <button className="fx-menu-item" onClick={() => go(`/manage/${host}`)}>
                <span>&#9881;</span> Manage</button>
              {caps.includes("footage") && (
                <button className="fx-menu-item" onClick={() => go(`/footage?device=${host}`)}>
                  <span>&#127902;</span> Footage</button>)}
              {caps.includes("stream") && (
                <button className="fx-menu-item" onClick={() => go(`/stream?device=${host}`)}>
                  <span>&#9654;</span> Video Stream</button>)}
              <button className="fx-menu-item" onClick={() => go(`/analytics?device=${host}`)}>
                <span>&#128202;</span> Analytics</button>
              {isAdmin && <div className="fx-menu-sep" />}
              {isAdmin && (
                <button className="fx-menu-item" onClick={() => { setMenu(null); actions.move(d); }}>
                  <span>&#8646;</span> Move to folder&hellip;</button>)}
              {isAdmin && (
                <button className="fx-menu-item" onClick={() => { setMenu(null); actions.renameDevice(d); }}>
                  <span>&#9998;</span> Rename&hellip;</button>)}
            </div>
          </div>
        );
      })(), document.body)}
    </aside>
  );
}
