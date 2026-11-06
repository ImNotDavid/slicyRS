import { X } from "lucide-react";
import type { Printer } from "../api";

type Props = { printers: Printer[]; onClose: () => void };

// black or white text, whichever reads better on a filament colour
const textOn = (hex: string) => {
  const n = parseInt(hex.slice(1, 7), 16);
  return (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255 > 0.55 ? "#000" : "#fff";
};

/** Read-only fleet status for every user; admins configure printers in the Admin console. */
export default function FleetModal({ printers, onClose }: Props) {
  const visible = printers.filter((p) => p.enabled);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" role="dialog" aria-label="Printer fleet" onClick={(e) => e.stopPropagation()}>
        <header><h3>Printer fleet</h3><button className="icon" onClick={onClose} aria-label="Close"><X size={18} /></button></header>
        <div className="modal-body">
          {!visible.length && <p className="muted">No printers are available yet. An admin needs to add them.</p>}
          {visible.map((p) => (
            <div className="fleet-row" key={p.id}>
              <div className="fleet-main">
                <strong>{p.name}</strong>
                <span className="muted">{p.model}{p.queue_depth ? ` · ${p.queue_depth} queued` : ""}</span>
              </div>
              <span className={"pill " + p.status.state.toLowerCase()}>{p.status.state}</span>
              <div className="ams">
                {(p.status.ams ?? []).map((s) => (
                  <span key={s.slot} className="slot" title={`${s.type} ${s.remain}%`} style={{ background: s.color, color: textOn(s.color) }}>{s.type}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
