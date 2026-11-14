import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, Megaphone, ShieldCheck, Trash2 } from "lucide-react";
import { api, type HomeData, type QuotaPart, type QuotaStatus, type User } from "../api";
import TermsDialog from "./TermsDialog";
import LiveProgress from "./LiveProgress";
import { Harry } from "./Brand";

const minutes = (s: number | null) => (s == null ? "—" : s < 600 ? "under 10 min" : s < 3600 ? `about ${Math.round(s / 600) * 10} min` : `about ${Math.round(s / 1800) / 2} h`);

/** Home (2.18): where everyone lands. Members: their jobs, how busy the pool is, notices. Staff also: fleet numbers. */
export default function HomePage({ user, go, onOpenJob }: { user: User; go: (page: "workspace" | "jobs" | "fleet" | "queue") => void; onOpenJob: (id: string) => void }) {
  const [h, setH] = useState<HomeData | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [level, setLevel] = useState<"info" | "warning">("info");
  const [quotas, setQuotas] = useState<QuotaStatus[]>([]);
  const [showTerms, setShowTerms] = useState(false);
  const load = () => { api.home().then(setH).catch((e) => setErr(e.message)); api.myQuota().then(setQuotas).catch(() => {}); };
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);
  if (err) return <p className="form-error">{err}</p>;
  if (!h) return <p className="muted">Loading…</p>;
  const isAdmin = user.role === "admin";
  const nothing = !h.mine.printing.length && !h.mine.waiting.length && !h.mine.to_collect.length;

  return (
    <div className="page-body home">
      {h.notices.map((n) => (
        <div key={n.id} className={"notice " + n.level} role="status">
          {n.level === "warning" ? <AlertTriangle size={18} /> : <Megaphone size={18} />}<span>{n.text}</span>
          {isAdmin && <button className="icon" aria-label="Remove notice" onClick={() => api.deleteNotice(n.id).then(load)}><Trash2 size={14} /></button>}
        </div>
      ))}

      {user.terms_needed && (
        <div className="notice warning" role="status"><ShieldCheck size={18} /><span>Read and accept the terms and safety rules before your next print.</span>
          <button className="btn" onClick={() => setShowTerms(true)}>Read them</button></div>
      )}
      {showTerms && <TermsDialog onAccepted={() => setShowTerms(false)} onClose={() => setShowTerms(false)} />}

      <button className="start-print" onClick={() => go("workspace")}>
        <span className="sp-text"><b>Start a print</b><span>Open a model, pick a goal and send it to the pool</span></span>
        <ArrowRight size={34} aria-hidden="true" />
      </button>

      <section className="stats-strip" aria-label="The print pool">
        <div className="stat"><span className="lbl">Printers free now</span><b>{h.pool.printers_free} of {h.pool.printers}</b><span>{h.pool.printers_out ? `${h.pool.printers_out} out of service` : "all in service"}</span></div>
        <div className="stat"><span className="lbl">Jobs waiting</span><b>{h.pool.jobs_waiting}</b><span>in the pool</span></div>
        <div className="stat"><span className="lbl">Wait for a 1 h print</span><b className="small">{h.pool.typical_wait_s === 0 ? "none" : minutes(h.pool.typical_wait_s)}</b><span>{h.pool.typical_wait_s === 0 ? "a printer is free now" : "before it starts, roughly"}</span></div>
      </section>

      <div className="home-grid">
        <section className="job-card" aria-label="My jobs">
          <div className="card-head"><h3>My jobs</h3><button className="chip" onClick={() => go("jobs")}>All my jobs</button></div>
          {nothing && <div className="empty-state small-empty"><Harry height={40} /><p>Nothing on the go. <button className="job-link" onClick={() => go("workspace")}>Start in the Workspace</button>.</p></div>}
          {h.mine.to_collect.map((j) => (
            <button key={j.id} className="home-row collect" onClick={() => onOpenJob(j.id)}><span className="tg tg-y">Collect</span><b>{j.name}</b><span className="muted">{j.printer_name}: collect it and confirm the plate is clear</span></button>
          ))}
          {h.mine.printing.map((j) => (
            <button key={j.id} className="home-row" onClick={() => onOpenJob(j.id)}><span className="tg">Printing</span><b>{j.name}</b>
              {j.live ? <LiveProgress live={j.live} /> : <span className="muted">on {j.printer_name}</span>}</button>
          ))}
          {h.mine.waiting.map((j) => (
            <button key={j.id} className="home-row" onClick={() => onOpenJob(j.id)}><span className="tg">{j.phase === "ready" ? "Starting" : "Waiting"}</span><b>{j.name}</b><span className="muted">{j.waiting_for ?? ""}</span></button>
          ))}
          {h.mine.recent.length > 0 && <><h4>Recently finished</h4>{h.mine.recent.map((j) => (
            <button key={j.id} className="home-row quiet" onClick={() => onOpenJob(j.id)}><span className="tg">{j.phase === "printed" ? "Printed" : j.phase === "failed" ? "Failed" : "Ended"}</span><b>{j.name}</b></button>
          ))}</>}
        </section>

        {quotas.some((q) => q.hours || q.grams) && (
          <section className="job-card" aria-label="My allowance">
            <div className="card-head"><h3>My allowance</h3></div>
            {quotas.filter((q) => q.hours || q.grams).map((q) => (
              <div key={q.group_id} className="allowance">
                <b>{q.group}</b> <span className="muted small-text">{q.period?.name}, until {new Date((q.period?.end ?? 0) * 1000).toLocaleDateString([], { day: "numeric", month: "short" })}</span>
                {q.hours && <Allowance part={q.hours} label="Print time" fmt={(v) => `${v.toFixed(1)} h`} />}
                {q.grams && <Allowance part={q.grams} label="Filament" fmt={(v) => `${Math.round(v)} g`} />}
              </div>
            ))}
            <p className="muted small-text">Prints count against the group they are for. A print that would go over is refused; a group operator can add more.</p>
          </section>
        )}

        {h.fleet && (
          <section className="job-card staff-card" aria-label="Fleet">
            <div className="card-head"><h3>Fleet <span className="staff-tag">Staff</span></h3><button className="chip" onClick={() => go("queue")}>Queue</button></div>
            <div className="fleet-states">{Object.entries(h.fleet.by_state).map(([k, n]) => <span key={k} className="tg">{k.toLowerCase()} {n}</span>)}</div>
            {h.fleet.needs_attention.map((p) => <button key={p.id} className="home-row collect" onClick={() => go("fleet")}><span className="tg tg-y">Attention</span><b>{p.name}</b><span className="muted">{p.why}</span></button>)}
            {h.fleet.plates_to_clear.map((p) => <button key={p.id} className="home-row" onClick={() => go("fleet")}><span className="tg">Plate</span><b>{p.name}</b><span className="muted">waiting to be cleared</span></button>)}
            {h.fleet.maintenance.map((p) => <button key={p.id} className="home-row quiet" onClick={() => go("fleet")}><span className="tg">Maintenance</span><b>{p.name}</b><span className="muted">{p.note}</span></button>)}
            {(h.fleet.flags ?? []).map((f, i) => <button key={i} className="home-row collect" onClick={() => go("fleet")}><span className="tg tg-y">Pattern</span><b>{f.what}</b><span className="muted">{f.text}</span></button>)}
            {h.fleet.at_risk > 0 && <p className="muted small-text">{h.fleet.at_risk} job{h.fleet.at_risk > 1 ? "s" : ""} may miss the pickup time.</p>}
            {!h.fleet.needs_attention.length && !h.fleet.plates_to_clear.length && !h.fleet.maintenance.length && <p className="muted">Nothing needs you right now.</p>}
          </section>
        )}
      </div>
      {isAdmin && (
        <form className="notice-form" onSubmit={(e) => { e.preventDefault(); if (note.trim()) api.addNotice(note.trim(), level).then(() => { setNote(""); load(); }); }}>
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Admins: post a notice for everyone (for example: closed on Friday)" aria-label="New notice" />
          <select value={level} onChange={(e) => setLevel(e.target.value as "info" | "warning")} aria-label="Notice type"><option value="info">Info</option><option value="warning">Warning</option></select>
          <button className="btn" disabled={!note.trim()}>Post</button>
        </form>
      )}
    </div>
  );
}

function Allowance({ part, label, fmt }: { part: NonNullable<QuotaPart>; label: string; fmt: (v: number) => string }) {
  const pct = Math.min(100, Math.round(part.share * 100));
  return (
    <div className="allow-row">
      <div className="allow-head"><span>{label}</span><span className={part.warn ? "warn-note" : "muted small-text"}>{fmt(part.used)} of {fmt(part.allowance)} used{part.warn ? ` (${pct}%)` : ""}</span></div>
      <div className="meter" role="img" aria-label={`${label}: ${pct} percent used`}><div style={{ width: `${pct}%` }} className={part.share >= 1 ? "bad" : part.warn ? "warn" : ""} /></div>
    </div>
  );
}
