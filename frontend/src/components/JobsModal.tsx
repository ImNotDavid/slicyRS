import { useEffect, useState } from "react";
import Frame from "./Frame";
import PlateCheck from "./PlateCheck";
import { Harry } from "./Brand";
import { api, type Job, type JobEvent } from "../api";

export const fmtTime = (s?: number) => {
  if (s == null) return "—";
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
};

export default function JobsModal({ onClose, onOpen, showOwner, inline }: { onClose?: () => void; onOpen: (j: Job) => void; showOwner?: boolean; inline?: boolean }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [clearing, setClearing] = useState<string | null>(null);
  const [history, setHistory] = useState<{ id: string; events: JobEvent[] } | null>(null);
  const load = () => api.jobs().then(setJobs);
  useEffect(() => { load(); }, []);
  const WAITING: Record<string, string> = { submitted: "preparing", previewing: "previewing", previewed: "not submitted", pooled: "waiting for a printer", assigned: "printer chosen", slicing: "slicing", ready: "ready to print", printing: "printing", cancelled: "cancelled", rejected: "rejected" };
  const CANCELLABLE = ["submitted", "previewing", "previewed", "pooled", "assigned", "slicing", "ready"];
  const when = (t: number) => new Date(t * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const label = (j: Job) => j.result ? "" : j.phase && j.phase !== "failed" && WAITING[j.phase] && !(j.phase === "printing" && j.printed) ? WAITING[j.phase] : "";
  return (
    <Frame title="Jobs" inline={inline} onClose={onClose}>
          {!jobs.length && <div className="empty-state"><Harry height={48} /><p>No jobs yet. Start in the Workspace and your prints will show up here.</p></div>}
          {jobs.map((j) => (
            <div className="fleet-row" key={j.id}>
              <div className="fleet-main">
                <strong>{j.name}</strong>
                <span className="muted">{j.printer_name ?? "printer not chosen yet"} · {j.filament} · {j.preset}{showOwner && j.owner_email ? ` · ${j.owner_email}` : ""}{j.pickup_by ? ` · pickup by ${when(j.pickup_by)}` : ""}</span>
                {(j.members?.length || j.batch_lead) && j.phase !== "pooled" ? <span className="muted">Shares a plate{j.members?.length ? ` with ${j.members.length} other part${j.members.length > 1 ? "s" : ""}` : " with another job"}</span> : null}
                {j.phase === "pooled" && j.waiting_for && <span className="muted">{j.waiting_for}</span>}
                {j.at_risk &&<span className="muted bad-text">may miss its pickup time</span>}
              </div>
              <span className={"pill " + (j.result ? (j.result.outcome === "finished" ? "done" : j.result.outcome === "unknown" ? "queued" : "failed") : j.state)}>
                {j.result ? (j.result.outcome === "finished" ? "printed" : j.result.outcome === "unknown" ? "sent, result unknown"
                  : j.result.layer != null && j.result.total_layers ? `failed at layer ${j.result.layer}/${j.result.total_layers}` : "failed")
                  : label(j) || (j.printed ? "printing / sent" : j.queued ? "queued" : j.state)}
              </span>
              <span className="muted">{fmtTime(j.stats?.time_s ?? j.preview?.time_s)}</span>
              {j.phase && CANCELLABLE.includes(j.phase) && <button className="btn" onClick={() => api.cancel(j.id).then(load)}>Cancel</button>}
              {j.plate_to_clear && j.printer_id && <button className="btn primary" onClick={() => setClearing(clearing === j.id ? null : j.id)}>Confirm plate is clear</button>}
              <button className="btn" onClick={async () => setHistory(history?.id === j.id ? null : { id: j.id, events: await api.events(j.id) })}>History</button>
              {j.state === "done" && !j.files_deleted_at && <button className="btn" onClick={() => onOpen(j)}>Open</button>}
              {j.files_deleted_at && <span className="muted" title="Files are removed after a retention period; slice the part again">files removed</span>}
              {history?.id === j.id && (
                <ul className="result-note history">
                  {history.events.map((e) => <li key={e.id}>{when(e.at)} · {e.kind === "phase" ? `${e.data.from ?? "new"} → ${e.data.to}${e.data.reason ? ` (${e.data.reason})` : ""}` : e.kind.replace(/_/g, " ")}</li>)}
                </ul>
              )}
              {clearing === j.id && j.printer_id && <PlateCheck printerId={j.printer_id} onDone={() => { setClearing(null); load(); }} />}
              {j.error && <p className="result-note bad">{j.error}</p>}
              {j.result && <p className={"result-note" + (j.result.outcome === "failed" ? " bad" : "")}>{j.result.summary}</p>}
            </div>
          ))}
    </Frame>
  );
}
