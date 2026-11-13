import { useState } from "react";
import { api } from "../api";

const ITEMS = [["bed_empty", "The build plate is empty"], ["plate_matches", "The plate type matches the printer's setting"],
  ["filament_matches", "The loaded filament matches the next job"]] as const;

/** The three ticks before a plate counts as clear (used on Fleet and, for a person's own finished print, on Jobs). */
export default function PlateCheck({ printerId, onDone }: { printerId: string; onDone: () => void }) {
  const [ticks, setTicks] = useState({ bed_empty: false, plate_matches: false, filament_matches: false });
  const [err, setErr] = useState("");
  const confirm = async () => {
    setErr("");
    try { await api.bedClear(printerId, ticks); onDone(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="bed-check">
      {ITEMS.map(([k, label]) => (
        <label className="check" key={k}><input type="checkbox" checked={ticks[k]} onChange={(e) => setTicks({ ...ticks, [k]: e.target.checked })} /> {label}</label>
      ))}
      {err && <p className="form-error" role="alert">{err}</p>}
      <button className="btn primary" disabled={!Object.values(ticks).every(Boolean)} onClick={confirm}>Confirm clear</button>
    </div>
  );
}
