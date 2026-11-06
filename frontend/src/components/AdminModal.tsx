import { useCallback, useEffect, useState } from "react";
import { Trash2, X } from "lucide-react";
import { api, type AuditEntry, type Catalog, type Printer, type PrinterInput, type ProbeResult, type StorageInfo, type User } from "../api";

type Props = { catalog: Catalog; me: User; onClose: () => void; onPrintersChanged: () => void };
type Tab = "printers" | "users" | "storage" | "audit";
const STATES = ["IDLE", "RUNNING", "PAUSE", "FAILED", "OFFLINE"];
const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "never");

export default function AdminModal({ catalog, me, onClose, onPrintersChanged }: Props) {
  const [tab, setTab] = useState<Tab>("printers");
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal wide-modal" role="dialog" aria-label="Admin" onClick={(e) => e.stopPropagation()}>
        <header>
          <h3>Admin</h3>
          <div className="tabs inline">
            {(["printers", "users", "storage", "audit"] as Tab[]).map((t) => (
              <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t === "audit" ? "Audit log" : t[0].toUpperCase() + t.slice(1)}</button>
            ))}
          </div>
          <button className="icon" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>
        <div className="modal-body">
          {tab === "printers" && <PrintersTab catalog={catalog} onChanged={onPrintersChanged} />}
          {tab === "users" && <UsersTab me={me} />}
          {tab === "storage" && <StorageTab />}
          {tab === "audit" && <AuditTab />}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ printers

const EMPTY: PrinterInput & { access_code: string } = { name: "", model: "", host: "", port: 8883, serial: "", access_code: "", hardened_nozzle: false, enabled: true };

function PrintersTab({ catalog, onChanged }: { catalog: Catalog; onChanged: () => void }) {
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const load = useCallback(() => api.printers().then(setPrinters), []);
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load]);
  const changed = () => { load(); onChanged(); };

  return (
    <>
      <p className="muted">Printers connect over LAN-mode MQTT (port 8883) from this server, so each must be reachable from it (same network or VPN).
        Access codes are stored encrypted and are never shown again.</p>
      {printers.map((p) => (
        <div key={p.id} className="admin-printer">
          <div className="fleet-row">
            <div className="fleet-main">
              <strong>{p.name}{!p.enabled && <span className="pill offline">disabled</span>}</strong>
              <span className="muted">{p.model} · {p.bed_type} · {p.host ? `${p.host}:${p.port}` : "manual (no connection)"}{p.has_access_code ? " · code saved" : ""}</span>
              {p.status.error && <span className="form-error">{p.status.error}</span>}
            </div>
            <span className={"pill " + p.status.state.toLowerCase()}>{p.status.state}</span>
            <div className="ams">
              {(p.status.ams ?? []).map((s) => <span key={s.slot} className="slot" style={{ background: s.color, color: textOn(s.color) }} title={`${s.type} ${s.remain}%`}>{s.type}</span>)}
            </div>
            <button className="btn" onClick={() => setEditing(editing === p.id ? null : p.id)}>{editing === p.id ? "Close" : "Edit"}</button>
          </div>
          {editing === p.id && <PrinterForm catalog={catalog} printer={p} onDone={() => { setEditing(null); changed(); }} />}
        </div>
      ))}
      {!printers.length && <p className="muted">No printers yet.</p>}
      {editing === "new"
        ? <PrinterForm catalog={catalog} onDone={() => { setEditing(null); changed(); }} />
        : <button className="btn primary" onClick={() => setEditing("new")}>Add printer</button>}
    </>
  );
}

