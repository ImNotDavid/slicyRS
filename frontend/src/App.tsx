import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ArrowDownToLine, Sparkles, Box, ChevronDown, ChevronUp, Download, FolderOpen, ListChecks, Maximize, Menu, Move3d, Pause, Play, Printer as PrinterIcon,
  RotateCcw, RotateCw, Scaling, Layers, ShieldCheck, LogOut,
} from "lucide-react";
import { api, gcodeUrl, modelUrl, type AssignResult, type Catalog, type Job, type JobPage, type LoadedMaterial, type Printer, type Transform, type User } from "./api";
import { colourName } from "./colours";
import { IDENTITY, MAX_PARTS, newPart, payload, plateSpec, type Part } from "./plate";
import PartsPanel from "./components/PartsPanel";
import Viewport from "./components/Viewport";
import { TRAVEL, type FeatureInfo } from "./gcode";
import SettingsPanel from "./components/SettingsPanel";
import SavedPresets from "./components/SavedPresets";
import SupportControl, { SUPPORT_KEYS } from "./components/SupportControl";
import { fmtTime } from "./format";
import SubmitModal from "./components/SubmitModal";

type Value = number | string | boolean;
const PLAY_SECONDS = 12;                       // a full run through the visible layers in the G-code playback
// 2.5: rough wait before a printer could start this, for the printer menu
const waitText = (s?: number | null) => (s == null ? "" : s < 300 ? " · free now" : ` · about ${s >= 3600 ? `${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}` : Math.round(s / 60) + " min"} wait`);
const hex = (n: number) => "#" + n.toString(16).padStart(6, "0");

