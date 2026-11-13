import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { api, type QueueCard, type QueueLane } from "../api";
import { fmtTime } from "../format";
import LiveProgress from "./LiveProgress";

const when = (t: number) => new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
const waitText = (s?: number | null) => (s == null ? "" : s < 300 ? "free now" : `free in about ${s >= 3600 ? `${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}` : `${Math.round(s / 60)} min`}`);

/** Staff queue (2.2): the pool and every printer's line as columns of job cards; drag to reorder or to line a job up for a printer. */
export default function QueuePage({ onOpenJob }: { onOpenJob: (id: string) => void }) {
  const [lanes, setLanes] = useState<QueueLane[] | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [drag, setDrag] = useState<QueueCard | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const dragging = useRef(false);
  const load = useCallback(() => api.queueBoard().then((q) => setLanes(q.lanes)).catch((e) => setErr(e.message)), []);
  useEffect(() => {
    load();
    const t = setInterval(() => { if (!dragging.current) load(); }, 5000);
    return () => clearInterval(t);
  }, [load]);
  useEffect(() => { if (!msg) return; const t = setTimeout(() => setMsg(""), 5000); return () => clearTimeout(t); }, [msg]);

  const move = async (card: QueueCard, lane: string, index: number) => {
    const target = lanes?.find((l) => l.id === lane);
    const why = lane !== "pool" ? card.fits[lane] : null;
    if (why) { setMsg(`${card.name} cannot go to ${target?.name}: ${why}.`); return; }
    try {
      const r = await api.queueMove(card.id, lane, index);
      setMsg(`${card.name} moved to ${lane === "pool" ? "the pool" : target?.name}, position ${r.position}.`);
    } catch (e) { setMsg((e as Error).message); }
    load();
  };
  const drop = (e: DragEvent<HTMLDivElement>, lane: string) => {
    e.preventDefault();
    dragging.current = false; setOver(null);
    if (!drag) return;
    const cards = [...e.currentTarget.querySelectorAll<HTMLElement>(".qc.movable")].filter((el) => el.dataset.id !== drag.id);
    const index = cards.filter((el) => { const r = el.getBoundingClientRect(); return r.top + r.height / 2 < e.clientY; }).length;
    const card = drag;
    setDrag(null);
    move(card, lane, index);
  };

  if (err) return <p className="form-error">{err}</p>;
  if (!lanes) return <p className="muted">Loading…</p>;
  const pool = lanes.find((l) => l.id === "pool")!;
  const printers = lanes.filter((l) => l.id !== "pool");
  const waiting = lanes.reduce((n, l) => n + l.jobs.length, 0);
  const printing = printers.filter((l) => l.now).length;

  const card = (c: QueueCard, lane: QueueLane, i: number) => {
    const fitsHere = printers.filter((p) => !c.fits[p.id] && p.id !== lane.id);
    return (
      <div key={c.id} data-id={c.id} className={"qc movable" + (drag?.id === c.id ? " drag" : "") + (c.at_risk ? " risk" : "")} draggable
        onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", c.id); dragging.current = true; setDrag(c); }}
        onDragEnd={() => { dragging.current = false; setDrag(null); setOver(null); }}>
        <div className="row"><span className="n">{lane.id === "pool" ? `#${i + 1} in the pool` : `Next ${i + 1}`}</span>
          <div className="mv">
            <button aria-label={`Move ${c.name} up`} disabled={i === 0} onClick={() => move(c, lane.id, i - 1)}><ArrowUp size={12} /></button>
            <button aria-label={`Move ${c.name} down`} disabled={i === lane.jobs.length - 1} onClick={() => move(c, lane.id, i + 1)}><ArrowDown size={12} /></button>
            {lane.id !== "pool" && <button aria-label={`Put ${c.name} back in the pool`} onClick={() => move(c, "pool", pool.jobs.length)}><X size={12} /></button>}
          </div></div>
        <button className="job-link nm" onClick={() => onOpenJob(c.id)}>{c.name}</button>
        <div className="row"><span className="muted">{c.owner}</span><span className="mono">{fmtTime(c.estimate_s)}</span></div>
        <div className="tags">
          <span className="tg">{c.filament}</span><span className="tg">{c.nozzle_mm} mm</span>
          {c.enclosed && <span className="tg">Enclosed</span>}{c.shared && <span className="tg">Shared plate</span>}
          {lane.id !== "pool" && <span className="tg tg-pin">Lined up</span>}
          {c.pickup_by && <span className={"tg" + (c.at_risk ? " tg-y" : "")}>Pickup {when(c.pickup_by)}</span>}
        </div>
        {c.waiting_for && <div className="muted small-text">{c.waiting_for}</div>}
        <select className="qc-send" aria-label={`Line ${c.name} up for a printer`} value="" onChange={(e) => e.target.value && move(c, e.target.value, 0)}>
          <option value="">{lane.id === "pool" ? "Line up for…" : "Move to…"}</option>
          {fitsHere.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          {lane.id !== "pool" && <option value="pool">Back to the pool</option>}
        </select>
      </div>
    );
  };

  const column = (lane: QueueLane) => {
    const why = drag && lane.id !== "pool" ? drag.fits[lane.id] : null;
    const isPool = lane.id === "pool";
    return (
      <section key={lane.id} className={"qcol" + (isPool ? " qcol-pool" : "")} aria-label={isPool ? "Pool" : lane.name}>
        <div className="qhead">
          <div className="t"><h2>{isPool ? "Pool" : lane.name}</h2>
            <span className={"pill " + (isPool ? "pooled" : lane.unavailable ? "offline" : (lane.state ?? "offline").toLowerCase())}>{isPool ? `${lane.jobs.length} waiting` : lane.unavailable ? "Out of service" : lane.state ?? "unknown"}</span></div>
          <div className="sub">{isPool ? "Goes to the first free printer that fits, in this order." :
            `${lane.model} · ${lane.nozzle_mm} mm · ${lane.unavailable ?? (lane.bed === "occupied" ? "plate needs clearing" : waitText(lane.wait_s) || "")}`}</div>
        </div>
        <div className={"qlist" + (over === lane.id ? (why ? " bad" : " over") : "")}
          onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = why ? "none" : "move"; if (over !== lane.id) setOver(lane.id); }}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(null); }}
          onDrop={(e) => drop(e, lane.id)}>
          {lane.now && (
            <div className="qc now"><div className="row"><span className="n">Printing now</span><span className="lbl">locked</span></div>
              <button className="job-link nm" onClick={() => onOpenJob(lane.now!.id)}>{lane.now.name}</button>
              <div className="muted">{lane.now.owner}</div>
              {lane.now.live && <LiveProgress live={lane.now.live} />}</div>
          )}
          {(lane.coming ?? []).map((c) => (
            <div key={c.id} className="qc now coming"><div className="row"><span className="n">{c.phase === "ready" ? "Sliced, starting next" : "On its way"}</span><span className="lbl">locked</span></div>
              <button className="job-link nm" onClick={() => onOpenJob(c.id)}>{c.name}</button><div className="muted">{c.owner}</div></div>
          ))}
          {lane.jobs.map((c, i) => card(c, lane, i))}
          {over === lane.id && why && <div className="drop-hint bad">Cannot go here: {why}</div>}
          {!lane.jobs.length && !lane.now && !(lane.coming ?? []).length && !(over === lane.id && why) &&
            <div className="drop-hint">{isPool ? "Nothing waiting" : "Drop a job here to line it up for this printer"}</div>}
        </div>
      </section>
    );
  };

  return (
    <div className="page-body queue-page">
      <div className="queue-top">
        <p className="muted">{waiting} waiting · {printing} printing · {printers.filter((p) => p.unavailable).length} out of service</p>
        <button className="btn" onClick={async () => { if (confirm("Put every job back in the automatic order and remove all printer line-ups?")) { await api.queueReset(); setMsg("Back to the automatic order."); load(); } }}>Let the pool decide</button>
      </div>
      <p className="muted small-text">Drag a card to change the order, or onto a printer to line it up there. The arrows and the menu on each card do the same from the keyboard.
        Printing and sliced jobs are locked.</p>
      <div className="qboard">{column(pool)}{printers.map(column)}</div>
      {msg && <div className="toast-q" role="status">{msg}</div>}
    </div>
  );
}
