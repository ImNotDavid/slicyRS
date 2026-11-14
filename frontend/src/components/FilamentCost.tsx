import { useCallback, useEffect, useState } from "react";
import { Download } from "lucide-react";
import { api, type Catalog, type CostReport, type CostRow } from "../api";

const money = (v: number) => v.toFixed(2);
const kg = (g: number) => (g >= 1000 ? `${(g / 1000).toFixed(2)} kg` : `${Math.round(g)} g`);
const day = (t: number) => new Date(t * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });

/** Filament cost (3.4): grams times the spool's price per kg (else the admin's default per material), by group and by person. */
export default function FilamentCost({ catalog }: { catalog: Catalog | null }) {
  const [range, setRange] = useState<string>("30");          // a term id, or "30" / "90" days
  const [r, setR] = useState<CostReport | null>(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<Record<string, string> | null>(null);
  const q = range === "30" || range === "90" ? { start: Math.floor(Date.now() / 1000) - Number(range) * 86400 } : { period: range };
  const load = useCallback(() => api.costs(q).then(setR).catch((e) => setErr(e.message)), [range]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  const csv = `/api/admin/costs.csv?${new URLSearchParams(Object.entries(q).map(([k, v]) => [k, String(v)]))}`;
  const table = (rows: CostRow[], who: string) => (
    <table className="audit"><thead><tr><th>{who}</th><th>Prints</th><th>Filament</th><th>Cost</th></tr></thead>
      <tbody>{rows.map((x) => <tr key={x.key}><td>{x.name}</td><td>{x.prints}</td><td>{kg(x.grams)}</td>
        <td>{money(x.cost)}{x.unpriced_g > 0 && <span className="muted small-text"> + {kg(x.unpriced_g)} unpriced</span>}</td></tr>)}
        {!rows.length && <tr><td colSpan={4} className="muted">No prints ended in this period.</td></tr>}</tbody></table>
  );
  const save = async () => {
    setErr("");
    try { await api.setPrices(Object.fromEntries(Object.entries(editing ?? {}).map(([m, v]) => [m, v === "" ? null : Number(v)]))); setEditing(null); load(); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <section className="job-card cost-card" aria-label="Filament cost">
      <div className="card-head"><h3>Filament cost</h3>
        <div className="row-actions">
          <select value={range} onChange={(e) => setRange(e.target.value)} aria-label="Period">
            <option value="30">Last 30 days</option><option value="90">Last 90 days</option>
            {(r?.periods ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
          <a className="btn" href={csv} download><Download size={14} /> CSV, one line per print</a>
        </div></div>
      {err && <p className="form-error" role="alert">{err}</p>}
      {r && <>
        <p className="muted">{day(r.start)} to {day(r.end - 1)}: {r.prints} print{r.prints === 1 ? "" : "s"}, {kg(r.grams)}, cost <b>{money(r.cost)}</b>
          {r.unpriced_g > 0 && <> plus {kg(r.unpriced_g)} with no price (set a default below, or a price on the spool)</>}.</p>
        <div className="dash-grid">
          <div><h4>By group</h4>{table(r.by_group, "Group")}</div>
          <div><h4>By person</h4>{table(r.by_person, "Person")}</div>
        </div>
        <h4>Default price per kg</h4>
        <p className="muted small-text">Used when a print's spool is not known or has no price. Filament only; machine time is not charged. Failed prints count what was printed before they stopped.</p>
        {editing ? (
          <div className="price-grid">
            {Object.keys(catalog?.filaments ?? r.prices).map((m) => (
              <label key={m} className="plate-field">{m}<input type="number" min={0} step="0.5" value={editing[m] ?? ""} placeholder="none"
                onChange={(e) => setEditing({ ...editing, [m]: e.target.value })} /></label>))}
            <div className="row-actions"><button className="btn primary" onClick={save}>Save prices</button><button className="btn" onClick={() => setEditing(null)}>Cancel</button></div>
          </div>
        ) : (
          <p>{Object.entries(r.prices).map(([m, v]) => `${m} ${money(v)}`).join(" · ") || <span className="muted">No defaults set.</span>}{" "}
            <button className="mini" onClick={() => setEditing(Object.fromEntries(Object.entries(r.prices).map(([m, v]) => [m, String(v)])))}>Edit</button></p>
        )}
      </>}
    </section>
  );
}
