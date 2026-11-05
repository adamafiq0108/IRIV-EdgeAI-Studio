// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// The camera Quality panel's single "Ask the camera" gate. The resolution and
// frame-rate controls used to sit there with a button each, offering common
// values the camera might refuse; now neither control (nor the size/rate in
// the header) shows until the camera has been asked once, so the operator is
// never choosing from a list that looks like the camera's own when it is not.
// Pure so the rule is testable without a browser.

// What the panel shows for the state it is in. `asked` = the button has been
// pressed (and finished, even if the probe failed -- the controls then carry
// the common-values fallback so nobody is left without one).
export function qualityView({ asked, hiRes, width, height }) {
  const a = !!asked;
  return {
    showControls: a,                 // the frame-rate select and its notes
    showResolution: a && !!hiRes,    // the size select needs the switch on too
    // The small text next to "Quality": the live size only once asked.
    headNote: !a ? "" : (hiRes ? `${width}×${height}` : "640×480 · AI working size"),
    // Customer copy (2026-10-11): the button's name says what it does.
    buttonLabel: a ? "Check Again" : "Check Camera Capabilities",
  };
}

// Run the two probes in the order the answer depends on: the size first, then
// the rates at the size that landed. `probeRes` resolves to the [w, h] it
// settled on (or null when it failed), `probeRates(size)` takes it. A failed
// size probe skips the rate probe (it would only fail the same way); the
// caller reveals the fallback lists either way. With the switch off the size is
// the AI's working size and is not probed -- asking must not resize a camera
// the operator left at 640x480.
export async function askBoth({ hiRes, probeRes, probeRates }) {
  let size = null;
  if (hiRes) {
    size = await probeRes();
    if (!size) return false;
  }
  await probeRates(size);
  return true;
}
