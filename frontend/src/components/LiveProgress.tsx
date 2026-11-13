import type { Live } from "../api";

const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
export const left = (m?: number | null) => (m == null ? "" : m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min left` : `${m} min left`);

/** Live print progress (2.1): a bar plus percent, time left, layer and, in full mode, temperatures. */
export default function LiveProgress({ live, full }: { live: Live; full?: boolean }) {
  const pct = Math.max(0, Math.min(100, live.percent ?? 0));
  const paused = live.state === "PAUSE";
  return (
    <div className={"live" + (paused ? " paused" : "")}>
      <div className="live-bar" role="progressbar" aria-label="Print progress" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><b style={{ width: `${pct}%` }} /></div>
      <div className="live-line">
        <span>{paused ? "Paused · " : live.state === "PREPARE" ? "Preparing · " : ""}{pct}%</span>
        {live.remaining_min != null && <span>{left(live.remaining_min)}{full && live.eta ? ` (about ${clock(live.eta)})` : ""}</span>}
        {live.layer != null && live.total_layers ? <span>layer {live.layer} of {live.total_layers}</span> : null}
        {full && (live.nozzle_c != null || live.bed_c != null) && <span>nozzle {Math.round(live.nozzle_c ?? 0)}° · bed {Math.round(live.bed_c ?? 0)}°</span>}
      </div>
    </div>
  );
}
