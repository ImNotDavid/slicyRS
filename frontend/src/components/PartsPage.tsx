import { useCallback, useEffect, useState } from "react";
import { Folder, FolderPlus, Plus, Search, Trash2 } from "lucide-react";
import { api, type PartFolder, type SavedPart } from "../api";
import { Harry } from "./Brand";

const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "—");
const mb = (b: number) => (b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} kB`);

/** My Parts (2.17): every model the person has uploaded, kept, in folders, searchable; open one on the Workspace plate again. */
export default function PartsPage({ onUse, onOpenJob }: { onUse: (id: string, add: boolean) => void; onOpenJob: (id: string) => void }) {
  const [parts, setParts] = useState<SavedPart[] | null>(null);
  const [folders, setFolders] = useState<PartFolder[]>([]);
  const [folder, setFolder] = useState("");             // "" all, "none" not in a folder, or a folder id
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [newFolder, setNewFolder] = useState<string | null>(null);
  const load = useCallback(() => api.parts(q, folder).then((r) => { setParts(r.parts); setFolders(r.folders); }).catch((e) => setErr(e.message)), [q, folder]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  const act = async (fn: () => Promise<unknown>) => { setErr(""); try { await fn(); load(); } catch (e) { setErr((e as Error).message); } };

  return (
    <div className="page-body parts-page">
      <div className="parts-layout">
        <nav className="folders" aria-label="Folders">
          <button className={"folder" + (folder === "" ? " on" : "")} onClick={() => setFolder("")}><Folder size={16} /> All parts</button>
          <button className={"folder" + (folder === "none" ? " on" : "")} onClick={() => setFolder("none")}><Folder size={16} /> Not in a folder</button>
          {folders.map((f) => (
            <div key={f.id} className="folder-row">
              <button className={"folder" + (folder === f.id ? " on" : "")} onClick={() => setFolder(f.id)}><Folder size={16} /> {f.name} <span className="muted-n">{f.count}</span></button>
              <button className="icon" aria-label={`Rename ${f.name}`} onClick={() => { const n = prompt("New name for the folder", f.name); if (n?.trim()) act(() => api.renameFolder(f.id, n.trim())); }}>✎</button>
              <button className="icon" aria-label={`Remove folder ${f.name}`} onClick={() => confirm(`Remove the folder "${f.name}"? Its parts stay, outside any folder.`) && act(async () => { await api.deleteFolder(f.id); if (folder === f.id) setFolder(""); })}><Trash2 size={13} /></button>
            </div>
          ))}
          {newFolder === null
            ? <button className="mini self-start" onClick={() => setNewFolder("")}><FolderPlus size={14} /> New folder</button>
            : <form className="upkeep-form" onSubmit={(e) => { e.preventDefault(); if (newFolder.trim()) act(async () => { await api.addFolder(newFolder.trim()); setNewFolder(null); }); }}>
                <input autoFocus value={newFolder} maxLength={60} onChange={(e) => setNewFolder(e.target.value)} placeholder="Folder name" aria-label="Folder name" />
                <div className="live-controls"><button className="btn primary">Create</button><button type="button" className="btn" onClick={() => setNewFolder(null)}>Cancel</button></div>
              </form>}
        </nav>

        <div className="parts-main">
          <label className="jobs-search"><Search size={16} aria-hidden="true" /><input type="search" placeholder="Search your parts by name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search parts" /></label>
          {err && <p className="form-error" role="alert">{err}</p>}
          {parts && !parts.length && <div className="empty-state"><Harry height={44} /><p>{q || folder ? "No parts match." : "Every model you upload in the Workspace is kept here."}</p></div>}
          <section className="jgrid" aria-label="Parts">
            {(parts ?? []).map((p) => (
              <article key={p.id} className="jc part-card">
                <div className="thumb">{p.available && <img src={`/api/parts/${p.id}/thumbnail.png`} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.display = "none"; }} />}</div>
                <div className="body">
                  <div className="name" title={p.name}>{p.name}</div>
                  <div className="meta">{p.dims && <span>{p.dims.map((d) => Math.round(d)).join(" × ")} mm</span>}<span>{mb(p.size)}</span><span>added {when(p.created)}</span></div>
                  <div className="meta">{p.jobs ? <span>{p.jobs} job{p.jobs > 1 ? "s" : ""}, {p.printed} printed</span> : <span>not printed yet</span>}
                    {p.last_job && <button className="job-link small" onClick={() => onOpenJob(p.last_job!.id)}>last job</button>}</div>
                  <div className="live-controls">
                    <button className="btn primary" disabled={!p.available} onClick={() => onUse(p.id, false)}>Open</button>
                    <button className="btn" disabled={!p.available} title="Add it to the plate already in the Workspace" onClick={() => onUse(p.id, true)}><Plus size={14} /> Add to plate</button>
                  </div>
                  <div className="part-tools">
                    <select aria-label={`Move ${p.name} to a folder`} value={p.folder_id ?? ""} onChange={(e) => act(() => api.updatePart(p.id, e.target.value ? { folder_id: e.target.value } : { clear_folder: true }))}>
                      <option value="">No folder</option>{folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                    </select>
                    <button className="mini" onClick={() => { const n = prompt("New name", p.name); if (n?.trim()) act(() => api.updatePart(p.id, { name: n.trim() })); }}>Rename</button>
                    <button className="mini" aria-label={`Delete ${p.name}`} onClick={() => confirm(`Delete "${p.name}" for good? Jobs made from it keep their own copies until they expire.`) && act(() => api.deletePart(p.id))}><Trash2 size={12} /></button>
                  </div>
                </div>
              </article>
            ))}
          </section>
        </div>
      </div>
    </div>
  );
}