function PrinterForm({ catalog, printer, onDone }: { catalog: Catalog; printer?: Printer; onDone: () => void }) {
  const models = Object.keys(catalog.printer_models);
  const filaments = Object.keys(catalog.filaments);
  const [f, setF] = useState({
    ...EMPTY, model: printer?.model ?? models[0], name: printer?.name ?? "", host: printer?.host ?? "", port: printer?.port ?? 8883,
    serial: printer?.serial ?? "", hardened_nozzle: !!printer?.hardened_nozzle, enabled: printer?.enabled ?? true,
    bed_type: printer?.bed_type ?? catalog.default_bed_type,
  });
  const [err, setErr] = useState("");
  const [probe, setProbe] = useState<ProbeResult | "testing" | null>(null);
  const live = !!f.host;

  const body = (): PrinterInput => ({
    name: f.name, model: f.model, host: f.host || null, port: Number(f.port) || 8883, serial: f.serial || null,
    access_code: f.access_code || null, hardened_nozzle: f.hardened_nozzle, enabled: f.enabled, bed_type: f.bed_type,
  });
  const save = async () => {
    setErr("");
    try { printer ? await api.updatePrinter(printer.id, body()) : await api.addPrinter(body()); onDone(); }
    catch (e) { setErr((e as Error).message); }
  };
  const test = async () => {
    setErr(""); setProbe("testing");
    try {
      setProbe(await api.testPrinter({ host: f.host, port: Number(f.port) || 8883, serial: f.serial, access_code: f.access_code || undefined, printer_id: printer?.id }));
    } catch (e) { setProbe(null); setErr((e as Error).message); }
  };
  const patchManual = async (status: Printer["status"]) => {
    if (!printer) return;
    await api.updatePrinter(printer.id, { ...body(), status });
    onDone();
  };
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF({ ...f, [k]: v });

  return (
    <div className="printer-form">
      <div className="form-grid">
        <input placeholder="Name (e.g. Bay 1 – P1S)" value={f.name} onChange={(e) => set("name", e.target.value)} aria-label="Name" />
        <select value={f.model} onChange={(e) => set("model", e.target.value)} aria-label="Model">{models.map((m) => <option key={m}>{m}</option>)}</select>
        <input placeholder="Printer IP / hostname" value={f.host} onChange={(e) => set("host", e.target.value)} aria-label="Host" />
        <input placeholder="MQTT port" type="number" value={f.port} onChange={(e) => set("port", Number(e.target.value))} aria-label="Port" />
        <input placeholder="Serial number" value={f.serial} onChange={(e) => set("serial", e.target.value)} aria-label="Serial" />
        <input placeholder={printer?.has_access_code ? "Access code (leave blank to keep)" : "LAN access code"} type="password" autoComplete="new-password"
          value={f.access_code} onChange={(e) => set("access_code", e.target.value)} aria-label="Access code" />
        <label className="plate-field">Build plate
          <select value={f.bed_type} onChange={(e) => set("bed_type", e.target.value)} aria-label="Build plate">
            {catalog.bed_types.map((b) => <option key={b}>{b}</option>)}
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={f.hardened_nozzle} onChange={(e) => set("hardened_nozzle", e.target.checked)} /> Hardened nozzle</label>
        <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Enabled (eligible for jobs)</label>
      </div>
      {!live && printer && (
        <div className="manual">
          <span className="muted">No host set, so state and loaded filament are managed by hand:</span>
          <select value={printer.status.state} onChange={(e) => patchManual({ ...printer.status, state: e.target.value })} aria-label="State">{STATES.map((s) => <option key={s}>{s}</option>)}</select>
          <select value="" onChange={(e) => e.target.value && patchManual({ ...printer.status, ams: [{ slot: 0, type: e.target.value, color: "#888888", remain: 100 }] })} aria-label="Loaded filament">
            <option value="">Set loaded filament…</option>{filaments.map((x) => <option key={x}>{x}</option>)}
          </select>
        </div>
      )}
      {probe === "testing" && <p className="muted">Connecting…</p>}
      {probe && probe !== "testing" && (
        <p className={probe.ok ? "ok-note" : "form-error"} role="status">
          {probe.ok ? `Connected. Printer reports ${probe.state ?? "online"}${probe.ams?.length ? `, ${probe.ams.length} AMS slot(s)` : ""}.` : probe.error}
        </p>
      )}
      {err && <p className="form-error" role="alert">{err}</p>}
      <div className="row-actions">
        <button className="btn primary" disabled={!f.name} onClick={save}>{printer ? "Save changes" : "Add printer"}</button>
        <button className="btn" disabled={!f.host || !f.serial || (!f.access_code && !printer?.has_access_code) || probe === "testing"} onClick={test}>Test connection</button>
        {printer && <button className="btn danger" onClick={async () => { if (confirm(`Remove ${printer.name}?`)) { await api.deletePrinter(printer.id); onDone(); } }}><Trash2 size={14} /> Remove</button>}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------- users

function UsersTab({ me }: { me: User }) {
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState({ email: "", name: "", role: "user" });
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(() => api.users().then(setUsers), []);
  useEffect(() => { load(); }, [load]);
  const guard = async (fn: () => Promise<unknown>) => { setErr(""); try { await fn(); await load(); } catch (e) { setErr((e as Error).message); } };

  return (
    <>
      <div className="row-actions">
        <button className="btn" onClick={async () => {
          setErr(""); setNote("");
          try { const r = await api.testEmail(); setNote(`Test email sent to ${r.to}. Check your inbox.`); }
          catch (e) { setErr((e as Error).message); }
        }}>Send myself a test email</button>
        <span className="muted">Verifies the SMTP settings used for sign-in codes.</span>
      </div>
      {note && <p className="ok-note" role="status">{note}</p>}
      <p className="muted">Only invited people can sign in. Use the email address Imperial lists for them; either their @imperial.ac.uk or @ic.ac.uk login is recognised.</p>
      <div className="form-grid invite">
        <input type="email" placeholder="email@imperial.ac.uk" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} aria-label="Invite email" />
        <input placeholder="Name (optional)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} aria-label="Invite name" />
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} aria-label="Invite role"><option value="user">User</option><option value="admin">Admin</option></select>
        <button className="btn primary" disabled={!form.email} onClick={() => guard(async () => { await api.invite(form); setForm({ email: "", name: "", role: "user" }); })}>Invite</button>
      </div>
      {err && <p className="form-error" role="alert">{err}</p>}
      {users.map((u) => (
        <div className="fleet-row" key={u.id}>
          <div className="fleet-main">
            <strong>{u.name || u.email}{u.id === me.id && <span className="pill">you</span>}</strong>
            <span className="muted">{u.name ? u.email + " · " : ""}last sign-in {when(u.last_login)}{u.invited_by ? ` · invited by ${u.invited_by}` : ""}</span>
          </div>
          <select value={u.role} aria-label={`Role for ${u.email}`} onChange={(e) => guard(() => api.patchUser(u.id, { role: e.target.value }))}>
            <option value="user">User</option><option value="admin">Admin</option>
          </select>
          <label className="check"><input type="checkbox" checked={u.active} onChange={(e) => guard(() => api.patchUser(u.id, { active: e.target.checked }))} aria-label={`Active ${u.email}`} /> Active</label>
          <button className="icon" aria-label={`Delete ${u.email}`} disabled={u.id === me.id}
            onClick={() => confirm(`Delete ${u.email}?`) && guard(() => api.deleteUser(u.id))}><Trash2 size={16} /></button>
        </div>
      ))}
    </>
  );
}

