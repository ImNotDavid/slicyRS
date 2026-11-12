import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api, type Forecast, type Job, type PlateSpec } from "../api";
import { fmtTime } from "./JobsModal";

type Props = {
  spec: PlateSpec & { filament: string; preset: string; settings: Record<string, unknown> };
  onClose: () => void; onDone: (j: Job) => void;
};

const local = (t: number) => { const d = new Date(t * 1000 - new Date().getTimezoneOffset() * 60000); return d.toISOString().slice(0, 16); };
const fmt = (t: number) => new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" });

/** Send a part to the shared pool: a printer-independent preview first, then an optional pickup time and automatic start. */
export default function SubmitModal({ spec, onClose, onDone }: Props) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [by, setBy] = useState("");
  const [auto, setAuto] = useState(false);
  const [share, setShare] = useState(false);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [earliest, setEarliest] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        let j = await api.draft(spec);
        setJob(j);
        while (live && (j.phase === "submitted" || j.phase === "previewing")) {
          await new Promise((r) => setTimeout(r, 700));
          j = await api.job(j.id);
        }
        if (!live) return;
        if (j.phase === "failed") throw new Error(j.error ?? "The preview slice failed");
        setJob(j);
        setForecast(await api.forecast(j.id));
      } catch (e) { if (live) setError((e as Error).message); }
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ready = job?.phase === "previewed";
  const pickup = by ? Math.floor(new Date(by).getTime() / 1000) : undefined;

  const submit = async () => {
    if (!job) return;
    setBusy(true); setError(""); setEarliest(null);
    try { onDone(await api.submit(job.id, { pickup_by: pickup ?? null, auto_start: auto, allow_batching: share })); }
    catch (e) { setError((e as Error).message); setEarliest((e as { earliest?: number }).earliest ?? null); }
    setBusy(false);
  };
  const close = () => { if (job && job.phase === "previewed") api.discard(job.id).catch(() => undefined); onClose(); };

  return (
    <div className="modal-back" onClick={close}>
      <div className="modal" role="dialog" aria-label="Submit to the print pool" onClick={(e) => e.stopPropagation()}>
        <header><h3>Submit to the print pool</h3><button className="icon" onClick={close} aria-label="Close"><X size={18} /></button></header>
        <div className="modal-body">
          {!job && !error && <p className="muted">Starting the preview…</p>}
          {job && !ready && !error && <p className="muted">Slicing a preview to estimate time and filament…</p>}
          {ready && (
            <dl className="submit-facts">
              <dt>Part</dt><dd>{job!.name}</dd>
              <dt>Estimated print time</dt><dd>{fmtTime(job!.preview?.time_s)}</dd>
              <dt>Filament</dt><dd>{job!.filament}{job!.preview?.filament_g != null ? ` · ${job!.preview.filament_g} g` : ""}</dd>
              <dt>Expected ready</dt>
              <dd>{forecast?.feasible && forecast.earliest_finish ? `${fmt(forecast.earliest_finish)}${forecast.jobs_ahead ? ` (${forecast.jobs_ahead} ahead of you)` : ""}` : "no printer can print this"}</dd>
            </dl>
          )}
          {ready && (
            <>
              <label className="field-row">Pickup by (optional)
                <input type="datetime-local" value={by} min={local(Date.now() / 1000)} onChange={(e) => { setBy(e.target.value); setError(""); setEarliest(null); }} />
              </label>
              <label className="check"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Start printing automatically when a printer is ready</label>
              <label className="check"><input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} /> Allow my part to share a plate with other people's parts</label>
              {share && <p className="muted">Only parts in the same filament and layer height can share; each part keeps its own wall, infill, speed and support settings. The job may wait for a partner (never past your pickup time, and at most the admin-set maximum), and if one part on a shared plate fails, the others can be affected. Any one owner can clear the plate afterwards.</p>}
              <p className="muted">The system picks the printer. Leave the time empty if you do not mind when it is done.</p>
            </>
          )}
          {error && <p className="form-error" role="alert">{error}</p>}
          {earliest && <button className="btn" onClick={() => { setBy(local(Math.ceil(earliest / 60) * 60 + 300)); setError(""); setEarliest(null); }}>Use the earliest likely time ({fmt(earliest)})</button>}
          <div className="row-actions">
            <button className="btn primary" disabled={!ready || busy} onClick={submit}>Submit</button>
            <button className="btn" onClick={close}>Cancel</button>
          </div>
        </div>
      </div>
    </div>
  );
}
