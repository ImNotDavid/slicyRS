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
              <span className={"pill " + j.state}>{j.printed ? "sent" : j.queued ? "queued" : j.state}</span>
              <span className="muted">{fmtTime(j.stats?.time_s)}</span>
              {j.state === "done" && <button className="btn" onClick={() => onOpen(j)}>Open</button>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
