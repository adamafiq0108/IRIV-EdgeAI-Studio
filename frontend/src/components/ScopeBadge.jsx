// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The active-scope pill shown beside a page title. Hidden when nothing is
// scoped; when a folder is selected in the sidebar it shows the folder name and
// an "Overall" button that clears the scope (back to all devices).
//
// Note: it renders a <b> + <button> (no <span>) on purpose -- `.page-title span`
// is the page subtitle style, and a <span> here would inherit it.
import { useApp } from "../state/app.jsx";

export default function ScopeBadge() {
  const { scope, setScope } = useApp();
  if (!scope || scope.kind !== "folder") return null;
  return (
    <>
      <b className="scope-badge-tag" title="Showing only this folder">&#128193; {scope.name}</b>
      <button className="scope-badge-clear" title="Show all devices"
              onClick={() => setScope({ kind: "all" })}>&#8634; Overall</button>
    </>
  );
}
