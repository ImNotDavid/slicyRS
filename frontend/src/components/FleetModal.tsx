import { useState } from "react";
import { api } from "../api";
import Frame from "./Frame";
import { Harry } from "./Brand";
import type { Printer } from "../api";
import PlateCheck from "./PlateCheck";
import LiveProgress from "./LiveProgress";

type Props = { printers: Printer[]; onClose?: () => void; onChanged?: () => void; inline?: boolean; onOpenJob?: (id: string) => void };

// black or white text, whichever reads better on a filament colour
const textOn = (hex: string) => {
  const n = parseInt(hex.slice(1, 7), 16);
  return (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255 > 0.55 ? "#000" : "#fff";
};

/** Read-only fleet status for every user; admins configure printers in the Admin console. */
export default function FleetModal({ printers, onClose, onChanged, inline, onOpenJob }: Props) {
  const visible = printers.filter((p) => p.enabled);
  const [open, setOpen] = useState<string | null>(null);
  const [maint, setMaint] = useState<{ id: string; note: string } | null>(null);
  const [err, setErr] = useState("");
  const act = async (fn: () => Promise<unknown>) => { setErr(""); try { await fn(); setMaint(null); onChanged?.(); } catch (e) { setErr((e as Error).message); } };
  return (
    <Frame title="Printer fleet" inline={inline} cards onClose={onClose}>
          {!visible.length && <div className="empty-state"><Harry height={48} /><p>No printers are available yet. An admin needs to add them.</p></div>}
          {visible.map((p) => (
            <div className="fleet-row" key={p.id}>
              <div className="fleet-main">
                <strong>{p.name}</strong>
                <span className="muted">{p.model}{p.queue_depth ? ` · ${p.queue_depth} queued` : ""}</span>
                {p.users && (p.users.length
                  ? <span className="who"><b>{p.users.length > 1 ? `${p.users.length} people share this plate` : p.users[0].name}</b> {p.users.map((u) => (
                      <button key={u.job_id} className="job-link small" onClick={() => onOpenJob?.(u.job_id)} disabled={!onOpenJob}>{p.users!.length > 1 ? u.name : u.job_name}</button>))}</span>
                  : <span className="who muted">{p.last_user ? `Nobody now. Last used by ${p.last_user.name}` : "Nobody now"}</span>)}
              </div>
              <span className={"pill " + p.status.state.toLowerCase()}>{p.status.state}</span>
              {["RUNNING", "PAUSE", "PREPARE"].includes(p.status.state) && p.status.progress != null && (
                <LiveProgress full live={{ state: p.status.state, percent: p.status.progress, remaining_min: p.status.remaining_min ?? null, eta: null,
                  layer: p.status.layer_num ?? null, total_layers: p.status.total_layer_num ?? null, nozzle_c: p.status.nozzle_c ?? null, bed_c: p.status.bed_c ?? null, updated: null }} />
              )}
              {p.bed && <span className={"pill bed-" + p.bed.state} title={p.bed.by ? `confirmed by ${p.bed.by}` : "build plate"}>plate {p.bed.state}</span>}
              {p.bed && p.bed.state !== "clear" && p.status.state !== "RUNNING" && <button className="btn" onClick={() => { setOpen(open === p.id ? null : p.id); }}>Confirm plate clear</button>}
              <div className="ams">
                {(p.status.ams ?? []).map((s) => (
                  <span key={s.slot} className="slot" title={`${s.type} ${s.remain}%`} style={{ background: s.color, color: textOn(s.color) }}>{s.type}</span>
                ))}
              </div>
              {p.attention && (
                <div className="upkeep attention" role="status"><b>Needs attention</b><span>{p.attention.reason}. It stays out of the pool until someone checks it.</span>
                  <button className="btn primary" onClick={() => act(() => api.markReady(p.id))}>Mark ready</button></div>
              )}
              {p.maintenance && (
                <div className="upkeep" role="status"><b>In maintenance</b><span>{p.maintenance.note || "No note"} · {p.maintenance.by}</span>
                  <button className="btn" onClick={() => act(() => api.setMaintenance(p.id, false))}>Back in service</button></div>
              )}
              {!p.maintenance && (maint?.id === p.id ? (
                <div className="upkeep-form">
                  <input value={maint.note} maxLength={200} placeholder="Note for everyone (for example: new nozzle, back Friday)" aria-label="Maintenance note"
                    onChange={(e) => setMaint({ id: p.id, note: e.target.value })} />
                  <button className="btn primary" onClick={() => act(() => api.setMaintenance(p.id, true, maint.note))}>Start maintenance</button>
                  <button className="btn" onClick={() => setMaint(null)}>Cancel</button>
                </div>
              ) : <button className="mini self-start" onClick={() => setMaint({ id: p.id, note: "" })}>Maintenance…</button>)}
              {p.wait_s != null && p.wait_s > 0 && <span className="muted small">Free in about {Math.round(p.wait_s / 60)} min</span>}
              {open === p.id && <PlateCheck printerId={p.id} onDone={() => { setOpen(null); onChanged?.(); }} />}
            </div>
          ))}
      {err && <p className="form-error" role="alert">{err}</p>}
    </Frame>
  );
}
