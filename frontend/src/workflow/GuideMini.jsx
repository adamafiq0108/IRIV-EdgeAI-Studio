// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// A guide folded away: a chip at the bottom right that says which guide it is and
// where it stands, and brings the guide back when pressed.
import { createPortal } from "react-dom";
import { BookOpen } from "lucide-react";

export default function GuideMini({ title, sub, onOpen }) {
  return createPortal(
    <button type="button" className="sw-mini" data-guide-mini onClick={onOpen}
            title="Open the guide again">
      <BookOpen size={22} aria-hidden="true" />
      <span>
        <strong>{title}</strong>
        {sub && <small>{sub}</small>}
      </span>
    </button>,
    document.body);
}