// --------------------------------------------------------------------- audit

function AuditTab() {
  const [log, setLog] = useState<AuditEntry[]>([]);
  useEffect(() => { api.audit().then(setLog); }, []);
  return (
    <table className="audit">
      <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Detail</th></tr></thead>
      <tbody>{log.map((a) => <tr key={a.id}><td>{when(a.at)}</td><td>{a.actor}</td><td>{a.action}</td><td>{a.detail}</td></tr>)}</tbody>
    </table>
  );
}

const textOn = (hex: string) => {
  const n = parseInt(hex.slice(1, 7), 16);
  return (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255 > 0.55 ? "#000" : "#fff";
};

// ------------------------------------------------------------------- storage

const bytes = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} kB`);

function StorageTab() {
  const [info, setInfo] = useState<StorageInfo | null>(null);
  const [form, setForm] = useState({ job_files_days: 30, failed_job_days: 7, model_files_days: 30 });
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => api.storage().then((i) => { setInfo(i); setForm(i.settings); }), []);
  useEffect(() => { load(); }, [load]);
  if (!info) return <p className="muted">Loading…</p>;
  const used = 100 - (info.disk.free_percent ?? 0);

  const save = async () => { setErr(""); setMsg(""); try { await api.saveStorage(form); setMsg("Retention settings saved."); load(); } catch (e) { setErr((e as Error).message); } };
  const clean = async (dry: boolean) => {
    setErr(""); setMsg("");
    try {
      const r = await api.cleanup(dry);
      setMsg(`${dry ? "Would delete" : "Deleted"} files of ${r.jobs_cleaned} job(s) and ${r.models_cleaned} model(s), ${bytes(r.bytes_freed)}.`);
      if (!dry) load();
    } catch (e) { setErr((e as Error).message); }
  };
  const num = (k: keyof typeof form, label: string, hint: string) => (
    <label className="plate-field">{label}
      <input type="number" min={0} max={3650} value={form[k]} onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })} aria-label={label} />
      <span className="muted">{hint}</span>
    </label>
  );

  return (
    <>
      {info.disk.low && <p className="form-error" role="alert">Disk space is low ({bytes(info.disk.free)} free). Uploads are refused when it runs out. Lower the retention times or free space.</p>}
      <div className="meter" role="img" aria-label={`Disk ${used.toFixed(0)} percent used`}><div style={{ width: `${used}%` }} className={info.disk.low ? "bad" : ""} /></div>
      <p className="muted">Disk: {bytes(info.disk.used)} used of {bytes(info.disk.total)} ({bytes(info.disk.free)} free).
        This service: job files {bytes(info.data.jobs)}, uploaded models {bytes(info.data.models)}, database {bytes(info.data.database)}.{" "}
        {info.counts.jobs_with_files} of {info.counts.jobs} jobs still have files.</p>
      <h4>Delete files after (days, 0 = keep forever)</h4>
      <div className="form-grid">
        {num("job_files_days", "Job files (G-code, print bundles)", "counted from creation or printing")}
        {num("failed_job_days", "Failed jobs", "slicing failed")}
        {num("model_files_days", "Uploaded models", "not used for this long")}
      </div>
      <p className="muted">Only files are deleted. Job history, users, printers and the audit log are kept, so statistics are not lost.
        Cleanup runs every 6 hours{info.last_cleanup ? `; last run removed ${info.last_cleanup.jobs_cleaned} job(s) and ${info.last_cleanup.models_cleaned} model(s) (${bytes(info.last_cleanup.bytes_freed)})` : ""}.</p>
      {msg && <p className="ok-note" role="status">{msg}</p>}
      {err && <p className="form-error" role="alert">{err}</p>}
      <div className="row-actions">
        <button className="btn primary" onClick={save}>Save settings</button>
        <button className="btn" onClick={() => clean(true)}>Preview cleanup</button>
        <button className="btn danger" onClick={() => confirm("Delete all expired files now? This cannot be undone.") && clean(false)}>Run cleanup now</button>
      </div>
    </>
  );
}
