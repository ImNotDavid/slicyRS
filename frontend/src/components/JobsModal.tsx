import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api, type Job } from "../api";

export const fmtTime = (s?: number) => {
  if (s == null) return "—";
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
};

export default function JobsModal({ onClose, onOpen, showOwner }: { onClose: () => void; onOpen: (j: Job) => void; showOwner?: boolean }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  useEffect(() => { api.jobs().then(setJobs); }, []);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header><h3>Jobs</h3><button className="icon" onClick={onClose} aria-label="Close"><X size={18} /></button></header>
        <div className="modal-body">
          {!jobs.length && <p className="muted">No jobs yet.</p>}
          {jobs.map((j) => (
            <div className="fleet-row" key={j.id}>
              <div className="fleet-main">
                <strong>{j.name}</strong>
                <span className="muted">{j.printer_name} · {j.filament} · {j.preset}{showOwner && j.owner_email ? ` · ${j.owner_email}` : ""}</span>
              </div>
              <span className={"pill " + (j.result ? (j.result.outcome === "finished" ? "done" : "failed") : j.state)}>
                {j.result ? (j.result.outcome === "finished" ? "printed" : j.result.layer != null && j.result.total_layers ? `failed at layer ${j.result.layer}/${j.result.total_layers}` : "failed")
                  : j.printed ? "printing / sent" : j.queued ? "queued" : j.state}
              </span>
              <span className="muted">{fmtTime(j.stats?.time_s)}</span>
              {j.state === "done" && !j.files_deleted_at && <button className="btn" onClick={() => onOpen(j)}>Open</button>}
              {j.files_deleted_at && <span className="muted" title="Files are removed after a retention period; slice the part again">files removed</span>}
              {j.result && <p className={"result-note" + (j.result.outcome === "failed" ? " bad" : "")}>{j.result.summary}</p>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
