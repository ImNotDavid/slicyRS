import { useState, type ReactNode } from "react";

/** Small, dependency-free charts in the house style: thin marks, recessive axes, a hover tooltip on every mark,
 *  text in text colours (never the series colour), and a table view for each. Colours: --series-1, --series-2. */

type Tip = { x: number; y: number; body: ReactNode } | null;

function Tooltip({ tip }: { tip: Tip }) {
  if (!tip) return null;
  return <div className="chart-tip" style={{ left: tip.x, top: tip.y }} role="tooltip">{tip.body}</div>;
}

/** Horizontal bars for one measure (no legend: the title names it). `max` fixes the scale (for example 100 for percent). */
export function Bars({ rows, unit = "", max, valueText }: {
  rows: { label: string; value: number; detail?: string }[]; unit?: string; max?: number; valueText?: (v: number) => string;
}) {
  const [tip, setTip] = useState<Tip>(null);
  const top = max ?? Math.max(1, ...rows.map((r) => r.value));
  const fmt = valueText ?? ((v: number) => `${Math.round(v * 10) / 10}${unit}`);
  if (!rows.length) return <p className="muted small-text">Nothing in this period.</p>;
  return (
    <div className="bars" onMouseLeave={() => setTip(null)}>
      {rows.map((r) => (
        <div key={r.label} className="bar-row"
          onMouseMove={(e) => { const b = e.currentTarget.parentElement!.getBoundingClientRect(); setTip({ x: e.clientX - b.left + 12, y: e.clientY - b.top + 12, body: <><b>{r.label}</b><span>{fmt(r.value)}</span>{r.detail && <span className="muted">{r.detail}</span>}</> }); }}>
          <span className="bar-label" title={r.label}>{r.label}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${Math.max(0.5, (100 * r.value) / top)}%` }} /></span>
          <span className="bar-value">{fmt(r.value)}</span>
        </div>
      ))}
      <Tooltip tip={tip} />
    </div>
  );
}

/** Stacked columns per day for two series (printed and failed), with a legend, a 2 px gap between segments, and a table. */
export function DailyColumns({ days }: { days: { day: string; finished: number; failed: number }[] }) {
  const [tip, setTip] = useState<Tip>(null);
  const max = Math.max(1, ...days.map((d) => d.finished + d.failed));
  const H = 140, W = Math.max(560, days.length * 22), bw = Math.max(6, Math.min(18, W / days.length - 6));
  const step = W / days.length;
  const label = (d: string) => new Date(d + "T12:00:00").toLocaleDateString([], days.length > 10 ? { day: "numeric" } : { weekday: "short" });
  return (
    <div className="daily" onMouseLeave={() => setTip(null)}>
      <div className="legend-row"><span><i className="sw s1" />Printed</span><span><i className="sw s2" />Failed</span></div>
      <svg viewBox={`0 0 ${W} ${H + 18}`} width="100%" role="img" aria-label="Prints per day, printed and failed">
        <line x1="0" x2={W} y1={H} y2={H} className="axis" />
        {days.map((d, i) => {
          const x = i * step + (step - bw) / 2;
          const hf = (H - 4) * d.finished / max, hx = (H - 4) * d.failed / max;
          return (
            <g key={d.day} onMouseMove={(e) => { const b = (e.currentTarget.ownerSVGElement!.parentElement as HTMLElement).getBoundingClientRect(); setTip({ x: e.clientX - b.left + 12, y: e.clientY - b.top + 12, body: <><b>{new Date(d.day + "T12:00:00").toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" })}</b><span>{d.finished} printed</span><span>{d.failed} failed</span></> }); }}>
              <rect x={i * step} y={0} width={step} height={H} fill="transparent" />
              {d.finished > 0 && <rect x={x} y={H - hf} width={bw} height={hf} className="s1" rx="2" />}
              {d.failed > 0 && <rect x={x} y={H - hf - hx - (d.finished ? 2 : 0)} width={bw} height={hx} className="s2" rx="2" />}
              {(days.length <= 10 || i % 5 === 0) && <text x={i * step + step / 2} y={H + 13} textAnchor="middle" className="tick">{label(d.day)}</text>}
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
      <details className="chart-table"><summary>Show as a table</summary>
        <table className="audit"><thead><tr><th>Day</th><th>Printed</th><th>Failed</th></tr></thead>
          <tbody>{days.map((d) => <tr key={d.day}><td>{d.day}</td><td>{d.finished}</td><td>{d.failed}</td></tr>)}</tbody></table></details>
    </div>
  );
}
