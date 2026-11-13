import { Fragment, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Camera, Download, Repeat } from "lucide-react";
import { api, gcodeUrl, type Catalog, type JobPage as Page } from "../api";
import Viewport from "./Viewport";
import PlateCheck from "./PlateCheck";
import { fmtTime } from "./JobsModal";

const when = (t: number) => new Date(t * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const show = (v: unknown, unit?: string) => (typeof v === "boolean" ? (v ? "on" : "off") : `${v}${unit ? ` ${unit}` : ""}`);
const CANCELLABLE = ["submitted", "previewing", "previewed", "pooled", "assigned", "slicing", "ready"];

/** One job, everything about it (2.9). Owners can act on it; operators and admins can read it. */
export default function JobPage({ id, catalog, onBack, onReuse }: {
  id: string; catalog: Catalog | null; onBack: () => void; onReuse: (p: Page) => void;
}) {
  const [p, setP] = useState<Page | null>(null);
  const [err, setErr] = useState("");
  const [gcode, setGcode] = useState<string | null>(null);
  const [layers, setLayers] = useState(0);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [allSettings, setAllSettings] = useState(false);
  const [clearing, setClearing] = useState(false);
  const load = () => api.jobPage(id).then(setP).catch((e) => setErr(e.message));
  useEffect(() => { setP(null); setGcode(null); load(); }, [id]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (p?.gcode) fetch(gcodeUrl(id)).then((r) => (r.ok ? r.text() : null)).then(setGcode).catch(() => setGcode(null)); }, [p?.gcode, id]);

  const bed = useMemo<[number, number, number]>(() => (p?.printer && catalog?.printer_models[p.printer.model]?.bed) || [256, 256, 256], [p, catalog]);
  const fields = useMemo(() => new Map((catalog?.settings_schema ?? []).flatMap((t) => t.groups.flatMap((g) => g.fields.map((f) => [f.key, { ...f, tab: t.tab, group: g.name }] as const)))), [catalog]);

  if (err) return <div className="page-body"><button className="btn" onClick={onBack}><ArrowLeft size={14} /> Back</button><p className="form-error">{err}</p></div>;
  if (!p) return <p className="muted">Loading…</p>;
  const j = p.job;
  const defaults = catalog?.preset_defaults[p.preset] ?? {};
  const full = { ...defaults, ...p.settings };
  const status = j.result ? (j.result.outcome === "finished" ? "printed" : j.result.outcome === "failed" ? "failed" : "result unknown") : (j.phase ?? j.state);

  return (
    <div className="page-body job-page">
      <div className="job-head">
        <button className="btn" onClick={onBack}><ArrowLeft size={14} /> Back</button>
        <h2>{j.name}</h2>
        <span className={"pill " + (j.result?.outcome === "failed" ? "failed" : j.result ? "done" : j.state)}>{status}</span>
        {!p.mine && p.owner && <span className="muted">{p.owner}</span>}
      </div>

      <div className="job-actions">
        {p.mine && <button className="btn primary" onClick={() => onReuse(p)}><Repeat size={14} /> Same settings again</button>}
        {p.gcode && p.can_manage && <a className="btn" href={gcodeUrl(id)}><Download size={14} /> G-code</a>}
        {p.can_manage && j.phase && CANCELLABLE.includes(j.phase) && <button className="btn danger" onClick={() => api.cancel(id).then(load)}>Cancel job</button>}
        {p.plate_to_clear && p.printer && <button className="btn" onClick={() => setClearing(!clearing)}>Confirm plate is clear</button>}
      </div>
      {clearing && p.printer && <PlateCheck printerId={p.printer.id} onDone={() => { setClearing(false); load(); }} />}

      <div className="job-grid">
        <section className="card job-card" aria-label="Summary">
          <h3>Summary</h3>
          <dl className="review-facts">
            <dt>Printer</dt><dd>{p.printer ? `${p.printer.name} (${p.printer.model})` : "not chosen yet"}</dd>
            <dt>Build plate</dt><dd>{p.printer?.bed_type ?? "—"}</dd>
            <dt>Material</dt><dd>{p.filament}, {p.nozzle_mm} mm nozzle</dd>
            <dt>Goal</dt><dd>{p.goal?.name ?? "Custom settings"} · {p.preset}</dd>
            <dt>Time</dt><dd>{p.actual_s != null ? `${fmtTime(p.actual_s)} (estimate ${fmtTime(p.estimate_s ?? undefined)})` : `estimate ${fmtTime(p.estimate_s ?? undefined)}`}</dd>
            <dt>Filament</dt><dd>{p.filament_g != null ? `${p.filament_g} g` : "—"}</dd>
            <dt>Layers</dt><dd>{p.layers ?? "—"}</dd>
            {j.pickup_by && <><dt>Pickup by</dt><dd>{when(j.pickup_by)}</dd></>}
          </dl>
          {j.result && <p className={"result-note" + (j.result.outcome === "failed" ? " bad" : "")}>{j.result.summary}</p>}
          {j.error && <p className="result-note bad">{j.error}</p>}
        </section>

        <section className="card job-card" aria-label="Parts">
          <h3>{p.parts.length > 1 ? `Plate of ${p.parts.length} parts` : "Part"}</h3>
          <ul className="job-parts">
            {p.parts.map((x, i) => <li key={i}><b>{x.name}</b>{!x.available && p.mine && <span className="muted"> · file removed, upload it again to reuse</span>}</li>)}
          </ul>
          {p.shared_with.length > 0 && <>
            <h4>Shared plate</h4>
            <ul className="job-parts">{p.shared_with.map((o, i) => <li key={i}>{o.name}{o.owner ? <span className="muted"> · {o.owner}</span> : null}</li>)}</ul>
          </>}
        </section>

        <section className="card job-card wide" aria-label="Sliced preview">
          <h3>Sliced preview</h3>
          {gcode ? (
            <div className="job-preview">
              <Viewport parts={[]} selectedUid={null} bed={bed} gcode={gcode} mode="preview" layerRange={range} fitSignal={0}
                onLayerCount={(n) => { setLayers(n); setRange([0, Math.max(0, n - 1)]); }} />
              {layers > 1 && (
                <label className="job-layer">Top layer <input type="range" min={0} max={layers - 1} value={range[1]} onChange={(e) => setRange([0, +e.target.value])} />
                  <span>{range[1] + 1} / {layers}</span></label>
              )}
            </div>
          ) : <p className="muted">{p.files_deleted ? "The sliced files were removed to save space." : "No sliced file yet. It appears once the job is sliced for a printer."}</p>}
        </section>

        <section className="card job-card" aria-label="Photo of the finished plate">
          <h3>Finished plate</h3>
          {p.photo ? <img src={p.photo} alt="The finished plate" className="job-photo" /> :
            <div className="photo-slot"><Camera size={22} /><span>A camera picture of the finished plate will show here once printer cameras are connected.</span></div>}
        </section>

        <section className="card job-card wide" aria-label="Settings">
          <h3>Settings</h3>
          {p.changed_settings.length ? (
            <table className="audit"><thead><tr><th>Setting</th><th>This job</th><th>Preset</th></tr></thead>
              <tbody>{p.changed_settings.map((s) => <tr key={s.key}><td>{s.label}</td><td><b>{show(s.value, s.unit)}</b></td><td className="muted">{s.default == null ? "—" : show(s.default, s.unit)}</td></tr>)}</tbody></table>
          ) : <p className="muted">Nothing changed from the {p.preset} preset{p.goal ? ` (${p.goal.name} goal)` : ""}.</p>}
          {catalog && <button className="mini" onClick={() => setAllSettings(!allSettings)}>{allSettings ? "Hide the full list" : "Show all settings"}</button>}
          {allSettings && catalog && catalog.settings_schema.map((t) => (
            <details key={t.tab} className="settings-all"><summary>{t.tab}</summary>
              {t.groups.map((g) => (
                <div key={g.name}><h4>{g.name}</h4>
                  <dl className="review-facts">{g.fields.map((f) => <Fragment key={f.key}><dt>{f.label}</dt><dd className={p.settings[f.key] !== undefined && p.settings[f.key] !== defaults[f.key] ? "changed" : ""}>{full[f.key] === undefined ? "—" : show(full[f.key], fields.get(f.key)?.unit)}</dd></Fragment>)}</dl>
                </div>
              ))}
            </details>
          ))}
        </section>

        <section className="card job-card wide" aria-label="History">
          <h3>History</h3>
          <ol className="job-history">
            {p.events.map((e) => (
              <li key={e.id}><span className="muted">{when(e.at)}</span>
                <span>{e.kind === "phase" ? `${String(e.data.from ?? "new")} → ${String(e.data.to)}${e.data.reason ? ` (${String(e.data.reason)})` : ""}` : e.kind.replace(/_/g, " ")}</span>
                {e.actor && <span className="muted">{e.actor}</span>}</li>
            ))}
          </ol>
        </section>
      </div>
    </div>
  );
}
