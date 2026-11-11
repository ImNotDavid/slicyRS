import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Box, ChevronDown, ChevronUp, Download, FolderOpen, ListChecks, Maximize, Menu, Move3d, Play, Printer as PrinterIcon,
  RotateCcw, RotateCw, Scaling, Layers, ShieldCheck, LogOut,
} from "lucide-react";
import { api, gcodeUrl, modelUrl, type AssignResult, type Catalog, type Job, type ModelCheck, type Printer, type Transform, type User } from "./api";
import Viewport from "./components/Viewport";
import SettingsPanel from "./components/SettingsPanel";
import SavedPresets from "./components/SavedPresets";
import FleetModal from "./components/FleetModal";
import AdminModal from "./components/AdminModal";
import JobsModal, { fmtTime } from "./components/JobsModal";
import SubmitModal from "./components/SubmitModal";

type Value = number | string | boolean;
const IDENTITY: Transform = { rotation: [0, 0, 0], scale: 1, position: [0, 0] };

export default function App({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [model, setModel] = useState<{ id: string; name: string; dims: [number, number, number]; checks?: ModelCheck[] } | null>(null);
  const [filament, setFilament] = useState("PLA");
  const [preset, setPreset] = useState("0.20mm Standard");
  const [settings, setSettings] = useState<Record<string, Value>>({});
  const [transform, setTransform] = useState<Transform>(IDENTITY);
  const [tool, setTool] = useState<"move" | "rotate" | "scale" | null>(null);
  const [pickFace, setPickFace] = useState(false);
  const [goal, setGoal] = useState<string | null>("standard");
  const [goalNotes, setGoalNotes] = useState<string[]>([]);
  const [assignment, setAssignment] = useState<AssignResult | null>(null);
  const [manualPrinter, setManualPrinter] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [gcode, setGcode] = useState<string | null>(null);
  const [mode, setMode] = useState<"prepare" | "preview">("prepare");
  const [layerCount, setLayerCount] = useState(0);
  const [layerRange, setLayerRange] = useState<[number, number]>([0, 0]);
  const [fitSignal, setFitSignal] = useState(0);
  const [infoOpen, setInfoOpen] = useState(() => window.innerWidth > 900);
  const [modal, setModal] = useState<null | "fleet" | "jobs" | "admin" | "submit">(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const isAdmin = user.role === "admin";
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refreshPrinters = useCallback(() => api.printers().then(setPrinters).catch(() => {}), []);

  useEffect(() => {
    api.catalog().then((c) => { setCatalog(c); setSettings(c.preset_defaults["0.20mm Standard"]); }).catch((e) => setMessage({ kind: "error", text: e.message }));
    refreshPrinters();
    const t = setInterval(refreshPrinters, 5000);
    return () => clearInterval(t);
  }, [refreshPrinters]);

  const changePreset = (id: string) => {
    setGoal(null);
    setPreset(id);
    if (catalog) setSettings(catalog.preset_defaults[id]);
  };

  // the nozzle size is implied by the process preset; the choices are the nozzles the fleet has
  const nozzle = catalog?.process_presets.find((p) => p.id === preset)?.nozzle ?? 0.4;
  const fleetNozzles = useMemo(() => {
    const sizes = [...new Set(printers.filter((p) => p.enabled && p.nozzle_mm).map((p) => p.nozzle_mm as number))].sort((a, b) => a - b);
    return sizes.length ? sizes : [0.4];
  }, [printers]);
  const changeNozzle = (n: number) => {
    if (!catalog) return;
    if (goal) applyGoal(goal, filament, n);
    else changePreset(catalog.goals.find((g) => g.id === "standard")?.presets[String(n)] ?? preset);
  };
  useEffect(() => { // the chosen nozzle must be one a printer actually has
    if (catalog && printers.length && !fleetNozzles.includes(nozzle)) changeNozzle(fleetNozzles[0]);
  }, [fleetNozzles, catalog, nozzle]); // eslint-disable-line react-hooks/exhaustive-deps

  const applyGoal = async (id: string, material = filament, n = nozzle) => {
    try {
      const r = await api.goal(id, material, n);
      setPreset(r.preset); setSettings(r.settings); setGoal(id); setGoalNotes(r.notes);
    } catch (e) { setMessage({ kind: "error", text: (e as Error).message }); }
  };
  // a goal follows the material: switching filament re-applies it with that material's adjustments
  useEffect(() => { if (goal && catalog) applyGoal(goal, filament); }, [filament]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-run printer assignment whenever the part, material, transform or fleet changes.
  const fleetKey = JSON.stringify(printers.map((p) => [p.id, p.status.state, p.queue_depth, p.status.ams]));
  useEffect(() => {
    if (!model) { setAssignment(null); return; }
    const t = setTimeout(() => {
      api.assign(model.id, filament, transform, nozzle).then(setAssignment).catch((e) => setMessage({ kind: "error", text: e.message }));
    }, 200);
    return () => clearTimeout(t);
  }, [model, filament, transform, fleetKey, nozzle]);

  // Any input change invalidates the previous slice.
  useEffect(() => { setJob(null); setGcode(null); setMode("prepare"); }, [model, filament, preset, settings, transform, manualPrinter]);

  const chosenId = manualPrinter || assignment?.chosen || "";
  const chosen = assignment?.candidates.find((c) => c.printer_id === chosenId);
  const chosenPrinter = printers.find((p) => p.id === chosenId);
  const bed = useMemo<[number, number, number]>(
    () => (chosenPrinter && catalog ? catalog.printer_models[chosenPrinter.model].bed : [256, 256, 256]),
    [chosenPrinter, catalog]);

  const upload = async (file: File) => {
    setBusy(true); setMessage(null);
    try {
      const m = await api.upload(file);
      setTransform(IDENTITY); setManualPrinter(""); setModel(m);
    } catch (e) { setMessage({ kind: "error", text: (e as Error).message }); }
    setBusy(false);
  };

  const openJob = async (j: Job) => {
    setJob(j);
    setGcode(await fetch(gcodeUrl(j.id)).then((r) => r.text()));
    setMode("preview");
    setModal(null);
  };

  const slice = async () => {
    if (!model) return;
    setBusy(true); setMessage(null);
    try {
      let j = await api.slice({ model_id: model.id, filament, preset, settings, printer_id: manualPrinter || undefined, ...transform });
      setJob(j);
      while (j.state === "slicing") {
        await new Promise((r) => setTimeout(r, 800));
        j = await api.job(j.id);
      }
      if (j.state === "failed") throw new Error(j.error ?? "Slicing failed");
      setJob(j);
      setGcode(await fetch(gcodeUrl(j.id)).then((r) => r.text()));
      setMode("preview");
    } catch (e) { setMessage({ kind: "error", text: (e as Error).message }); }
    setBusy(false);
  };

  const orientTo = async (mode: "flat" | "auto" | "face", normal?: [number, number, number]) => {
    if (!model) return;
    setBusy(true);
    try {
      const r = await api.orient(model.id, mode, normal);
      setTransform({ ...transform, rotation: r.rotation });
      setMessage({ kind: "ok", text: mode === "face" ? "Laid on the face you picked" : mode === "flat" ? "Laid flat on its largest face" : r.before.overhang_percent - r.after.overhang_percent > 0.5
        ? `Turned to need less support (overhang ${Math.round(r.before.overhang_percent)}% → ${Math.round(r.after.overhang_percent)}%)` : "Already a good orientation" });
    } catch (e) { setMessage({ kind: "error", text: (e as Error).message }); }
    setBusy(false);
  };

  const queue = async () => { if (job) { setJob(await api.queue(job.id)); setMessage({ kind: "ok", text: `Queued on ${job.printer_name}` }); refreshPrinters(); } };
  const print = async () => {
    if (!job) return;
    setBusy(true);
    try {
      const r = await api.print(job.id);
      setJob({ ...job, printed: true, queued: true });
      setMessage({ kind: "ok", text: r.started ? `Printing on ${job.printer_name}` : `Uploaded '${r.file}' to ${job.printer_name}'s SD card but it has not reported starting. Start it from the printer's screen.` });
      refreshPrinters();
    } catch (e) { setMessage({ kind: "error", text: (e as Error).message }); }
    setBusy(false);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setModal(null); setTool(null); setPickFace(false); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (message?.kind !== "ok") return;
    const t = setTimeout(() => setMessage(null), 5000);
    return () => clearTimeout(t);
  }, [message]);

  useEffect(() => { if (tool !== "rotate" || mode !== "prepare") setPickFace(false); }, [tool, mode]);

  const onLayerCount = useCallback((n: number) => { setLayerCount(n); setLayerRange([0, Math.max(0, n - 1)]); }, []);

  if (!catalog) return <div className="loading">{message?.text ?? "Loading…"}</div>;

  const dims = assignment?.dims ?? model?.dims;
  const ready = job?.state === "done";

  return (
    <div className="app" onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) upload(f); }}>
      <input ref={fileInput} type="file" hidden accept=".stl,.3mf,.obj,.step,.stp" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />

      <nav className="rail">
        <div className="logo" aria-label="slicyRS">SP</div>
        <button className={modal ? "" : "on"} title="Prepare" onClick={() => setModal(null)}><Box size={22} /></button>
        <button title="Open model" onClick={() => fileInput.current?.click()}><FolderOpen size={22} /></button>
        <button className={modal === "fleet" ? "on" : ""} title="Printer fleet" onClick={() => setModal("fleet")}><PrinterIcon size={22} /></button>
        <button className={modal === "jobs" ? "on" : ""} title="Jobs" onClick={() => setModal("jobs")}><ListChecks size={22} /></button>
        {isAdmin && <button className={modal === "admin" ? "on" : ""} title="Admin" onClick={() => setModal("admin")}><ShieldCheck size={22} /></button>}
      </nav>

      <header className="topbar">
        <Menu size={20} />
        <span className="spacer" />
        <span className="muted engine-note">{catalog.slicer_mode === "mock" ? "Mock slicer: perimeters only, not real G-code" : `${catalog.engine} engine`}</span>
        <div className="user-menu">
          <button className="user" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>
            <div className="avatar">{(user.name || user.email)[0].toUpperCase()}</div>
            <span>{user.name || user.email}</span>
            {isAdmin && <em className="role-badge">admin</em>}
          </button>
          {menuOpen && (
            <div className="menu" role="menu">
              <div className="menu-head"><strong>{user.name || user.email}</strong><span className="muted">{user.email}</span></div>
              <button role="menuitem" onClick={onSignOut}><LogOut size={16} /> Sign out</button>
              <button role="menuitem" onClick={async () => { await api.logoutAll().catch(() => {}); onSignOut(); }}><LogOut size={16} /> Sign out all devices</button>
            </div>
          )}
        </div>
      </header>

      <aside className="panel">
        <div className="card select-card"><span className="engine">{catalog.engine}</span><ChevronDown size={18} /></div>

        {model?.checks?.length ? (
          <div className="card checks" aria-label="Checks on the uploaded model">
            {model.checks.map((c) => <p key={c.text} className={c.level === "warn" ? "form-error" : "muted"}>{c.level === "warn" ? "Check: " : ""}{c.text}</p>)}
          </div>
        ) : null}

        <div className="card">
          <div className="printer-head">
            <div className="printer-icon"><PrinterIcon size={34} /></div>
            <div>
              <strong>{chosenPrinter ? chosenPrinter.model : model ? "No eligible printer" : "Auto printer"}</strong>
              <div className="muted">{chosenPrinter ? `${chosenPrinter.name} · ${chosenPrinter.bed_type.replace(" Plate", "")}` : "Open a model to assign"}</div>
            </div>
            <div className="plate">{bed[0]}×{bed[1]}</div>
          </div>
          <select className="row-select" value={manualPrinter} onChange={(e) => setManualPrinter(e.target.value)} disabled={!assignment}>
            <option value="">{!model ? "Auto: open a model first" : assignment?.chosen ? "Auto (recommended)" : "Auto: none eligible"}</option>
            {assignment?.candidates.map((c) => (
              <option key={c.printer_id} value={c.printer_id} disabled={!c.eligible}>
                {c.name} ({c.model}){c.eligible ? "" : " — not eligible"}
              </option>
            ))}
          </select>
          {chosen && (
            <ul className="why">
              {chosen.reasons.map((r) => <li key={r}>{r}</li>)}
            </ul>
          )}
          {assignment && !assignment.chosen && (
            <ul className="why bad">
              {[...new Set(assignment.candidates.flatMap((c) => c.blockers))].slice(0, 4).map((b) => <li key={b}>{b}</li>)}
            </ul>
          )}
        </div>

        <div className="card">
          <div className="printer-head">
            <div className="spool" style={{ background: chosenPrinter?.status.ams?.find((s) => s.slot === chosen?.loaded_slot)?.color ?? "#2f6bff" }} />
            <div>
              <strong>{filament}</strong>
              <div className="muted">{catalog.filaments[filament].nozzle}° / {catalog.filaments[filament].bed}°
                {catalog.filaments[filament].enclosed ? " · needs enclosure" : ""}</div>
            </div>
          </div>
          <select className="row-select" value={filament} onChange={(e) => setFilament(e.target.value)}>
            {Object.entries(catalog.filaments).map(([key, f]) => <option key={key} value={key}>{f.orca}</option>)}
          </select>
          <select className="row-select" value={nozzle} aria-label="Nozzle size" onChange={(e) => changeNozzle(Number(e.target.value))}
            title="Only nozzle sizes that your printers have are offered">
            {fleetNozzles.map((n) => <option key={n} value={n}>{n} mm nozzle</option>)}
          </select>
        </div>

        <div className="goals" role="group" aria-label="Print goal">
          {catalog.goals.map((g) => (
            <button key={g.id} className={"goal" + (goal === g.id ? " on" : "")} aria-pressed={goal === g.id} title={g.description}
              onClick={() => applyGoal(g.id)}>{g.name}</button>
          ))}
        </div>
        {goal && (
          <p className="goal-note muted">{catalog.goals.find((g) => g.id === goal)?.description}{goalNotes.length ? " " + goalNotes.join(" ") : ""}</p>
        )}

        <div className="preset-row">
          <select value={preset} onChange={(e) => changePreset(e.target.value)}>
            {catalog.process_presets.filter((p) => p.nozzle === nozzle).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <button className="icon-btn" title="Reset settings to preset" aria-label="Reset settings to preset"
            disabled={JSON.stringify(settings) === JSON.stringify(catalog.preset_defaults[preset])}
            onClick={() => { setGoal(null); setSettings(catalog.preset_defaults[preset]); }}><RotateCcw size={18} /></button>
        </div>

        <SavedPresets catalog={catalog} user={user} current={{ preset, filament, settings }}
          onApply={(p) => { setGoal(null); setFilament(p.filament); setPreset(p.preset); setSettings({ ...catalog.preset_defaults[p.preset], ...p.settings }); }} />

        <SettingsPanel catalog={catalog} values={settings} baseline={catalog.preset_defaults[preset]}
          onChange={(k, v) => { setGoal(null); setSettings((s) => ({ ...s, [k]: v })); }} />
      </aside>

      <main className="viewport">
        <Viewport modelUrl={model ? modelUrl(model.id) : null} bed={bed} transform={transform} gcode={gcode} mode={mode}
          layerRange={layerRange} onLayerCount={onLayerCount} fitSignal={fitSignal}
          pickFace={pickFace} onPickFace={(n) => { setPickFace(false); orientTo("face", n); }}
          moveMode={tool === "move" && mode === "prepare"} onMove={(position) => setTransform((t) => ({ ...t, position }))} />

        <div className="mode-toggle">
          <button className={mode === "prepare" ? "btn primary" : "btn"} onClick={() => setMode("prepare")}>PREPARE</button>
          <button className={mode === "preview" ? "btn primary" : "btn"} disabled={!gcode} onClick={() => setMode("preview")}>PREVIEW</button>
        </div>

        {!model && (
          <div className="empty">
            <button className="btn primary big" onClick={() => fileInput.current?.click()} disabled={busy}>
              {busy ? "Uploading…" : "Open a model"}
            </button>
            <p className="muted">or drop an STL / 3MF / OBJ / STEP file anywhere</p>
          </div>
        )}

        {model && mode === "prepare" && (
          <div className="tools">
            <button title="Move" className={tool === "move" ? "on" : ""} onClick={() => setTool(tool === "move" ? null : "move")}><Move3d size={20} /></button>
            <button title="Rotate" className={tool === "rotate" ? "on" : ""} onClick={() => setTool(tool === "rotate" ? null : "rotate")}><RotateCw size={20} /></button>
            <button title="Scale" className={tool === "scale" ? "on" : ""} onClick={() => setTool(tool === "scale" ? null : "scale")}><Scaling size={20} /></button>
            <button title="Reset view" onClick={() => setFitSignal((n) => n + 1)}><Maximize size={20} /></button>
            {tool === "move" && (
              <div className="popover">
                {(["X", "Y"] as const).map((ax, i) => (
                  <label key={ax}><b>{ax}</b>
                    <input type="number" step={1} value={Math.round(transform.position[i] * 10) / 10} aria-label={`Position ${ax} in millimetres from the middle of the bed`}
                      onChange={(e) => { const p = [...transform.position] as [number, number]; p[i] = Number(e.target.value) || 0; setTransform({ ...transform, position: p }); }} />
                    <span>mm</span><span />
                  </label>
                ))}
                <div className="actions">
                  <button className="mini" disabled={!transform.position[0] && !transform.position[1]} title="Let the slicer place the part (the middle of the bed)"
                    onClick={() => { setTransform({ ...transform, position: [0, 0] }); setMessage({ kind: "ok", text: "The slicer will place the part" }); }}>Auto-arrange</button>
                </div>
                <p className="hint">Drag the part on the bed, or type a position. 0, 0 is the middle of the bed; the part stays where you put it.</p>
              </div>
            )}
            {tool === "rotate" && (
              <div className="popover">
                {(["X", "Y", "Z"] as const).map((ax, i) => (
                  <label key={ax}><b>{ax}</b>
                    <input type="number" step={15} value={transform.rotation[i]} aria-label={`Rotate ${ax} degrees`}
                      onChange={(e) => { const r = [...transform.rotation] as [number, number, number]; r[i] = Number(e.target.value); setTransform({ ...transform, rotation: r }); }} />
                    <span>°</span>
                    <button className="mini" onClick={() => { const r = [...transform.rotation] as [number, number, number]; r[i] = (r[i] + 90) % 360; setTransform({ ...transform, rotation: r }); }}>+90°</button>
                  </label>
                ))}
                <div className="actions">
                  <button className={"mini" + (pickFace ? " on" : "")} aria-pressed={pickFace} disabled={busy}
                    title="Then click the face of the part that should lie on the bed" onClick={() => setPickFace(!pickFace)}>Lay flat</button>
                  <button className="mini" disabled={busy} title="Turn the part to need the least support" onClick={() => orientTo("auto")}>Auto-orient</button>
                  <button className="mini" disabled={busy} onClick={() => setTransform({ ...transform, rotation: [0, 0, 0] })}>Reset</button>
                </div>
                {pickFace && <p className="hint">Click the face that should lie on the bed.</p>}
              </div>
            )}
            {tool === "scale" && (
              <div className="popover scale">
                <label>Scale <input aria-label="Scale percent" type="number" min={1} step={5} value={Math.round(transform.scale * 100)}
                  onChange={(e) => Number(e.target.value) > 0 && setTransform({ ...transform, scale: Number(e.target.value) / 100 })} /><span>%</span></label>
              </div>
            )}
          </div>
        )}

        {mode === "preview" && layerCount > 1 && (
          <div className="layer-slider" title="Layer range">
            <Layers size={16} />
            <input type="range" className="v" min={0} max={layerCount - 1} value={layerRange[0]} aria-label="Bottom layer"
              onChange={(e) => setLayerRange([Math.min(+e.target.value, layerRange[1]), layerRange[1]])} />
            <input type="range" className="v" min={0} max={layerCount - 1} value={layerRange[1]} aria-label="Top layer"
              onChange={(e) => setLayerRange([layerRange[0], Math.max(+e.target.value, layerRange[0])])} />
            <span className="readout">{layerRange[0] + 1}–{layerRange[1] + 1} / {layerCount}</span>
          </div>
        )}

        {message && <div className={"toast " + message.kind} role="status" onClick={() => setMessage(null)}>{message.text}</div>}

        {model && (
          <div className="info">
            <button className="info-head" onClick={() => setInfoOpen(!infoOpen)}>
              Print Info {infoOpen ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
            </button>
            {infoOpen && (
              <dl>
                <dt>Size</dt><dd>{dims ? dims.map((d) => d.toFixed(1)).join(" × ") + " mm" : "—"}</dd>
                <dt>Printer</dt><dd>{job?.printer_name ?? chosenPrinter?.name ?? "—"}</dd>
                <dt>Filament</dt><dd>{filament}{job?.stats?.filament_g != null ? ` · ${job.stats.filament_g} g` : ""}</dd>
                <dt>Time</dt><dd>{fmtTime(job?.stats?.time_s)}</dd>
                <dt>Layers</dt><dd>{job?.stats?.layers ?? "—"}</dd>
              </dl>
            )}
          </div>
        )}

        <div className="bottombar">
          <span className="filename">{model?.name ?? "No file"}</span>
          <a className={"btn" + (ready ? "" : " disabled")} href={ready ? gcodeUrl(job!.id) : undefined} aria-label="Download G-code" title="Download G-code"><Download size={16} /></a>
          <button className="btn primary" disabled={!model || busy || !assignment?.chosen && !manualPrinter} onClick={slice}>
            <Play size={14} /> {busy && job?.state === "slicing" ? "SLICING…" : "SLICE"}
          </button>
          <button className="btn dark" disabled={!ready || job?.queued} onClick={queue}>QUEUE</button>
          <button className="btn" disabled={!model || busy} onClick={() => setModal("submit")} title="Let the system choose a printer and when">SUBMIT TO POOL</button>
          <button className="btn go" disabled={!ready || busy || job?.printed} onClick={print}>{job?.printed ? "SENT" : "PRINT"}</button>
        </div>
      </main>

      {modal === "fleet" && <FleetModal printers={printers} onClose={() => setModal(null)} onChanged={refreshPrinters} />}
      {modal === "submit" && model && <SubmitModal spec={{ model_id: model.id, filament, preset, settings, ...transform }} onClose={() => setModal(null)}
        onDone={(j) => {
          setModal(null); setMessage({ kind: "ok", text: `${j.name} is in the print pool. Follow it under Jobs.` }); }} />}
      {modal === "admin" && isAdmin && <AdminModal catalog={catalog} me={user} onClose={() => setModal(null)} onPrintersChanged={refreshPrinters} />}
      {modal === "jobs" && <JobsModal onClose={() => setModal(null)} onOpen={openJob} showOwner={isAdmin} />}
    </div>
  );
}
