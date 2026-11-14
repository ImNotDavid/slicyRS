import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, type Catalog, type Printer, type Spool } from "../api";

// owner request: colour and brand are chosen from lists ("Other" allows anything else)
const BRANDS = ["Bambu Lab", "Polymaker", "Prusament", "eSun", "Sunlu", "Elegoo", "Overture", "Jayo", "Generic"];
const COLOURS: [string, string][] = [["Black", "#111111"], ["White", "#f4f4f4"], ["Grey", "#8a8a8a"], ["Silver", "#c0c0c0"], ["Red", "#d0242b"], ["Orange", "#f07a1a"],
  ["Yellow", "#f5d000"], ["Green", "#2f9e44"], ["Blue", "#1f5fd6"], ["Light blue", "#58a6ff"], ["Purple", "#7b3fbf"], ["Pink", "#f06aa6"], ["Brown", "#7a4b2a"],
  ["Beige", "#d8c3a0"], ["Gold", "#c9a227"], ["Transparent", "#e8eef2"]];
const OTHER = "__other";

const EMPTY = { material: "PLA", colour_name: "", colour_hex: "#888888", brand: "", start_g: 1000, remaining_g: null as number | null, price: null as number | null, notes: "", printer_id: "" };

/** Filament inventory (2.10): spools with remaining weight and cost; operators and admins. */
export default function FilamentPage({ catalog, printers }: { catalog: Catalog | null; printers: Printer[] }) {
  const [data, setData] = useState<{ spools: Spool[]; totals: Record<string, { spools: number; remaining_g: number; value: number }> } | null>(null);
  const [form, setForm] = useState<typeof EMPTY | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const load = () => api.spools().then(setData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const save = async () => {
    if (!form) return;
    setErr("");
    const body = { ...form, colour_name: form.colour_name.trim(), brand: form.brand.trim(), printer_id: form.printer_id || null };
    try { if (editing) await api.updateSpool(editing, body); else await api.addSpool(body); setForm(null); setEditing(null); load(); }
    catch (e) { setErr((e as Error).message); }
  };
  const materials = Object.keys(catalog?.filaments ?? { PLA: 1 });
  return (
    <div className="page-body filament-page">
      {data && Object.keys(data.totals).length > 0 && (
        <section className="stats-strip" aria-label="Stock by material">
          {Object.entries(data.totals).map(([m, t]) => (
            <div className="stat" key={m}><span className="lbl">{m}</span><b>{t.remaining_g >= 1000 ? `${(t.remaining_g / 1000).toFixed(1)} kg` : `${Math.round(t.remaining_g)} g`}</b>
              <span>{t.spools} spool{t.spools === 1 ? "" : "s"}{t.value ? ` · worth ${t.value.toFixed(2)}` : ""}</span></div>
          ))}
        </section>
      )}
      <div className="queue-top"><p className="muted">Spools marked as loaded on a printer lose the grams each print uses, so the weight stays roughly right. Correct it when you weigh a spool.</p>
        {!form && <button className="btn primary" onClick={() => { setForm({ ...EMPTY }); setEditing(null); }}><Plus size={14} /> Add spool</button>}</div>
      {form && (
        <div className="spool-form card-like">
          <label>Material<select value={form.material} onChange={(e) => setForm({ ...form, material: e.target.value })}>{materials.map((m) => <option key={m}>{m}</option>)}</select></label>
          <label>Colour
            <select value={COLOURS.some(([n]) => n === form.colour_name) ? form.colour_name : form.colour_name ? OTHER : ""}
              onChange={(e) => { const c = COLOURS.find(([n]) => n === e.target.value); setForm(c ? { ...form, colour_name: c[0], colour_hex: c[1] } : { ...form, colour_name: e.target.value === OTHER ? " " : "" }); }}>
              <option value="">Choose a colour</option>{COLOURS.map(([n]) => <option key={n} value={n}>{n}</option>)}<option value={OTHER}>Other…</option>
            </select>
            {form.colour_name && !COLOURS.some(([n]) => n === form.colour_name) && (
              <span className="row custom"><input type="color" value={form.colour_hex} onChange={(e) => setForm({ ...form, colour_hex: e.target.value })} aria-label="Colour" />
                <input value={form.colour_name.trim()} maxLength={40} placeholder="Colour name" onChange={(e) => setForm({ ...form, colour_name: e.target.value || " " })} aria-label="Colour name" /></span>
            )}
          </label>
          <label>Brand
            <select value={BRANDS.includes(form.brand) ? form.brand : form.brand ? OTHER : ""} onChange={(e) => setForm({ ...form, brand: e.target.value === OTHER ? " " : e.target.value })}>
              <option value="">Choose a brand</option>{BRANDS.map((b) => <option key={b}>{b}</option>)}<option value={OTHER}>Other…</option>
            </select>
            {form.brand && !BRANDS.includes(form.brand) && <input className="custom" value={form.brand.trim()} maxLength={60} placeholder="Brand" onChange={(e) => setForm({ ...form, brand: e.target.value || " " })} aria-label="Brand" />}
          </label>
          <label>Spool weight (g)<input type="number" min={1} value={form.start_g} onChange={(e) => setForm({ ...form, start_g: Number(e.target.value) })} /></label>
          <label>Remaining (g)<input type="number" min={0} value={form.remaining_g ?? ""} placeholder="full" onChange={(e) => setForm({ ...form, remaining_g: e.target.value === "" ? null : Number(e.target.value) })} /></label>
          <label>Price<input type="number" min={0} step="0.01" value={form.price ?? ""} placeholder="optional" onChange={(e) => setForm({ ...form, price: e.target.value === "" ? null : Number(e.target.value) })} /></label>
          <label>Loaded on<select value={form.printer_id} onChange={(e) => setForm({ ...form, printer_id: e.target.value })}><option value="">Not loaded</option>{printers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label className="wide">Notes<input value={form.notes} maxLength={200} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
          <div className="live-controls wide"><button className="btn primary" onClick={save}>{editing ? "Save" : "Add spool"}</button><button className="btn" onClick={() => { setForm(null); setEditing(null); }}>Cancel</button></div>
        </div>
      )}
      {err && <p className="form-error" role="alert">{err}</p>}
      <section className="jgrid" aria-label="Spools">
        {(data?.spools ?? []).map((s) => (
          <article key={s.id} className={"jc spool-card" + (s.empty ? " empty" : "")}>
            <div className="spool-top"><span className="swatch" style={{ background: s.colour_hex ?? "#888" }} /><div><b>{s.material}</b><div className="muted small-text">{[s.colour_name, s.brand].filter(Boolean).join(" · ") || "—"}</div></div></div>
            <div className="body">
              <div className="live-bar" role="meter" aria-label="Remaining filament" aria-valuenow={s.percent ?? 0} aria-valuemin={0} aria-valuemax={100}><b style={{ width: `${s.percent ?? 0}%` }} /></div>
              <div className="meta"><span>{Math.round(s.remaining_g)} of {Math.round(s.start_g)} g</span>{s.cost_per_kg != null && <span>{s.cost_per_kg.toFixed(2)} per kg</span>}</div>
              <div className="meta">{s.printer_name ? <span className="tg tg-pin">On {s.printer_name}</span> : <span>Not loaded</span>}{s.empty && <span className="tg tg-y">Empty</span>}</div>
              {s.notes && <div className="muted small-text">{s.notes}</div>}
              <div className="part-tools">
                <button className="mini" onClick={() => { setEditing(s.id); setForm({ material: s.material, colour_name: s.colour_name ?? "", colour_hex: s.colour_hex ?? "#888888", brand: s.brand ?? "", start_g: s.start_g, remaining_g: s.remaining_g, price: s.price, notes: s.notes ?? "", printer_id: s.printer_id ?? "" }); }}>Edit</button>
                <button className="mini" aria-label="Delete spool" onClick={() => confirm("Delete this spool from the inventory?") && api.deleteSpool(s.id).then(load)}><Trash2 size={12} /></button>
              </div>
            </div>
          </article>
        ))}
      </section>
      {data && !data.spools.length && !form && <p className="muted">No spools yet.</p>}
    </div>
  );
}
