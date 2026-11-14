import { useEffect, useState } from "react";
import { api, type Catalog, type Dashboard } from "../api";
import { Bars, DailyColumns } from "./Charts";
import FilamentCost from "./FilamentCost";

const dur = (s: number | null) => (s == null ? "—" : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const kg = (g: number) => (g >= 1000 ? `${(g / 1000).toFixed(2)} kg` : `${Math.round(g)} g`);

/** Fleet dashboard for admins (2.11): utilisation, failures, queue and filament over 7 or 30 days. */
export default function DashboardPage({ catalog }: { catalog?: Catalog | null }) {
  const [days, setDays] = useState<7 | 30>(7);
  const [d, setD] = useState<Dashboard | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setD(null); api.dashboard(days).then(setD).catch((e) => setErr(e.message)); }, [days]);
  if (err) return <p className="form-error">{err}</p>;
  const total = d ? d.prints.finished + d.prints.failed : 0;
  return (
    <div className="page-body dashboard">
      <div className="seg small dash-range" role="radiogroup" aria-label="Period">
        {([7, 30] as const).map((n) => <button key={n} role="radio" aria-checked={days === n} className={days === n ? "on" : ""} onClick={() => setDays(n)}>Last {n} days</button>)}
      </div>
      {!d ? <p className="muted">Loading…</p> : (
        <>
          <section className="stats-strip" aria-label="Headline numbers">
            <div className="stat"><span className="lbl">Prints finished</span><b className="dot">{d.prints.finished}</b><span>{total ? `${Math.round(100 * d.prints.finished / total)}% of ${total}` : "none ended"}</span></div>
            <div className="stat"><span className="lbl">Failed</span><b className="dot">{d.prints.failed}</b><span>{total ? `${Math.round(100 * d.prints.failed / total)}% failure rate` : ""}</span></div>
            <div className="stat"><span className="lbl">Median wait in the pool</span><b className="dot">{dur(d.queue.median_s)}</b><span>90% within {dur(d.queue.p90_s)}</span></div>
            <div className="stat"><span className="lbl">Filament used</span><b className="dot">{kg(d.filament.total_g)}</b><span>estimated from the slicer</span></div>
          </section>
          <div className="dash-grid">
            <section className="job-card" aria-label="Prints per day"><h3>Prints per day</h3><DailyColumns days={d.daily} /></section>
            <section className="job-card" aria-label="Printer utilisation"><h3>Printer utilisation</h3>
              <p className="muted small-text">Share of the period each printer spent printing.</p>
              <Bars rows={d.utilisation.map((u) => ({ label: u.name, value: u.percent, detail: `${dur(u.busy_s)} printing` }))} unit="%" max={100} /></section>
            <section className="job-card" aria-label="Failure rate by printer"><h3>Failure rate by printer</h3>
              <Bars rows={d.failures_by_printer.map((r) => ({ label: r.name, value: r.rate ?? 0, detail: `${r.failed} of ${r.failed + r.finished} prints failed` }))} unit="%" max={100} /></section>
            <section className="job-card" aria-label="Failure rate by material"><h3>Failure rate by material</h3>
              <Bars rows={d.failures_by_material.map((r) => ({ label: r.material, value: r.rate ?? 0, detail: `${r.failed} of ${r.failed + r.finished} prints failed` }))} unit="%" max={100} /></section>
            <section className="job-card" aria-label="Why prints failed"><h3>Why prints failed</h3>
              <Bars rows={d.failure_reasons.map((r) => ({ label: r.reason, value: r.count }))} valueText={(v) => `${v}`} /></section>
            <section className="job-card" aria-label="Failure patterns"><h3>Failure patterns</h3>
              <p className="muted small-text">Last {d.learning.window_days} days. Combinations that fail at least half the time, and printers on a bad run.</p>
              {d.learning.flags.length ? <ul className="flag-list">{d.learning.flags.map((f, i) => <li key={i}><span className="tg tg-y">{f.kind}</span> {f.text}</li>)}</ul>
                : <p className="muted">No patterns yet.</p>}</section>
            <section className="job-card" aria-label="What people said went wrong"><h3>What people said went wrong</h3>
              <Bars rows={d.learning.causes.map((c) => ({ label: c.cause, value: c.count }))} valueText={(v) => `${v}`} />
              {d.learning.unexplained > 0 && <p className="muted small-text">{d.learning.unexplained} failed print{d.learning.unexplained > 1 ? "s" : ""} not explained yet.</p>}</section>
            <section className="job-card" aria-label="Queue"><h3>Queue and pickups</h3>
              <dl className="review-facts">
                <dt>Waiting now</dt><dd>{d.queue.waiting_now}</dd>
                <dt>At risk now</dt><dd>{d.queue.at_risk_now}</dd>
                <dt>Longest wait</dt><dd>{dur(d.queue.max_s)}</dd>
                <dt>Jobs that got a printer</dt><dd>{d.queue.samples}</dd>
                <dt>Pickup times due</dt><dd>{d.queue.pickups}, {d.queue.late_pickups} missed</dd>
              </dl></section>
            <section className="job-card" aria-label="Filament by material"><h3>Filament by material</h3>
              <Bars rows={d.filament.by_material.map((r) => ({ label: r.material, value: r.grams }))} valueText={kg} /></section>
            <section className="job-card" aria-label="Filament by person"><h3>Filament by person</h3>
              <Bars rows={d.filament.by_person.map((r) => ({ label: r.person, value: r.grams }))} valueText={kg} /></section>
          </div>
        </>
      )}
      <FilamentCost catalog={catalog ?? null} />
    </div>
  );
}
