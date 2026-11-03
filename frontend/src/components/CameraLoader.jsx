// The wait for a picture, said with a picture.
//
// Every feed on the box used to say it in words -- "Connecting to the video
// feed…" -- which is a sentence to read before you are allowed to look at a
// camera, in a product whose whole job is looking at cameras. The words also
// went stale in every other language and got clipped in a small tile.
//
// So: a camera in a turning ring. The RING says "working on it" the way every
// spinner does; the CAMERA says which kind of working. The icon deliberately
// does not rotate -- a tumbling camera reads as broken, not as busy.
//
// The sentence is kept, for the people who cannot see the ring: it rides as
// the element's accessible label and as its tooltip, so a screen reader still
// says "connecting to the video feed" and a hover still explains the circle.
import { Camera } from "lucide-react";

/**
 * @param label  what this wait IS, for assistive tech and the tooltip.
 * @param size   the ring's diameter in px. The icon scales with it, so one
 *               number fits this to a full-page feed or a dashboard tile.
 * @param dim    paint a scrim behind it (over a live picture that is being
 *               replaced); off where the box is already empty.
 */
export default function CameraLoader({ label = "Connecting to the video feed…",
                                       size = 56, dim = false }) {
  return (
    <div className={`cam-loader${dim ? " is-dim" : ""}`}
         role="status" aria-live="polite" title={label}>
      <div className="cam-loader-ring" style={{ "--cam-loader-size": `${size}px` }}>
        <Camera size={Math.round(size * 0.42)} aria-hidden="true" />
      </div>
      {/* Present for a screen reader, absent for the eye: the ring already
          says this, and saying it twice is what the words were doing. */}
      <span className="sr-only">{label}</span>
    </div>
  );
}
