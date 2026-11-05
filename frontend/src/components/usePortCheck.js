// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The port field's live answer, shared by the create wizard's "Custom port"
// and Edit system (Tok Aba, 2026-10-06).
//
// One server question, GET /api/hub/ports/check: what Automatic (next free)
// would hand out now, and -- for a typed port -- whether this box can give it,
// in the same plain words the save would refuse with
// (provision.master_port_refusal). Asked after a short pause in typing, so a
// half-typed "51" is never checked and the Save button never races the
// answer: `verdict` is null while a check is pending.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";

export function usePortCheck({ port, slug = "", enabled = true, delay = 300 }) {
  const [suggested, setSuggested] = useState(null);
  const [verdict, setVerdict] = useState(null);
  const seq = useRef(0);
  const typed = String(port ?? "").trim();

  useEffect(() => {
    if (!enabled) return undefined;
    const mine = ++seq.current;
    setVerdict(null);
    const q = new URLSearchParams();
    if (typed) q.set("port", typed);
    if (slug) q.set("slug", slug);
    const ask = () => api.get(`/api/hub/ports/check?${q.toString()}`)
      .then((d) => {
        if (mine !== seq.current) return;
        if (d?.suggested) setSuggested(d.suggested);
        if (typed) setVerdict({ usable: !!d?.usable, detail: d?.detail || "" });
      })
      .catch((e) => {
        if (mine !== seq.current || !typed) return;
        setVerdict({ usable: false, detail: e?.message || "the port could not be checked" });
      });
    const t = setTimeout(ask, typed ? delay : 0);
    return () => clearTimeout(t);
  }, [typed, slug, enabled, delay]);

  return { suggested, verdict };
}
