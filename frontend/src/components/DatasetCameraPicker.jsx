// The Dataset page's camera picker: nothing at all with one camera (there is
// nothing to choose), a labelled dropdown of the system's cameras by name with
// more. A switched-off camera is still listed, marked "(off)", so the operator
// sees why it cannot capture rather than wondering where it went.
import { showPicker } from "../lib/datasetCamera.js";

export default function DatasetCameraPicker({ cameras, value, onChange, disabled }) {
  if (!showPicker(cameras)) return null;
  return (
    <label className="muted small" style={{ display: "grid", gap: 4, marginBottom: 10 }}>
      <span>Camera</span>
      <select className="input" value={value || ""} disabled={disabled}
              data-testid="dataset-camera"
              onChange={(e) => onChange(e.target.value)}>
        {cameras.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}{c.enabled === false ? " (off)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
