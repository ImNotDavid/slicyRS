import { useState } from "react";
import Frame from "./Frame";
import { Harry } from "./Brand";
import type { Printer } from "../api";
import PlateCheck from "./PlateCheck";

type Props = { printers: Printer[]; onClose?: () => void; onChanged?: () => void; inline?: boolean };

// black or white text, whichever reads better on a filament colour
const textOn = (hex: string) => {
  const n = parseInt(hex.slice(1, 7), 16);
  return (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255 > 0.55 ? "#000" : "#fff";
};

/** Read-only fleet status for every user; admins configure printers in the Admin console. */
export default function FleetModal({ printers, onClose, onChanged, inline }: Props) {
  const visible = printers.filter((p) => p.enabled);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Frame title="Printer fleet" inline={inline} cards onClose={onClose}>
          {!visible.length && <div className="empty-state"><Harry height={48} /><p>No printers are available yet. An admin needs to add them.</p></div>}
          {visible.map((p) => (
            <div className="fleet-row" key={p.id}>
              <div className="fleet-main">
                <strong>{p.name}</strong>
                <span className="muted">{p.model}{p.queue_depth ? ` · ${p.queue_depth} queued` : ""}</span>
                {p.users && (p.users.length
                  ? <span className="who"><b>{p.users.length > 1 ? `${p.users.length} people share this plate` : p.users[0].name}</b> <span className="muted">{p.users.length > 1 ? p.users.map((u) => u.name).join(", ") : p.users[0].job_name}</span></span>
                  : <span className="who muted">{p.last_user ? `Nobody now. Last used by ${p.last_user.name}` : "Nobody now"}</span>)}
              </div>
              <span className={"pill " + p.status.state.toLowerCase()}>{p.status.state}</span>
              {p.bed && <span className={"pill bed-" + p.bed.state} title={p.bed.by ? `confirmed by ${p.bed.by}` : "build plate"}>plate {p.bed.state}</span>}
              {p.bed && p.bed.state !== "clear" && p.status.state !== "RUNNING" && <button className="btn" onClick={() => { setOpen(open === p.id ? null : p.id); }}>Confirm plate clear</button>}
              <div className="ams">
                {(p.status.ams ?? []).map((s) => (
                  <span key={s.slot} className="slot" title={`${s.type} ${s.remain}%`} style={{ background: s.color, color: textOn(s.color) }}>{s.type}</span>
                ))}
              </div>
              {open === p.id && <PlateCheck printerId={p.id} onDone={() => { setOpen(null); onChanged?.(); }} />}
            </div>
          ))}
    </Frame>
  );
}
