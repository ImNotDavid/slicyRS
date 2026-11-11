import { useState } from "react";
import { Copy, LayoutGrid, Plus, Trash2 } from "lucide-react";
import { MAX_PARTS, type Part } from "../plate";

type Props = {
  parts: Part[];
  selectedUid: string | null;
  overlapping: [string, string][];
  busy: boolean;
  onSelect: (uid: string) => void;
  onRemove: (uid: string) => void;
  onAdd: () => void;
  onArrange: () => void;
  onCopies: (uid: string, count: number) => void;
};

/** The parts on the plate: pick one to work on it, add more files or copies, and let the slicer lay them all out. */
export default function PartsPanel({ parts, selectedUid, overlapping, busy, onSelect, onRemove, onAdd, onArrange, onCopies }: Props) {
  const [copies, setCopies] = useState(1);
  const sel = parts.find((p) => p.uid === selectedUid);
  const free = MAX_PARTS - parts.length;
  const name = (uid: string) => parts.find((p) => p.uid === uid)?.model.name ?? "a part";
  const clash = new Set(overlapping.flat());
  return (
    <div className="card parts" aria-label="Parts on the plate">
      <div className="parts-head">
        <strong>Parts</strong><span className="muted">{parts.length} of {MAX_PARTS}</span>
        <span className="grow" />
        <button className="mini" disabled={busy || free <= 0} onClick={onAdd} title={free > 0 ? "Add another file to this plate" : `A plate holds at most ${MAX_PARTS} parts`}><Plus size={14} /> Add part</button>
        <button className="mini" disabled={busy || parts.length < 2} onClick={onArrange} title="Let the slicer lay out every part (it may turn parts to pack them)"><LayoutGrid size={14} /> Arrange all</button>
      </div>
      <ul className="part-list">
        {parts.map((p, i) => (
          <li key={p.uid} className={(p.uid === selectedUid ? "on " : "") + (clash.has(p.uid) ? "clash" : "")}>
            <button className="part-row" aria-pressed={p.uid === selectedUid} onClick={() => onSelect(p.uid)}>
              <span className="n">{i + 1}</span><span className="name">{p.model.name}</span>
              <span className="muted dims">{p.model.dims.map((d) => Math.round(d * p.transform.scale)).join("×")}</span>
            </button>
            <button className="icon-btn" aria-label={`Remove ${p.model.name}`} title="Remove from the plate" disabled={busy || parts.length === 1} onClick={() => onRemove(p.uid)}><Trash2 size={15} /></button>
          </li>
        ))}
      </ul>
      {sel && free > 0 && (
        <div className="copies">
          <label>Copies of the selected part
            <input type="number" min={1} max={free} value={Math.min(copies, free)} aria-label="Number of copies to add"
              onChange={(e) => setCopies(Math.max(1, Math.min(free, Number(e.target.value) || 1)))} />
          </label>
          <button className="mini" disabled={busy} onClick={() => onCopies(sel.uid, Math.min(copies, free))}><Copy size={14} /> Add</button>
        </div>
      )}
      {overlapping.length > 0 && (
        <p className="form-error" role="alert">
          {overlapping.slice(0, 3).map(([a, b]) => `${name(a)} and ${name(b)}`).join("; ")} {overlapping.length === 1 ? "overlap" : "overlap (and more)"}. Move them apart or press Arrange all.
        </p>
      )}
    </div>
  );
}
