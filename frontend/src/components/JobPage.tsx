import { Fragment, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Camera, Download, Pause, Play, Repeat, Square, X } from "lucide-react";
import { api, gcodeUrl, type Catalog, type JobPage as Page } from "../api";
import Viewport from "./Viewport";
import PlateCheck from "./PlateCheck";
import LiveProgress from "./LiveProgress";
import { statusOf } from "./JobsGrid";
import { fmtTime } from "../format";
import { TRAVEL, type FeatureInfo } from "../gcode";

const when = (t: number) => new Date(t * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const show = (v: unknown, unit?: string) => (typeof v === "boolean" ? (v ? "on" : "off") : `${v}${unit ? ` ${unit}` : ""}`);
const hex = (n: number) => "#" + n.toString(16).padStart(6, "0");
const CANCELLABLE = ["submitted", "previewing", "previewed", "pooled", "assigned", "slicing", "ready"];
type Tab = "settings" | "history" | "files";

/** One sentence on where the job is and what happens next. */
function statusLine(p: Page): string {
  const j = p.job;
  if (p.plate_to_clear) return `Finished on ${p.printer?.name}. Collect it, then confirm the plate is clear so the next job can start.`;
  if (j.result) return j.result.summary;
  switch (j.phase) {
    case "pooled": return j.waiting_for ?? "Waiting in the pool for a printer that fits.";
    case "assigned": return `Going to ${p.printer?.name ?? "a printer"}; it will be sliced for it next.`;
    case "slicing": return `Being sliced for ${p.printer?.name ?? "its printer"}.`;
    case "ready": return `Sliced for ${p.printer?.name}. It is waiting to be started.`;
    case "printing": return `Printing on ${p.printer?.name}.`;
    case "cancelled": return "This job was cancelled.";
    default: return j.error ?? "";
  }
}

/** One job (2.9): owners can act on it; operators and admins can read it. */
export default function JobPage({ id, catalog, onBack, onReuse }: {
  id: string; catalog: Catalog | null; onBack: () => void; onReuse: (p: Page) => void;
}) {
  const [p, setP] = useState<Page | null>(null);
  const [err, setErr] = useState("");
  const [gcode, setGcode] = useState<string | null>(null);
  const [layers, setLayers] = useState(0);
  const [top, setTop] = useState(0);
  const [features, setFeatures] = useState<FeatureInfo[]>([]);
  const [view, setView] = useState<"toolpaths" | "photo">("toolpaths");
  const [tab, setTab] = useState<Tab>("settings");
  const [allSettings, setAllSettings] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [busy, setBusy] = useState("");
  const control = async (action: "pause" | "resume" | "stop") => {
    if (action === "stop" && !confirm("Stop this print? It cannot be resumed, and the part on the plate is lost.")) return;
    setBusy(action); setErr("");
    try { const r = await api.controlPrint(id, action); if (!r.done) alert("Sent. The printer has not confirmed yet; check its screen if nothing changes."); }
    catch (e) { alert((e as Error).message); }
    setBusy(""); load();
  };
  const load = () => api.jobPage(id).then(setP).catch((e) => setErr(e.message));
  useEffect(() => { setP(null); setGcode(null); setTab("settings"); setView("toolpaths"); load(); }, [id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const printing = p?.job.phase === "printing" || !!p?.live;
  useEffect(() => {                         // while it prints, keep the numbers fresh
    if (!printing) return;
    const t = setInterval(() => api.jobPage(id).then(setP).catch(() => {}), 5000);
    return () => clearInterval(t);
  }, [printing, id]);
  useEffect(() => { if (p?.gcode) fetch(gcodeUrl(id)).then((r) => (r.ok ? r.text() : null)).then(setGcode).catch(() => setGcode(null)); }, [p?.gcode, id]);

  const bed = useMemo<[number, number, number]>(() => (p?.printer && catalog?.printer_models[p.printer.model]?.bed) || [256, 256, 256], [p, catalog]);
  const units = useMemo(() => new Map((catalog?.settings_schema ?? []).flatMap((t) => t.groups.flatMap((g) => g.fields.map((f) => [f.key, f.unit] as const)))), [catalog]);

  if (err) return <div className="page-body"><button className="chip" onClick={onBack}><ArrowLeft size={14} /> Back</button><p className="form-error">{err}</p></div>;
  if (!p) return <p className="muted">Loading…</p>;
  const j = p.job;
  const s = statusOf(j);
  const defaults = catalog?.preset_defaults[p.preset] ?? {};
  const full = { ...defaults, ...p.settings };
  const layerH = full.layer_height;
  const supports = p.settings.enable_support ?? defaults.enable_support;
  const pickupOk = j.pickup_by ? !j.at_risk : null;

  return (
    <div className="page-body job-page">
      <nav className="crumbs" aria-label="Breadcrumb"><button className="chip" onClick={onBack}><ArrowLeft size={14} /> Back</button><span aria-hidden="true">/</span><span>{j.name}</span></nav>

      <header className="job-head">
        <div className="job-title">
          <div className="row"><h2>{j.name}</h2><span className={"pill " + s.pill}>{s.label}</span></div>
          <p className="muted">{statusLine(p)}</p>
          {!p.mine && p.owner && <p className="muted">Owner: {p.owner}</p>}
        </div>
        <div className="job-actions">
          {p.mine && <button className="btn primary" onClick={() => onReuse(p)}><Repeat size={14} /> Same settings again</button>}
          {p.gcode && p.can_manage && <a className="btn" href={gcodeUrl(id)}><Download size={14} /> Download G-code</a>}
          {p.plate_to_clear && p.printer && <button className="btn" onClick={() => setClearing(!clearing)}>Confirm plate is clear</button>}
          {p.can_manage && j.phase && CANCELLABLE.includes(j.phase) && <button className="btn danger" onClick={() => confirm(`Cancel ${j.name}?`) && api.cancel(id).then(load)}><X size={14} /> Cancel job</button>}
        </div>
      </header>
      {p.live && (
        <section className="live-panel" aria-label="Live progress">
          <div className="live-head"><span className="lbl">Live from {p.printer?.name}</span>
            <div className="live-controls">
              {p.can_pause && p.live.state !== "PAUSE" && <button className="btn" disabled={!!busy} onClick={() => control("pause")}><Pause size={14} /> {busy === "pause" ? "Pausing…" : "Pause"}</button>}
              {p.can_pause && p.live.state === "PAUSE" && <button className="btn primary" disabled={!!busy} onClick={() => control("resume")}><Play size={14} /> {busy === "resume" ? "Resuming…" : "Resume"}</button>}
              {p.can_stop && <button className="btn danger" disabled={!!busy} onClick={() => control("stop")}><Square size={14} /> {busy === "stop" ? "Stopping…" : "Stop"}</button>}
              {p.can_pause && !p.can_stop && <span className="muted small">A shared plate can only be stopped by an operator.</span>}
            </div></div>
          <LiveProgress live={p.live} full />
        </section>
      )}
      {clearing && p.printer && <PlateCheck printerId={p.printer.id} onDone={() => { setClearing(false); load(); }} />}

      <section className="stats-strip" aria-label="Key numbers">
        <div className="stat"><span className="lbl">Print time</span><b>{fmtTime(p.actual_s ?? p.estimate_s)}</b><span>{p.actual_s != null ? `estimate ${fmtTime(p.estimate_s)}` : "estimate"}</span></div>
        <div className="stat"><span className="lbl">Filament</span><b>{p.filament_g != null ? `${p.filament_g} g` : "—"}</b><span>{p.filament}, {p.nozzle_mm} mm nozzle</span></div>
        <div className="stat"><span className="lbl">Layers</span><b>{p.layers ?? "—"}</b><span>{layerH != null ? `${layerH} mm` : ""}</span></div>
        <div className="stat"><span className="lbl">Printer</span><b className="small">{p.printer?.name ?? "Not chosen yet"}</b><span>{p.printer ? `${p.printer.model} · ${p.printer.bed_type.replace(" Plate", "")}` : "the pool chooses"}</span></div>
        {j.pickup_by && <div className="stat"><span className="lbl">Pickup by</span><b className="small">{when(j.pickup_by)}</b><span className={pickupOk ? "" : "bad-text"}>{pickupOk ? "on track" : "may be late"}</span></div>}
      </section>

      <div className="job-main">
        <section className="job-card" aria-label="Preview">
          <div className="card-head"><h3>{view === "photo" ? "Plate photo" : "Sliced preview"}</h3>
            <div className="seg small" role="radiogroup" aria-label="Preview">
              <button role="radio" aria-checked={view === "toolpaths"} className={view === "toolpaths" ? "on" : ""} onClick={() => setView("toolpaths")}>Toolpaths</button>
              <button role="radio" aria-checked={view === "photo"} className={view === "photo" ? "on" : ""} onClick={() => setView("photo")}>Plate photo</button>
            </div></div>
          {view === "photo" ? (
            p.photo ? <img src={p.photo} alt="The finished plate" className="job-photo" /> :
              <div className="photo-slot big"><Camera size={22} /><span>A camera picture of the finished plate shows here once printer cameras are connected.</span></div>
          ) : gcode ? (
            <>
              <div className="job-preview">
                <Viewport parts={[]} selectedUid={null} bed={bed} gcode={gcode} mode="preview" layerRange={[0, top]} fitSignal={0} onFeatures={setFeatures}
                  onLayerCount={(n) => { setLayers(n); setTop(Math.max(0, n - 1)); }} />
                {layers > 1 && (
                  <label className="job-layer">Top layer <input type="range" min={0} max={layers - 1} value={top} onChange={(e) => setTop(+e.target.value)} aria-label="Top layer" />
                    <span>{top + 1} / {layers}</span></label>
                )}
              </div>
              {features.length > 0 && <ul className="legend-row">{features.filter((f) => f.name !== TRAVEL).map((f) => <li key={f.name}><i style={{ background: hex(f.color) }} />{f.name}</li>)}</ul>}
            </>
          ) : <div className="photo-slot big"><span>{p.files_deleted ? "The sliced files were removed to save space." : "No sliced file yet. It appears once the job is sliced for a printer."}</span></div>}
        </section>

        <div className="job-side">
          <section className="job-card" aria-label="On this plate">
            <h3>On this plate</h3>
            <ul className="plate-list">
              {p.parts.map((x, i) => <li key={i}><b>{x.name}</b><span className="lbl">{p.mine ? "yours" : "this job"}</span></li>)}
              {p.shared_with.map((o, i) => <li key={"o" + i}><span>{o.name}</span><span className="lbl">{o.owner ?? "shared plate"}</span></li>)}
            </ul>
            {p.shared_with.length > 0 && <p className="muted small">Shares the plate with {p.shared_with.length} other part{p.shared_with.length > 1 ? "s" : ""}. Any owner can clear it afterwards.</p>}
            {p.mine && p.parts.some((x) => !x.available) && <p className="muted small">The model file was removed after not being used for a while. "Same settings again" keeps the settings and asks for the file.</p>}
          </section>
          <section className="job-card" aria-label="Finished plate">
            <h3>Finished plate</h3>
            {p.photo ? <img src={p.photo} alt="The finished plate" className="job-photo" /> :
              <div className="photo-slot"><Camera size={20} /><span>A camera picture of the finished plate shows here once printer cameras are connected.</span></div>}
          </section>
        </div>
      </div>

      <section className="job-card" aria-label="Details">
        <div className="tabs-row" role="tablist">
          {(["settings", "history", "files"] as Tab[]).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={"tab2" + (tab === t ? " on" : "")} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
          ))}
        </div>
        {tab === "settings" && (
          <>
            <dl className="review-facts">
              <dt>Goal</dt><dd>{p.goal?.name ?? "Custom settings"} ({p.preset} preset)</dd>
              <dt>Material</dt><dd>{p.filament}, {p.nozzle_mm} mm nozzle</dd>
              <dt>Supports</dt><dd>{supports ? `${String(full.support_type ?? "").startsWith("tree") ? "Tree" : "Normal"}${full.support_critical_regions_only ? ", critical overhangs only" : ""}${full.support_on_build_plate_only ? ", from the build plate only" : ""}` : "None"}</dd>
            </dl>
            <div className="lbl">Changed from the preset</div>
            {p.changed_settings.length ? (
              <table className="audit"><thead><tr><th>Setting</th><th>This job</th><th>Preset</th></tr></thead>
                <tbody>{p.changed_settings.map((c) => <tr key={c.key}><td>{c.label}</td><td className="changed">{show(c.value, c.unit)}</td><td className="muted">{c.default == null ? "—" : show(c.default, c.unit)}</td></tr>)}</tbody></table>
            ) : <p className="muted">Nothing changed from the preset.</p>}
            {catalog && <button className="chip self-start" onClick={() => setAllSettings(!allSettings)}>{allSettings ? "Hide the full list" : "Show all settings"}</button>}
            {allSettings && catalog && catalog.settings_schema.map((t) => (
              <details key={t.tab} className="settings-all"><summary>{t.tab}</summary>
                {t.groups.map((g) => (
                  <div key={g.name}><h4>{g.name}</h4>
                    <dl className="review-facts">{g.fields.map((f) => <Fragment key={f.key}><dt>{f.label}</dt>
                      <dd className={p.settings[f.key] !== undefined && p.settings[f.key] !== defaults[f.key] ? "changed" : ""}>{full[f.key] === undefined ? "—" : show(full[f.key], units.get(f.key))}</dd></Fragment>)}</dl>
                  </div>
                ))}
              </details>
            ))}
          </>
        )}
        {tab === "history" && (
          <ol className="timeline">
            {p.events.map((e) => (
              <li key={e.id}><i className={e.kind === "phase" && String(e.data.to) === "failed" ? "bad" : ""} aria-hidden="true" />
                <span>{e.kind === "phase" ? `${String(e.data.from ?? "new")} → ${String(e.data.to)}${e.data.reason ? ` (${String(e.data.reason)})` : ""}` : e.kind.replace(/_/g, " ")}
                  {e.actor && <span className="muted"> · {e.actor}</span>}</span>
                <span className="lbl">{when(e.at)}</span></li>
            ))}
          </ol>
        )}
        {tab === "files" && (
          <dl className="review-facts">
            <dt>Model{p.parts.length > 1 ? "s" : ""}</dt><dd>{p.parts.map((x) => `${x.name}${x.available ? "" : " (removed)"}`).join(", ")}</dd>
            <dt>Sliced file</dt><dd>{p.gcode ? "Available" : p.files_deleted ? "Removed to save space" : "Not sliced yet"}</dd>
            <dt>Plate layout</dt><dd>{p.parts.length > 1 ? `${p.parts.length} parts` : "1 part"}{p.shared_with.length ? `, plus ${p.shared_with.length} from other people` : ""}</dd>
            <dt>Created</dt><dd>{when(j.created)}</dd>
          </dl>
        )}
      </section>
    </div>
  );
}
