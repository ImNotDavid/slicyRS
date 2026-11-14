import { useCallback, useEffect, useState } from "react";
import { api, type ApprovalCard } from "../api";
import { fmtTime } from "../format";
import { Harry } from "./Brand";

const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");

/** Approvals (3.3): prints in your groups that wait for an operator before reaching a printer. */
export default function ApprovalsPage({ onOpenJob }: { onOpenJob: (id: string) => void }) {
  const [cards, setCards] = useState<ApprovalCard[] | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const load = useCallback(() => api.approvals().then(setCards).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setErr(""); setMsg("");
    try { await fn(); setMsg(ok); setRejecting(null); setNote(""); load(); } catch (e) { setErr((e as Error).message); }
  };
  if (!cards) return err ? <p className="form-error">{err}</p> : <p className="muted">Loading…</p>;
  return (
    <div className="page-body">
      <p className="muted">These prints wait for you before they join the pool. Approved prints keep their place in the queue from when they were sent.</p>
      {msg && <p className="ok-note" role="status">{msg}</p>}{err && <p className="form-error" role="alert">{err}</p>}
      {!cards.length && <div className="empty-state"><Harry height={44} /><p>Nothing is waiting for approval.</p></div>}
      <section className="jgrid" aria-label="Waiting for approval">
        {cards.map((c) => (
          <article key={c.id} className="jc approval-card">
            <div className="thumb"><img src={`/api/jobs/${c.id}/thumbnail.png`} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.display = "none"; }} /></div>
            <div className="body">
              <button className="job-link name" onClick={() => onOpenJob(c.id)} title={c.name}>{c.name}</button>
              <div className="meta"><span>{c.owner}</span><span>{c.group}</span></div>
              <div className="meta"><span className="mono">{fmtTime(c.time_s)}</span>{c.filament_g != null && <span>{Math.round(c.filament_g)} g</span>}<span>{c.filament} · {c.nozzle_mm} mm</span></div>
              <div className="meta"><span>Sent {when(c.submitted_at)}</span>{c.pickup_by && <span>Pickup by {when(c.pickup_by)}</span>}</div>
              <p className="warn-note">Needs approval because {c.reasons.join(" and ")}.</p>
              {rejecting === c.id ? (
                <form className="upkeep-form" onSubmit={(e) => { e.preventDefault(); if (note.trim()) act(() => api.reject(c.id, note.trim()), `${c.name} rejected; ${c.owner} has been told why.`); }}>
                  <textarea autoFocus rows={2} maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why? The owner sees this, so say what to change." aria-label={`Why reject ${c.name}`} />
                  <div className="live-controls"><button className="btn danger" disabled={!note.trim()}>Reject</button><button type="button" className="btn" onClick={() => { setRejecting(null); setNote(""); }}>Cancel</button></div>
                </form>
              ) : (
                <div className="live-controls">
                  <button className="btn primary" onClick={() => act(() => api.approve(c.id), `${c.name} approved and in the pool.`)}>Approve</button>
                  <button className="btn" onClick={() => { setRejecting(c.id); setNote(""); }}>Reject…</button>
                </div>
              )}
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
