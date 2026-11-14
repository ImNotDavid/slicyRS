import { useEffect, useMemo, useState } from "react";
import { ArrowDownUp, Search } from "lucide-react";
import { api, type Catalog, type Job } from "../api";
import { fmtTime } from "../format";
import { Harry } from "./Brand";
import LiveProgress from "./LiveProgress";

type Status = { key: "wait" | "slice" | "ready" | "print" | "done" | "fail" | "off"; label: string; pill: string; rank: number };
type Sort = "date" | "name" | "status" | "time" | "material" | "owner";
type Filter = "all" | "active" | "ready" | "done" | "fail";

/** One word for where a job is, with the colour used everywhere else. */
export function statusOf(j: Job): Status {
  if (j.result) {
    if (j.result.outcome === "finished") return { key: "done", label: "Printed", pill: "done", rank: 4 };
    if (j.result.outcome === "failed") return { key: "fail", label: "Failed", pill: "failed", rank: 5 };
    return { key: "done", label: "Result unknown", pill: "queued", rank: 4 };
  }
  switch (j.phase) {
    case "printing": return { key: "print", label: "Printing", pill: "running", rank: 0 };
    case "ready": return { key: "ready", label: "Ready to print", pill: "ready", rank: 1 };
    case "slicing": return { key: "slice", label: "Slicing", pill: "slicing", rank: 2 };
    case "assigned": return { key: "wait", label: "Printer chosen", pill: "assigned", rank: 3 };
    case "pooled": return { key: "wait", label: "Waiting", pill: "pooled", rank: 3 };
    case "awaiting_approval": return { key: "wait", label: "Needs approval", pill: "submitted", rank: 3 };
    case "submitted": case "previewing": case "previewed": return { key: "wait", label: "Preparing", pill: "submitted", rank: 3 };
    case "printed": return { key: "done", label: "Printed", pill: "done", rank: 4 };
    case "failed": return { key: "fail", label: "Failed", pill: "failed", rank: 5 };
    case "unknown": return { key: "done", label: "Result unknown", pill: "queued", rank: 4 };
    case "cancelled": return { key: "off", label: "Cancelled", pill: "cancelled", rank: 6 };
    case "rejected": return { key: "off", label: "Rejected", pill: "cancelled", rank: 6 };
  }
  if (j.state === "failed") return { key: "fail", label: "Failed", pill: "failed", rank: 5 };
  return j.printed ? { key: "print", label: "Sent to printer", pill: "running", rank: 0 } : { key: "ready", label: "Sliced", pill: "ready", rank: 1 };
}

/** The one line that says what happens next. */
function nextLine(j: Job, s: Status): string {
  if (j.plate_to_clear) return "Collect it and confirm the plate is clear";
  if (j.result?.outcome === "failed") return j.result.layer != null && j.result.total_layers ? `Stopped at layer ${j.result.layer} of ${j.result.total_layers}` : "Stopped before the end";
  if (j.phase === "awaiting_approval") return "Waiting for a group operator to approve it";
  if (s.key === "wait") return j.waiting_for ?? (j.phase === "assigned" ? `Going to ${j.printer_name}` : "Waiting for a free printer");
  if (s.key === "ready") return j.pooled ? `Starting on ${j.printer_name ?? "its printer"}` : j.printer_name ? `Sliced for ${j.printer_name}: press Print` : "Sliced";
  if (s.key === "print") return j.printer_name ? `On ${j.printer_name}` : "Printing";
  if (s.key === "slice") return j.printer_name ? `Preparing for ${j.printer_name}` : "Preparing";
  if (j.error) return j.error;
  return "";
}

