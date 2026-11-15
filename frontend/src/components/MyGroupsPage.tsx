import { useCallback, useEffect, useState } from "react";
import { api, type ManagedGroup } from "../api";

const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleDateString([], { day: "numeric", month: "short" }) : "never");

/** My groups (3.0): a group's operators invite and remove members and set their role in the group. */
export default function MyGroupsPage() {
  const [groups, setGroups] = useState<ManagedGroup[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api.managedGroups().then(setGroups).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const act = async (fn: () => Promise<unknown>, ok: string) => { setErr(""); setMsg(""); try { await fn(); setMsg(ok); load(); } catch (e) { setErr((e as Error).message); } };
  const topUp = (uid: string, name: string) => {
    const h = prompt(`Extra print hours for ${name} this quota period (0 for none)`, "0"); if (h === null) return;
    const gr = prompt(`Extra filament in grams for ${name} this quota period (0 for none)`, "0"); if (gr === null) return;
    const hours = Number(h) || 0, grams = Number(gr) || 0;
    if (hours < 0 || grams < 0 || (!hours && !grams)) return;
    act(() => api.topUp(g.id, uid, hours, grams), `Added ${hours ? `${hours} h` : ""}${hours && grams ? " and " : ""}${grams ? `${grams} g` : ""} for ${name} this quota period`);
  };
  if (err && !groups) return <p className="form-error">{err}</p>;
  if (!groups) return <p className="muted">Loading…</p>;
  if (!groups.length) return <p className="muted">You do not manage any group.</p>;
  const g = groups.find((x) => x.id === sel) ?? groups[0];
  const operators = g.members.filter((m) => m.role === "Operator").length;
  return (
    <div className="page-body">
      {groups.length > 1 && <div className="chips" role="tablist" aria-label="Your groups">{groups.map((x) => <button key={x.id} role="tab" aria-selected={x.id === g.id} className={"chip" + (x.id === g.id ? " on" : "")} onClick={() => setSel(x.id)}>{x.name}</button>)}</div>}
      <section className="stats-strip" aria-label="This group">
        <div className="stat"><span className="lbl">Members</span><b>{g.members.length}</b><span>{operators} operator{operators === 1 ? "" : "s"}</span></div>
        <div className="stat"><span className="lbl">Printers</span><b className="small">{g.default ? "All shared" : g.only_printers.length ? `Shared + ${g.only_printers.join(", ")}` : "All shared"}</b><span>{g.only_printers.length ? "some only for this group" : ""}</span></div>
        <div className="stat"><span className="lbl">Waiting now</span><b>{g.waiting}</b><span>jobs for this group</span></div>
      </section>
      <section className="job-card" aria-label="Members">
        <div className="card-head"><h3>{g.name}: members</h3>
          <form className="row-actions" onSubmit={(e) => { e.preventDefault(); if (email.trim()) act(async () => { await api.addMember(g.id, email.trim(), role); setEmail(""); }, `Invited ${email.trim()}. They join when they first sign in.`); }}>
            <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Imperial email to invite" aria-label="Email to invite" />
            <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">{g.roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
            <button className="btn primary" disabled={!email.trim()}>Invite</button></form></div>
        <table className="audit"><thead><tr><th>Person</th><th>Role in this group</th><th>Jobs</th><th>Quota used</th><th>Last signed in</th><th></th></tr></thead>
          <tbody>{g.members.map((m) => (
            <tr key={m.id}><td><b>{m.name}</b><div className="muted small-text">{m.email}{m.invited ? " · invited, not signed in yet" : ""}{!m.active ? " · disabled" : ""}</div></td>
              <td>{m.admin ? <span className="tg tg-y">Admin</span> : (
                <select value={m.role_id ?? ""} aria-label={`Role for ${m.name}`} onChange={(e) => act(() => api.setMemberRole(g.id, m.id, e.target.value), `${m.name} is now ${g.roles.find((r) => r.id === e.target.value)?.name}`)}>
                  {!g.roles.some((r) => r.id === m.role_id) && <option value={m.role_id ?? ""}>{m.role}</option>}
                  {g.roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>)}</td>
              <td>{m.jobs}</td>
              <td>{m.admin ? <span className="muted">no quota (admin)</span> : m.quota ? <>
                  {([["h", m.quota.hours], ["g", m.quota.grams]] as const).filter(([, q]) => q).map(([u, q]) => (
                    <div key={u} className={q!.warn ? "warn-note" : ""}>{u === "h" ? `${q!.used.toFixed(1)} / ${q!.allowance.toFixed(1)} h` : `${Math.round(q!.used)} / ${Math.round(q!.allowance)} g`}</div>))}
                  <button className="mini" onClick={() => topUp(m.id, m.name)}>Top up</button></> : <span className="muted">no quota</span>}</td>
              <td>{when(m.last_login)}</td>
              <td>{!g.default && !m.admin && <button className="mini" onClick={() => confirm(`Remove ${m.name} from ${g.name}?`) && act(() => api.removeMember(g.id, m.id), `${m.name} removed from ${g.name}`)}>Remove</button>}</td></tr>
          ))}</tbody></table>
        <p className="muted small-text">{g.default ? "Everyone is in the default group; an admin disables accounts instead of removing them." : "Removing someone takes them out of this group only; their jobs for it stay."} You can only give roles whose rights you hold yourself.</p>
        {msg && <p className="ok-note" role="status">{msg}</p>}{err && <p className="form-error" role="alert">{err}</p>}
      </section>
    </div>
  );
}