export default function App({ user, reuseRequest, onReused, partRequest, onPartLoaded }: {
  user: User; reuseRequest: JobPage | null; onReused: () => void;
  partRequest: { id: string; add: boolean } | null; onPartLoaded: () => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [parts, setParts] = useState<Part[]>([]);
  const [selectedUid, setSelectedUid] = useState<string | null>(null);
  const [overlapPairs, setOverlapPairs] = useState<[string, string][]>([]);
  const [filament, setFilament] = useState("PLA");
  // owner request: only the materials and colours loaded in the pool's printers right now are offered
  const [loaded, setLoaded] = useState<LoadedMaterial[] | null>(null);
  const [colour, setColour] = useState<string | null>(null);
  useEffect(() => {
    const get = () => api.loadedFilament().then(setLoaded).catch(() => {});
    get(); const t = setInterval(get, 30000); return () => clearInterval(t);
  }, []);
  const colours = loaded?.find((m) => m.material === filament)?.colours ?? [];
  useEffect(() => {
    if (!loaded) return;
    if (loaded.length && !loaded.some((m) => m.material === filament)) { setFilament(loaded[0].material); return; }
    if (!colours.some((c) => c.colour === colour)) setColour(colours[0]?.colour ?? null);
  }, [loaded, filament]); // eslint-disable-line react-hooks/exhaustive-deps
  const noFilament = loaded !== null && !colour;
  const colourPicker = (
    <div className="chips colour-chips" role="radiogroup" aria-label="Colour">
      {colours.map((c) => (
        <button key={c.colour} role="radio" aria-checked={colour === c.colour} className={"chip" + (colour === c.colour ? " on" : "")}
          title={`Loaded on ${c.printers.join(", ")}`} onClick={() => setColour(c.colour)}>
          <i className="dot-swatch" style={{ background: c.colour }} aria-hidden="true" />{colourName(c.colour)}</button>
      ))}
    </div>
  );
  const noFilamentNote = <p className="form-error">No filament is loaded in a printer you can use right now (or the printers are offline). Ask an operator to load some, then try again.</p>;
  const [preset, setPreset] = useState("0.20mm Standard");
  const [settings, setSettings] = useState<Record<string, Value>>({});
  // the tools act on the selected part; the rest of the app reads it as "the model" and "its transform"
  const selectedPart = parts.find((p) => p.uid === selectedUid) ?? parts[0] ?? null;
  const model = selectedPart?.model ?? null;
  const transform = selectedPart?.transform ?? IDENTITY;
  const setTransform = (t: Transform | ((t: Transform) => Transform)) =>
    setParts((ps) => ps.map((p) => (p.uid === selectedPart?.uid ? { ...p, transform: typeof t === "function" ? t(p.transform) : t } : p)));
  const viewParts = useMemo(() => parts.map((p) => ({ uid: p.uid, url: modelUrl(p.model.id), transform: p.transform })), [parts]);
  const plateName = parts.length > 1 ? `${parts[0].model.name} + ${parts.length - 1} more` : parts[0]?.model.name ?? "No file";
  const [tool, setTool] = useState<"move" | "rotate" | "scale" | null>(null);
  const [pickFace, setPickFace] = useState(false);
  const [showOverhang, setShowOverhang] = useState(false);
  const [overhangPercent, setOverhangPercent] = useState<number | null>(null);
  const [features, setFeatures] = useState<FeatureInfo[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  const [travel, setTravel] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
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
  const [modal, setModal] = useState<null | "submit">(null);
  const isAdmin = user.role === "admin";
  const [workflow, setWorkflowState] = useState<"guided" | "full">(() => { try { return localStorage.getItem("workflow") === "full" ? "full" : "guided"; } catch { return "guided"; } });
  const setWorkflow = (w: "guided" | "full") => { setWorkflowState(w); try { localStorage.setItem("workflow", w); } catch { /* not remembered */ } };
  const [step, setStep] = useState(1);
  const criticalTouched = useRef(false);                 // once the person sets it themselves, the guided default stops applying
  const [sliceSig, setSliceSig] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const addInput = useRef<HTMLInputElement>(null);

  const refreshPrinters = useCallback(() => api.printers().then(setPrinters).catch(() => {}), []);

  useEffect(() => {
    api.catalog().then((c) => { setCatalog(c); setSettings(c.preset_defaults["0.20mm Standard"]); }).catch((e) => setMessage({ kind: "error", text: e.message }));
    refreshPrinters();
    const t = setInterval(refreshPrinters, 5000);
    return () => clearInterval(t);
  }, [refreshPrinters]);

  // the guided view starts with "support critical regions only" on: fewer supports, less to clean up
  useEffect(() => {
    if (workflow === "guided" && catalog && !criticalTouched.current) setSettings((s) => (s.support_critical_regions_only ? s : { ...s, support_critical_regions_only: true }));
  }, [workflow, catalog, preset]);   // eslint-disable-line react-hooks/exhaustive-deps

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
      setPreset(r.preset); setSettings((prev) => ({ ...r.settings, ...Object.fromEntries(SUPPORT_KEYS.filter((k) => k in prev).map((k) => [k, prev[k]])) })); setGoal(id); setGoalNotes(r.notes);
    } catch (e) { setMessage({ kind: "error", text: (e as Error).message }); }
  };
  // a goal follows the material: switching filament re-applies it with that material's adjustments
  const keepSettingsOnce = useRef(false);   // "same settings again" sets material and settings together: do not re-apply the goal over them
  useEffect(() => { if (keepSettingsOnce.current) { keepSettingsOnce.current = false; return; } if (goal && catalog) applyGoal(goal, filament); }, [filament]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-run printer assignment whenever the part, material, transform or fleet changes.
  const fleetKey = JSON.stringify(printers.map((p) => [p.id, p.status.state, p.queue_depth, p.status.ams]));
  useEffect(() => {
    if (!parts.length) { setAssignment(null); return; }
    const t = setTimeout(() => {
      api.assign(plateSpec(parts), filament, nozzle).then(setAssignment).catch((e) => setMessage({ kind: "error", text: e.message }));
    }, 200);
    return () => clearTimeout(t);
  }, [parts, filament, fleetKey, nozzle]);

  // Any input change invalidates the previous slice.
  useEffect(() => { setJob(null); setGcode(null); setMode("prepare"); }, [parts, filament, preset, settings, manualPrinter]);

  const chosenId = manualPrinter || assignment?.chosen || "";
  const chosen = assignment?.candidates.find((c) => c.printer_id === chosenId);
  const chosenPrinter = printers.find((p) => p.id === chosenId);
  const bed = useMemo<[number, number, number]>(
    () => (chosenPrinter && catalog ? catalog.printer_models[chosenPrinter.model].bed : [256, 256, 256]),
    [chosenPrinter, catalog]);

  const upload = async (file: File) => {                       // opening a file starts a new plate with that one part
    setBusy(true); setMessage(null);
    try {
      const p = newPart(await api.upload(file));
      setParts([p]); setSelectedUid(p.uid); setManualPrinter("");
    } catch (e) { setMessage({ kind: "error", text: (e as Error).message }); }
    setBusy(false);
  };

  // the printer model the slicer lays the plate out for: the chosen printer, else the smallest bed in the fleet (so the layout fits all of them)
  const layoutPrinterModel = (): string => {
    if (chosenPrinter) return chosenPrinter.model;
    const beds = printers.filter((p) => p.enabled && catalog?.printer_models[p.model]).map((p) => ({ m: p.model, a: catalog!.printer_models[p.model].bed }));
    beds.sort((x, y) => x.a[0] * x.a[1] - y.a[0] * y.a[1]);
    return beds[0]?.m ?? "X1C";
  };
  const arrangeAll = async (list: Part[]) => {
    if (list.length < 2) { setParts(list); return; }
    setBusy(true);
    try {
      const r = await api.arrange(list.map(payload), layoutPrinterModel(), filament, preset);
      setParts(list.map((p, i) => ({ ...p, transform: { ...p.transform, position: r.parts[i].position,
        rotation: [p.transform.rotation[0], p.transform.rotation[1], (((p.transform.rotation[2] + r.parts[i].rotation_z) % 360) + 360) % 360] as [number, number, number] } })));
    } catch (e) {
      setParts(list);
      setMessage({ kind: "error", text: (e as Error).message });
    }
    setBusy(false);
  };
  const addPart = async (file: File) => {
    if (parts.length >= MAX_PARTS) { setMessage({ kind: "error", text: `A plate holds at most ${MAX_PARTS} parts` }); return; }
    setBusy(true); setMessage(null);
    try {
      const p = newPart(await api.upload(file));
      setBusy(false);
      setSelectedUid(p.uid);
      await arrangeAll([...parts, p]);
    } catch (e) { setMessage({ kind: "error", text: (e as Error).message }); setBusy(false); }
  };
  const addCopies = async (uid: string, count: number) => {
    const src = parts.find((p) => p.uid === uid);
    if (!src) return;
    const copies = Array.from({ length: Math.min(count, MAX_PARTS - parts.length) }, () => newPart(src.model, { ...src.transform }));
    await arrangeAll([...parts, ...copies]);
  };
  const removePart = (uid: string) => {
    const rest = parts.filter((p) => p.uid !== uid);
    setParts(rest);
    if (selectedUid === uid) setSelectedUid(rest[0]?.uid ?? null);
  };



  // My Parts: open a saved part on a new plate, or add it to the plate already here (2.17)
  useEffect(() => {
    if (!partRequest || !catalog) return;
    (async () => {
      try {
        const m = await api.modelInfo(partRequest.id);
        const p = newPart(m);
        setJob(null); setGcode(null); setMode("prepare");
        if (partRequest.add && parts.length) {
          if (parts.length >= MAX_PARTS) setMessage({ kind: "error", text: `A plate holds at most ${MAX_PARTS} parts` });
          else { setSelectedUid(p.uid); await arrangeAll([...parts, p]); }
        } else { setParts([p]); setSelectedUid(p.uid); setManualPrinter(""); setStep(1); }
        setMessage({ kind: "ok", text: `${m.name} ${partRequest.add && parts.length ? "added to the plate" : "opened"}` });
      } catch (e) { setMessage({ kind: "error", text: (e as Error).message }); }
      onPartLoaded();
    })();
  }, [partRequest, catalog]);   // eslint-disable-line react-hooks/exhaustive-deps

  // "Same settings again" from a job page: the same material, preset, goal and settings, and the same parts if their files still exist
  useEffect(() => {
    if (!reuseRequest || !catalog) return;
    const r = reuseRequest;
    (async () => {
      if (r.filament !== filament) keepSettingsOnce.current = true;
      criticalTouched.current = true;      // the job's own support choices win over the guided default
      setFilament(r.filament); setPreset(r.preset); setGoal(r.goal?.id ?? null);
      setSettings({ ...catalog.preset_defaults[r.preset], ...r.settings });
      setJob(null); setGcode(null); setMode("prepare");
      const usable = r.parts.filter((x) => x.available && x.model_id);
      try {
        const infos = await Promise.all(usable.map((x) => api.modelInfo(x.model_id!)));
        const next = infos.map((m, i) => newPart(m, { rotation: usable[i].rotation ?? [0, 0, 0], scale: usable[i].scale ?? 1, position: usable[i].position ?? [0, 0] }));
        setParts(next); setSelectedUid(next[0]?.uid ?? null);
      } catch { setParts([]); }
      const missing = r.parts.length - usable.length;
      setStep(usable.length && !missing ? 2 : 1);
      setMessage(missing ? { kind: "error", text: `${missing === r.parts.length ? "The file was" : `${missing} of the files were`} removed after not being used for a while. Upload ${missing === 1 ? "it" : "them"} again; the settings are kept.` }
        : { kind: "ok", text: `${r.job.name}: same settings loaded` });
      onReused();
    })();
  }, [reuseRequest, catalog]);   // eslint-disable-line react-hooks/exhaustive-deps   // eslint-disable-line react-hooks/exhaustive-deps

  const slice = async () => {
    if (!model) return;
    setBusy(true); setMessage(null);
    try {
      let j = await api.slice({ ...plateSpec(parts), filament, preset, settings, goal, printer_id: manualPrinter || undefined });
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

  const [lapse, setLapse] = useState(false);   // 2.8: record a timelapse of a print started from here
  const print = async () => {
    if (!job) return;
    setBusy(true);
    try {
      const r = await api.print(job.id, lapse);
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

  useEffect(() => { if (mode !== "prepare" || tool) setPickFace(false); }, [mode, tool]);

  // a new G-code starts with everything shown and no playback
  useEffect(() => { setHidden([]); setTravel(false); setProgress(null); setPlaying(false); }, [gcode]);
  useEffect(() => {
    if (!playing) return;
    let raf = 0, last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setProgress((p) => {
        const next = (p ?? 0) + dt / PLAY_SECONDS;
        if (next >= 1) { setPlaying(false); return 1; }
        return next;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  useEffect(() => { if (workflow === "guided" && step <= 2) setMode("prepare"); }, [step, workflow]);
  useEffect(() => { if (!parts.length) setStep(1); }, [parts.length]);

  const onLayerCount = useCallback((n: number) => { setLayerCount(n); setLayerRange([0, Math.max(0, n - 1)]); }, []);

  if (!catalog) return <div className="loading">{message?.text ?? "Loading…"}</div>;

  const currentSig = JSON.stringify([plateSpec(parts), filament, preset, settings, manualPrinter]);
  const dims = assignment?.dims ?? model?.dims;
  const ready = job?.state === "done";

  const modelBlock = (
    <>
        {parts.length > 0 && (
          <PartsPanel parts={parts} selectedUid={selectedPart?.uid ?? null} overlapping={overlapPairs} busy={busy} onSelect={setSelectedUid} onRemove={removePart}
            onAdd={() => addInput.current?.click()} onArrange={() => arrangeAll(parts)} onCopies={addCopies} />
        )}

        {model?.checks?.length ? (
          <div className="card checks" aria-label="Checks on the uploaded model">
            {model.checks.map((c) => <p key={c.text} className={c.level === "warn" ? "form-error" : "muted"}>{c.level === "warn" ? "Check: " : ""}{c.text}</p>)}
          </div>
        ) : null}

        {showOverhang && overhangPercent != null && mode === "prepare" && model && (
          <div className="card checks" role="status">
            <p className={overhangPercent >= 15 ? "form-error" : "muted"}>
              {overhangPercent < 0.5 ? "No faces need support at this orientation (estimate)."
                : `About ${Math.round(overhangPercent)}% of the surface will need support at this orientation (estimate, ${Number(settings.support_threshold_angle) || 45}° threshold, shown in red). The sliced preview shows the real supports.`}
            </p>
          </div>
        )}

    </>
  );
  const printerCard = (
    <>
        <div className="card">
          <div className="printer-head">
            <div className="printer-icon"><PrinterIcon size={34} /></div>
            <div>
              <strong>{chosenPrinter ? chosenPrinter.model : model ? "No eligible printer" : "Auto printer"}</strong>
              <div className="muted">{chosenPrinter ? `${chosenPrinter.name} · ${chosenPrinter.bed_type.replace(" Plate", "")}` : "Open a model to assign"}</div>
            </div>
            <div className="plate">{bed[0]}×{bed[1]}</div>
          </div>
          <select className="row-select" aria-label="Printer" value={manualPrinter} onChange={(e) => setManualPrinter(e.target.value)} disabled={!assignment}>
            <option value="">{!model ? "Auto: open a model first" : assignment?.chosen ? "Auto (recommended)" : "Auto: none eligible"}</option>
            {assignment?.candidates.map((c) => (
              <option key={c.printer_id} value={c.printer_id} disabled={!c.eligible}>
                {c.name} ({c.model}){c.eligible ? waitText(c.wait_s) : ` — ${c.blockers[0] ?? "not eligible"}`}
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

    </>
  );
  const filamentCard = (
    <>
        <div className="card">
          <div className="printer-head">
            <div className="spool" style={{ background: colour ?? chosenPrinter?.status.ams?.find((s) => s.slot === chosen?.loaded_slot)?.color ?? "#2f6bff" }} />
            <div>
              <strong>{filament}</strong>
              <div className="muted">{catalog.filaments[filament].nozzle}° / {catalog.filaments[filament].bed}°
                {catalog.filaments[filament].enclosed ? " · needs enclosure" : ""}</div>
            </div>
          </div>
          <select className="row-select" value={filament} onChange={(e) => setFilament(e.target.value)} aria-label="Material (loaded in the pool now)">
            {(loaded ?? []).map((m) => <option key={m.material} value={m.material}>{catalog.filaments[m.material]?.orca ?? m.material}</option>)}
          </select>
          <select className="row-select" value={colour ?? ""} onChange={(e) => setColour(e.target.value)} aria-label="Colour (loaded in the pool now)">
            {colours.map((c) => <option key={c.colour} value={c.colour}>{colourName(c.colour)} ({c.colour}) · {c.printers.join(", ")}</option>)}
          </select>
          {noFilament && noFilamentNote}
          <select className="row-select" value={nozzle} aria-label="Nozzle size" onChange={(e) => changeNozzle(Number(e.target.value))}
            title="Only nozzle sizes that your printers have are offered">
            {fleetNozzles.map((n) => <option key={n} value={n}>{n} mm nozzle</option>)}
          </select>
        </div>

    </>
  );
  const goalsBlock = (
    <>
        <div className="goals" role="group" aria-label="Print goal">
          {catalog.goals.map((g) => (
            <button key={g.id} className={"goal" + (goal === g.id ? " on" : "")} aria-pressed={goal === g.id} title={g.description}
              onClick={() => applyGoal(g.id)}>{g.name}</button>
          ))}
        </div>
        {goal && (
          <p className="goal-note muted">{catalog.goals.find((g) => g.id === goal)?.description}{goalNotes.length ? " " + goalNotes.join(" ") : ""}</p>
        )}

    </>
  );
  const fullSettings = (
    <>
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
    </>
  );

  const STEPS = ["Model", "Goal", "Review", "Submit"];
  const canStep = (n: number) => n === 1 || (n >= 2 && !!model);
  const stale = !!job && sliceSig !== currentSig;
  const sliceNow = () => { setSliceSig(currentSig); slice(); };
  const guidedPanel = (
    <>
      {step === 1 && (
        <>
          <div className="step-head"><span className="lbl">Step 1 of 4</span><h2>Add your model</h2><p className="muted">STL, 3MF, OBJ or STEP. Add more parts to share one plate.</p></div>
          {!parts.length && <button className="btn primary big" onClick={() => fileInput.current?.click()} disabled={busy}>{busy ? "Uploading…" : "Open a model"}</button>}
          {modelBlock}
        </>
      )}
      {step === 2 && (
        <>
          <div className="step-head"><span className="lbl">Step 2 of 4</span><h2>What do you need?</h2><p className="muted">Pick a goal and material. We set layer height, walls and infill for you.</p></div>
          {goalsBlock}
          <section className="support-ctl" aria-label="Material and nozzle">
            <div className="lbl">Material</div>
            <div className="chips" role="radiogroup" aria-label="Material">
              {(loaded ?? []).map((m) => (
                <button key={m.material} role="radio" aria-checked={filament === m.material} className={"chip" + (filament === m.material ? " on" : "")} onClick={() => setFilament(m.material)}>{m.material}</button>
              ))}
            </div>
            {loaded === null && <p className="muted">Checking what is loaded in the printers…</p>}
            {noFilament && noFilamentNote}
            {colours.length > 0 && <><div className="lbl">Colour</div>{colourPicker}
              <p className="muted small-text">Only what is loaded in the printers right now. Your print goes to a printer with this colour loaded.</p></>}
            <p className="muted">{catalog.filaments[filament].nozzle}° nozzle, {catalog.filaments[filament].bed}° bed{catalog.filaments[filament].enclosed ? ", needs an enclosed printer" : ""}</p>
            <div className="lbl">Nozzle</div>
            <div className="chips" role="radiogroup" aria-label="Nozzle size">
              {fleetNozzles.map((n) => (
                <button key={n} role="radio" aria-checked={nozzle === n} className={"chip" + (nozzle === n ? " on" : "")} onClick={() => changeNozzle(n)}>{n} mm</button>
              ))}
            </div>
            <p className="muted">Only sizes fitted to a connected printer are offered.</p>
          </section>
          <SupportControl settings={settings} onChange={(k, v) => { if (k === "support_critical_regions_only") criticalTouched.current = true; setSettings((st) => ({ ...st, [k]: v })); }} showOverhang={showOverhang}
            onToggleOverhang={() => setShowOverhang(!showOverhang)} overhangPercent={overhangPercent} />
          <button className="mini wide" onClick={() => setWorkflow("full")}>Open all settings</button>
        </>
      )}
      {step === 3 && (
        <>
          <div className="step-head"><span className="lbl">Step 3 of 4</span><h2>Check the result</h2><p className="muted">Slice it to see the real layers, time and supports before you send it.</p></div>
          {printerCard}
          <button className="btn primary big" disabled={!model || busy || overlapPairs.length > 0 || (!assignment?.chosen && !manualPrinter)} onClick={sliceNow}>
            <Play size={14} /> {busy && job?.state === "slicing" ? "SLICING…" : job && !stale ? "SLICE AGAIN" : "SLICE"}
          </button>
          {job && ready && (
            <dl className="review-facts">
              <dt>Printer</dt><dd>{job.printer_name ?? "—"}</dd>
              <dt>Time</dt><dd>{fmtTime(job.stats?.time_s)}</dd>
              <dt>Filament</dt><dd>{filament}{job.stats?.filament_g != null ? ` · ${job.stats.filament_g} g` : ""}</dd>
              <dt>Layers</dt><dd>{job.stats?.layers ?? "—"}</dd>
            </dl>
          )}
          {stale && <p className="form-error">You changed something since this slice. Slice again to refresh the preview.</p>}
          {showOverhang && overhangPercent != null && <p className="muted">Overhang estimate: about {Math.round(overhangPercent)}% needs support.</p>}
          <button className="mini wide" onClick={() => setWorkflow("full")}>Open all settings</button>
        </>
      )}
      {step === 4 && (
        <>
          <div className="step-head"><span className="lbl">Step 4 of 4</span><h2>Send it</h2><p className="muted">The pool picks the first free printer that fits. Nothing is queued until you press the button.</p></div>
          <dl className="review-facts">
            <dt>Part</dt><dd>{plateName}</dd>
            <dt>Material</dt><dd>{colour ? `${colourName(colour)} ` : ""}{filament}, {nozzle} mm nozzle</dd>
            <dt>Goal</dt><dd>{goal ? catalog.goals.find((g) => g.id === goal)?.name : "Custom settings"}</dd>
          </dl>
          <button className="btn primary big" disabled={!model || busy || overlapPairs.length > 0 || !colour} onClick={() => setModal("submit")}>SUBMIT TO POOL</button>
        </>
      )}
      <div className="step-nav">
        <button className="btn" disabled={step === 1} onClick={() => setStep(step - 1)}>Back</button>
        {step < 4 && <button className="btn primary" disabled={!canStep(step + 1)} onClick={() => setStep(step + 1)}>Continue</button>}
      </div>
    </>
  );

  return (
    <div className="app" onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) (parts.length ? addPart(f) : upload(f)); }}>
      <input ref={fileInput} type="file" hidden accept=".stl,.3mf,.obj,.step,.stp" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
      <input ref={addInput} type="file" hidden accept=".stl,.3mf,.obj,.step,.stp" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) addPart(f); }} />


      <header className="topbar">
        <span className="spacer" />
        <span className="muted engine-note">{catalog.slicer_mode === "mock" ? "Mock slicer: perimeters only, not real G-code" : `${catalog.engine} engine`}</span>
      </header>

      <aside className="panel">
        <div className="workflow-bar">
          <div className="seg view-toggle" role="radiogroup" aria-label="Settings view">
            <button role="radio" aria-checked={workflow === "guided"} className={workflow === "guided" ? "on" : ""} onClick={() => setWorkflow("guided")}>Guided steps</button>
            <button role="radio" aria-checked={workflow === "full"} className={workflow === "full" ? "on" : ""} onClick={() => setWorkflow("full")}>All settings</button>
          </div>
          {workflow === "guided" && (
            <div className="stepper" role="group" aria-label="Steps">
              {STEPS.map((label, i) => (
                <button key={label} className={"stp" + (step === i + 1 ? " on" : step > i + 1 ? " done" : "")} disabled={!canStep(i + 1)} aria-current={step === i + 1 ? "step" : undefined}
                  onClick={() => setStep(i + 1)}><i>{step > i + 1 ? "✓" : i + 1}</i><span>{label}</span></button>
              ))}
            </div>
          )}
        </div>
        {workflow === "full" ? (
          <>
        <div className="card select-card"><span className="engine">{catalog.engine}</span><ChevronDown size={18} /></div>

            {modelBlock}
            {printerCard}
            {filamentCard}
            {goalsBlock}
            {fullSettings}
          </>
        ) : guidedPanel}
      </aside>

      <main className="viewport">
        <Viewport parts={viewParts} selectedUid={selectedPart?.uid ?? null} onSelect={setSelectedUid} onOverlap={setOverlapPairs} bed={bed} gcode={gcode} mode={mode}
          layerRange={layerRange} onLayerCount={onLayerCount} fitSignal={fitSignal}
          pickFace={pickFace} onPickFace={(n) => { setPickFace(false); orientTo("face", n); }}
          overhangAngle={showOverhang && mode === "prepare" ? (Number(settings.support_threshold_angle) || 45) : null} onOverhang={setOverhangPercent}
          hiddenFeatures={hidden} showTravel={travel} progress={progress} onFeatures={setFeatures}
          moveMode={tool === "move" && mode === "prepare"}
          onMove={(uid, position) => setParts((ps) => ps.map((x) => (x.uid === uid ? { ...x, transform: { ...x.transform, position } } : x)))} />

        <div className="mode-toggle">
          <button className={mode === "prepare" ? "btn primary" : "btn"} onClick={() => setMode("prepare")}>PREPARE</button>
          <button className={mode === "preview" ? "btn primary" : "btn"} disabled={!gcode} onClick={() => setMode("preview")}>PREVIEW</button>
        </div>

        {!parts.length && (
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
            <button title="Lay flat: then click the face of the part that should lie on the bed" aria-pressed={pickFace} className={pickFace ? "on" : ""} disabled={busy}
              onClick={() => { setPickFace(!pickFace); if (!pickFace) setTool(null); }}><ArrowDownToLine size={20} /></button>
            <button title="Auto-orient: turn the part to need the least support" disabled={busy} onClick={() => orientTo("auto")}><Sparkles size={20} /></button>
            <button title="Show the faces that will need support (an estimate)" aria-pressed={showOverhang} className={showOverhang ? "on" : ""}
              onClick={() => setShowOverhang(!showOverhang)}><AlertTriangle size={20} /></button>
            <button title="Reset view" onClick={() => setFitSignal((n) => n + 1)}><Maximize size={20} /></button>
            {pickFace && <div className="popover"><p className="hint">Click the face of the part that should lie on the bed.</p></div>}
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
                  <button className="mini" disabled={busy || (parts.length === 1 && !transform.position[0] && !transform.position[1])}
                    title={parts.length > 1 ? "Let the slicer lay out every part" : "Let the slicer place the part (the middle of the bed)"}
                    onClick={() => { if (parts.length > 1) arrangeAll(parts); else { setTransform({ ...transform, position: [0, 0] }); setMessage({ kind: "ok", text: "The slicer will place the part" }); } }}>Auto-arrange{parts.length > 1 ? " all" : ""}</button>
                </div>
                <p className="hint">Drag a part on the bed, or type a position for the selected one. 0, 0 is the middle of the bed; parts stay where you put them and stop at their neighbours.</p>
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
                  <button className="mini" disabled={busy} onClick={() => setTransform({ ...transform, rotation: [0, 0, 0] })}>Reset</button>
                </div>
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

        {mode === "preview" && gcode && features.length > 0 && (
          <div className="gcode-panel" role="group" aria-label="G-code view">
            <div className="playback">
              <button className="mini" aria-label={playing ? "Pause playback" : "Play the print"} title={playing ? "Pause" : "Play the print layer by layer"}
                onClick={() => { if (playing) { setPlaying(false); } else { if (progress == null || progress >= 1) setProgress(0); setPlaying(true); } }}>
                {playing ? <Pause size={14} /> : <Play size={14} />}
              </button>
              <input type="range" min={0} max={1000} value={Math.round((progress ?? 1) * 1000)} aria-label="Playback position"
                onChange={(e) => { setPlaying(false); setProgress(Number(e.target.value) / 1000); }} />
              <button className="mini" disabled={progress == null} onClick={() => { setPlaying(false); setProgress(null); }}>All</button>
            </div>
            <ul className="legend">
              {features.filter((f) => f.name !== TRAVEL).map((f) => (
                <li key={f.name}>
                  <label>
                    <input type="checkbox" checked={!hidden.includes(f.name)}
                      onChange={(e) => setHidden(e.target.checked ? hidden.filter((h) => h !== f.name) : [...hidden, f.name])} />
                    <i style={{ background: hex(f.color) }} />{f.name}
                  </label>
                </li>
              ))}
              {features.some((f) => f.name === TRAVEL) && (
                <li><label><input type="checkbox" checked={travel} onChange={(e) => setTravel(e.target.checked)} /><i style={{ background: hex(features.find((f) => f.name === TRAVEL)!.color) }} />Travel moves</label></li>
              )}
            </ul>
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
          <span className="filename">{plateName}</span>
          <a className={"btn" + (ready ? "" : " disabled")} href={ready ? gcodeUrl(job!.id) : undefined} aria-label="Download G-code" title="Download G-code"><Download size={16} /></a>
          {workflow === "full" && <>
            <button className="btn primary" disabled={!model || busy || overlapPairs.length > 0 || !assignment?.chosen && !manualPrinter} onClick={slice}>
              <Play size={14} /> {busy && job?.state === "slicing" ? "SLICING…" : "SLICE"}
            </button>
            <button className="btn" disabled={!model || busy || overlapPairs.length > 0 || !colour} onClick={() => setModal("submit")} title="Let the system choose a printer and when">SUBMIT TO POOL</button>
            <label className="lapse-toggle" title="Record a timelapse of this print"><input type="checkbox" checked={lapse} onChange={(e) => setLapse(e.target.checked)} disabled={!ready || job?.printed} /> Timelapse</label>
            <button className="btn go" disabled={!ready || busy || job?.printed} onClick={print}>{job?.printed ? "SENT" : "PRINT"}</button>
          </>}
        </div>
      </main>

      {modal === "submit" && model && <SubmitModal user={user} spec={{ ...plateSpec(parts), filament, preset, settings, goal, colour }} onClose={() => setModal(null)}
        onDone={(j) => {
          setModal(null); setMessage({ kind: "ok", text: j.phase === "awaiting_approval" ? `${j.name} needs approval because ${j.approval?.reasons.join(" and ")}. A group operator has been told; it joins the pool once approved. Follow it under Jobs.` : `${j.name} is in the print pool. Follow it under Jobs.` }); }} />}
    </div>
  );
}
