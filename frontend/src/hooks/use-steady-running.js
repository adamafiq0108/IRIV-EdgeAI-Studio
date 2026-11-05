// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// A "stopped" answer has to prove itself. The status endpoint reports
// running=false transiently -- an app restarting after a config change, a
// master respawning after a deploy -- and pages that believed it at once
// flickered to their stopped state and TORE THE LIVE STREAM DOWN while
// detection ran fine underneath.
//
//   running=true   believed immediately (nothing fakes a heartbeat)
//   running=false  believed after `falses` consecutive polls -- or at once
//                  when `immediate` says the operator pressed Stop themselves
//   null           not decided yet: the page should hold its skeleton
import { useEffect, useRef, useState } from "react";

export function useSteadyRunning(status, { immediate = false, falses = 2 } = {}) {
  const [steady, setSteady] = useState(null);
  const count = useRef(0);
  // Dep is the STATUS OBJECT: each poll makes a new one, so consecutive
  // false answers are counted even though the boolean never changes.
  useEffect(() => {
    if (!status) return;
    if (status.running) {
      count.current = 0;
      setSteady(true);
      return;
    }
    count.current += 1;
    if (immediate || count.current >= falses) setSteady(false);
  }, [status, immediate, falses]);
  return steady;
}
