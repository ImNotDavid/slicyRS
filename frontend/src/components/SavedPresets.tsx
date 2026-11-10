import { useCallback, useEffect, useState } from "react";
import { BookmarkPlus, Trash2 } from "lucide-react";
import { api, type Catalog, type SavedPreset, type User } from "../api";

type Value = number | string | boolean;
type Props = {
  catalog: Catalog; user: User;
  current: { preset: string; filament: string; settings: Record<string, Value> };
  onApply: (p: SavedPreset) => void;
};

const same = (a: Record<string, Value>, b: Record<string, Value>) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());

/** A person's saved settings, plus the presets an admin shared with everyone. */
export default function SavedPresets({ catalog, user, current, onApply }: Props) {
  const [list, setList] = useState<SavedPreset[]>([]);
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [err, setErr] = useState("");
  const load = useCallback(() => api.presets().then(setList).catch(() => undefined), []);
  useEffect(() => { load(); }, [load]);

  const sel = list.find((p) => p.id === selected);
  // the selection only holds while the panel still matches it
  const active = sel && sel.preset === current.preset && sel.filament === current.filament
    && same({ ...catalog.preset_defaults[sel.preset], ...sel.settings }, current.settings) ? sel : undefined;
  const canDelete = !!active && (active.scope === "user" || user.role === "admin");

  const save = async () => {
    setErr("");
    try {
      const p = await api.savePreset({ name, ...current, shared });
      await load(); setSelected(p.id); setSaving(false); setName(""); setShared(false);
    } catch (e) { setErr((e as Error).message); }
  };

  return (
    <div className="saved">
      <div className="saved-row">
        <select value={active?.id ?? ""} aria-label="Saved presets" onChange={(e) => {
          const p = list.find((x) => x.id === e.target.value);
          if (p) { setSelected(p.id); onApply(p); }
        }}>
          <option value="" disabled>{list.length ? "Saved presets…" : "No saved presets yet"}</option>
          {list.some((p) => p.scope === "shared") && (
            <optgroup label="Shared">{list.filter((p) => p.scope === "shared").map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>
          )}
          {list.some((p) => p.scope === "user") && (
            <optgroup label="Mine">{list.filter((p) => p.scope === "user").map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>
          )}
        </select>
        <button className="icon-btn" title="Save the current settings as a preset" aria-label="Save the current settings as a preset"
          onClick={() => { setSaving(!saving); setErr(""); }}><BookmarkPlus size={18} /></button>
        <button className="icon-btn" title="Delete this preset" aria-label="Delete this preset" disabled={!canDelete}
          onClick={async () => { if (active && confirm(`Delete the preset "${active.name}"?`)) { await api.deletePreset(active.id).catch((e) => setErr(e.message)); setSelected(""); load(); } }}><Trash2 size={18} /></button>
      </div>
      {saving && (
        <div className="saved-form">
          <input placeholder="Name for this preset" value={name} maxLength={60} aria-label="Preset name" onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && name.trim() && save()} />
          {user.role === "admin" && <label className="check"><input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} /> Share with everyone</label>}
          <div className="row-actions">
            <button className="btn primary" disabled={!name.trim()} onClick={save}>Save</button>
            <button className="btn" onClick={() => setSaving(false)}>Cancel</button>
          </div>
        </div>
      )}
      {err && <p className="form-error" role="alert">{err}</p>}
    </div>
  );
}
