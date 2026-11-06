export type Field = {
  key: string; label: string; unit: string | null; type: "number" | "bool" | "select";
  options: string[] | null; level: "basic" | "simple" | "advanced"; step: number | null;
};
export type Group = { name: string; fields: Field[] };
export type Tab = { tab: string; groups: Group[] };
export type FilamentInfo = { orca: string; nozzle: number; bed: number; enclosed: boolean };
export type PrinterModel = { vendor: string; bed: [number, number, number]; enclosed: boolean; ams: string };
export type Catalog = {
  printer_models: Record<string, PrinterModel>;
  filaments: Record<string, FilamentInfo>;
  process_presets: { id: string; layer_height: number }[];
  settings_schema: Tab[];
  preset_defaults: Record<string, Record<string, number | string | boolean>>;
  slicer_mode: string;
  engine: string;
  bed_types: string[];
  default_bed_type: string;
};
export type AmsSlot = { slot: number; type: string; color: string; remain: number };
export type Printer = {
  id: string; name: string; model: string; hardened_nozzle?: boolean; enabled: boolean; queue_depth: number; live: boolean; bed_type: string;
  status: { state: string; ams?: AmsSlot[]; progress?: number; error?: string | null };
  // admin only
  host?: string | null; port?: number; serial?: string | null; has_access_code?: boolean; demo?: boolean;
};
export type PrinterInput = {
  name: string; model: string; host?: string | null; port?: number; serial?: string | null; access_code?: string | null;
  hardened_nozzle?: boolean; enabled?: boolean; bed_type?: string; status?: Printer["status"];
};
export type User = { id: string; email: string; name: string; role: "admin" | "user"; active: boolean; invited_by: string | null; created: number; last_login: number | null };
export type AuthConfig = { sso: boolean; sso_name: string; dev: boolean; email_login: boolean };
export type StorageInfo = {
  disk: { total: number; used: number; free: number; free_percent: number | null; low: boolean };
  data: { jobs: number; models: number; database: number };
  counts: { jobs: number; jobs_with_files: number; models: number };
  settings: { job_files_days: number; failed_job_days: number; model_files_days: number };
  last_cleanup: { at: number; jobs_cleaned: number; models_cleaned: number; bytes_freed: number } | null;
};
export type CleanupReport = { dry_run: boolean; jobs_cleaned: number; models_cleaned: number; bytes_freed: number };
export type AuditEntry = { id: string; at: number; actor: string; action: string; detail: string };
export type ProbeResult = { ok: boolean; error: string | null; state?: string; ams?: AmsSlot[] };
export type Candidate = {
  printer_id: string; name: string; model: string; eligible: boolean; score: number;
  reasons: string[]; blockers: string[]; loaded_slot: number | null;
};
export type AssignResult = { dims: [number, number, number]; chosen: string | null; candidates: Candidate[] };
export type Job = {
  id: string; name: string; state: "slicing" | "done" | "failed"; error?: string;
  printer_id: string; printer_name: string; printer_model: string; filament: string; preset: string;
  queued: boolean; printed: boolean; created: number; bundle?: string | null; owner_email?: string; files_deleted_at?: number | null;
  result?: { outcome: "finished" | "failed"; summary: string; layer: number | null; total_layers: number | null;
             hms: { code: string; text: string }[]; duration_s: number | null } | null;
  stats?: { time_s?: number; filament_g?: number; layers?: number; mock?: boolean };
};
export type Transform = { rotation: [number, number, number]; scale: number };

async function j<T>(res: Response): Promise<T> {
  if (res.status === 401 && !res.url.includes("/auth/")) window.dispatchEvent(new Event("auth-expired"));
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail ?? msg; } catch { /* non-JSON error body */ }
    throw new Error(msg);
  }
  return res.json();
}

const post = (url: string, body?: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });

const send = (method: string, url: string, body?: unknown) =>
  fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  authConfig: () => fetch("/auth/config").then(j<AuthConfig>),
  me: () => fetch("/api/me").then(j<User>),
  emailRequest: (email: string) => send("POST", "/auth/email/request", { email }).then(j<{ ok: boolean; ttl_minutes: number }>),
  emailVerify: (email: string, code: string) => send("POST", "/auth/email/verify", { email, code }).then(j<User>),
  testEmail: () => send("POST", "/api/admin/email/test").then(j<{ ok: boolean; to: string }>),
  devLogin: (email: string) => send("POST", "/auth/dev-login", { email }).then(j<User>),
  logoutAll: () => send("POST", "/auth/logout-all").then(j<{ ok: boolean; ended: number }>),
  logout: () => send("POST", "/auth/logout").then(j<{ ok: boolean }>),
  users: () => fetch("/api/admin/users").then(j<User[]>),
  invite: (u: { email: string; name: string; role: string }) => send("POST", "/api/admin/users", u).then(j<User>),
  patchUser: (id: string, p: { role?: string; active?: boolean }) => send("PATCH", `/api/admin/users/${id}`, p).then(j<User>),
  deleteUser: (id: string) => send("DELETE", `/api/admin/users/${id}`).then(j<{ ok: boolean }>),
  storage: () => fetch("/api/admin/storage").then(j<StorageInfo>),
  saveStorage: (s: Partial<StorageInfo["settings"]>) => send("PUT", "/api/admin/storage/settings", s).then(j<StorageInfo["settings"]>),
  cleanup: (dry: boolean) => send("POST", `/api/admin/storage/cleanup?dry_run=${dry}`).then(j<CleanupReport>),
  audit: () => fetch("/api/admin/audit?limit=100").then(j<AuditEntry[]>),
  testPrinter: (p: { host: string; port: number; serial: string; access_code?: string; printer_id?: string }) =>
    send("POST", "/api/admin/printers/test", p).then(j<ProbeResult>),
  catalog: () => fetch("/api/catalog").then(j<Catalog>),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/models", { method: "POST", body: fd }).then(j<{ id: string; name: string; dims: [number, number, number]; triangles: number }>);
  },
  assign: (model_id: string, filament: string, t: Transform) =>
    post("/api/assign", { model_id, filament, ...t }).then(j<AssignResult>),
  slice: (body: { model_id: string; filament: string; preset: string; settings: Record<string, unknown>; printer_id?: string } & Transform) =>
    post("/api/slice", body).then(j<Job>),
  job: (id: string) => fetch(`/api/jobs/${id}`).then(j<Job>),
  jobs: () => fetch("/api/jobs").then(j<Job[]>),
  queue: (id: string) => post(`/api/jobs/${id}/queue`).then(j<Job>),
  print: (id: string) => post(`/api/jobs/${id}/print`).then(j<{ ok: boolean; started: boolean; file: string }>),
  printers: () => fetch("/api/printers").then(j<Printer[]>),
  addPrinter: (p: PrinterInput) => send("POST", "/api/admin/printers", p).then(j<Printer>),
  updatePrinter: (id: string, p: PrinterInput) => send("PUT", `/api/admin/printers/${id}`, p).then(j<Printer>),
  deletePrinter: (id: string) => send("DELETE", `/api/admin/printers/${id}`).then(j<{ ok: boolean }>),
};

export const modelUrl = (id: string) => `/api/models/${id}/preview.stl`;
export const gcodeUrl = (jobId: string) => `/api/jobs/${jobId}/gcode`;