const IN: Record<Filter, (j: Job, s: Status) => boolean> = {
  all: () => true,
  active: (_, s) => s.key === "wait" || s.key === "slice" || s.key === "print",
  ready: (j, s) => s.key === "ready" || !!j.plate_to_clear,
  done: (_, s) => s.key === "done",
  fail: (_, s) => s.key === "fail",
};
const FILTERS: [Filter, string][] = [["all", "All"], ["active", "In progress"], ["ready", "Ready"], ["done", "Printed"], ["fail", "Failed"]];
const SORT_LABEL: Record<Sort, string> = { date: "Date", name: "Name", status: "Status", time: "Print time", material: "Material", owner: "Owner" };
const when = (t: number) => {
  const d = new Date(t * 1000), now = new Date();
  const same = d.toDateString() === now.toDateString();
  return same ? `Today ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : d.toLocaleDateString([], { day: "numeric", month: "short" });
};
const remember = (k: string, v?: string) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch { /* not remembered */ } return null; };

/** My jobs (everyone) and All jobs (operators and admins): a searchable grid of cards that can be ordered any way. */
export default function JobsGrid({ scope, catalog, onOpen }: { scope: "mine" | "all"; catalog: Catalog | null; onOpen: (id: string) => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>(() => (remember(`jobs.sort.${scope}`) as Sort) || "date");
  const [desc, setDesc] = useState(() => remember(`jobs.desc.${scope}`) !== "0");
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => {
    const load = () => api.jobs(scope).then(setJobs).catch((e) => setErr(e.message));
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [scope]);
  useEffect(() => { remember(`jobs.sort.${scope}`, sort); remember(`jobs.desc.${scope}`, desc ? "1" : "0"); }, [sort, desc, scope]);

  const goalName = (id?: string | null) => catalog?.goals.find((g) => g.id === id)?.name;
  const rows = useMemo(() => (jobs ?? []).map((j) => ({ j, s: statusOf(j), time: j.stats?.time_s ?? j.preview?.time_s ?? null })), [jobs]);
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    const key: Record<Sort, (r: typeof rows[number]) => string | number> = {
      date: (r) => r.j.created, name: (r) => r.j.name.toLowerCase(), status: (r) => r.s.rank, time: (r) => r.time ?? 0,
      material: (r) => r.j.filament, owner: (r) => r.j.owner_email ?? "",
    };
    return rows
      .filter((r) => IN[filter](r.j, r.s))
      .filter((r) => !t || [r.j.name, r.j.filament, r.j.printer_name ?? "", r.j.preset, goalName(r.j.spec?.goal) ?? "", scope === "all" ? r.j.owner_email ?? "" : ""]
        .join(" ").toLowerCase().includes(t))
      .sort((a, b) => { const x = key[sort](a), y = key[sort](b); return (x < y ? -1 : x > y ? 1 : 0) * (desc ? -1 : 1); });
  }, [rows, q, sort, desc, filter, scope]); // eslint-disable-line react-hooks/exhaustive-deps

  const textual = sort === "name" || sort === "material" || sort === "owner";
  const dirLabel = sort === "date" ? (desc ? "Newest first" : "Oldest first") : textual ? (desc ? "Z to A" : "A to Z") : (desc ? "High to low" : "Low to high");
  const active = rows.filter((r) => IN.active(r.j, r.s)).length;
  const collect = rows.filter((r) => r.j.plate_to_clear).length;

  if (err) return <p className="form-error">{err}</p>;
  if (!jobs) return <p className="muted">Loading…</p>;
  if (!jobs.length) return <div className="empty-state"><Harry height={48} /><p>{scope === "mine" ? "No jobs yet. Start in the Workspace and your prints will show up here." : "No jobs yet."}</p></div>;

  return (
    <div className="page-body jobs-grid-page">
      <p className="muted jobs-count">{jobs.length} job{jobs.length === 1 ? "" : "s"} · {active} in progress{collect ? ` · ${collect} to collect` : ""}</p>
      <div className="jobs-tools">
        <label className="jobs-search"><Search size={16} aria-hidden="true" />
          <input type="search" placeholder={scope === "all" ? "Search by name, owner, material or printer" : "Search by name, material or printer"} value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="Search jobs" /></label>
        <label className="lbl jobs-order">Order
          <select value={sort} onChange={(e) => { const v = e.target.value as Sort; setSort(v); setDesc(v === "date" || v === "time"); }} aria-label="Order jobs by">
            {(Object.keys(SORT_LABEL) as Sort[]).filter((k) => k !== "owner" || scope === "all").map((k) => <option key={k} value={k}>{SORT_LABEL[k]}</option>)}
          </select>
        </label>
        <button className="chip" onClick={() => setDesc(!desc)} aria-label={`Order: ${dirLabel}. Click to reverse`}><ArrowDownUp size={14} /> {dirLabel}</button>
      </div>
      <div className="chips" role="group" aria-label="Show">
        {FILTERS.map(([k, label]) => (
          <button key={k} className={"chip" + (filter === k ? " on" : "")} aria-pressed={filter === k} onClick={() => setFilter(k)}>
            {label} <span className="muted-n">{rows.filter((r) => IN[k](r.j, r.s)).length}</span></button>
        ))}
      </div>
      <section className="jgrid" aria-label="Jobs">
        {shown.map(({ j, s, time }) => (
          <button key={j.id} className="jc" onClick={() => onOpen(j.id)} aria-label={`${j.name}, ${s.label}`}>
            <div className="thumb"><img src={`/api/jobs/${j.id}/thumbnail.png`} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.display = "none"; }} /></div>
            {j.plate_to_clear && <span className="flag">Plate needs clearing</span>}
            {j.at_risk && !j.plate_to_clear && <span className="flag">May miss pickup</span>}
            <div className="body">
              <div className="row"><span className={"pill " + s.pill}>{s.label}</span><span className="lbl">{when(j.created)}</span></div>
              <div className="name">{j.name}</div>
              <div className="meta"><span>{j.filament}</span>{goalName(j.spec?.goal) && <span>{goalName(j.spec?.goal)}</span>}<span>{fmtTime(time)}</span>
                {j.members?.length || j.batch_lead ? <span>shared plate</span> : null}</div>
              {scope === "all" && j.owner_email && <div className="meta owner">{j.owner_email}</div>}
              {j.live ? <LiveProgress live={j.live} /> : nextLine(j, s) && <div className="next">{nextLine(j, s)}</div>}
            </div>
          </button>
        ))}
      </section>
      {!shown.length && <div className="jobs-none">No jobs match. Try another search or show all.</div>}
    </div>
  );
}
