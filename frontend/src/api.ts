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
export type Bed = { state: "occupied" | "clear" | "unknown"; source: string | null; by?: string | null; at: number | null; finished?: boolean };
export type Printer = {
  bed?: Bed;
  id: string; name: string; model: string; hardened_nozzle?: boolean; enabled: boolean; queue_depth: number; live: boolean; bed_type: string;
  status: { state: string; ams?: AmsSlot[]; progress?: number; error?: string | null };
  // admin only
  host?: string | null; port?: number; serial?: string | null; has_access_code?: boolean; demo?: boolean; gateway?: string | null;
};
export type Gateway = { id: string; name: string; created: number | null; last_seen: number | null; version?: string | null; online: boolean; printers: string[] };
export type PrinterInput = {
  name: string; model: string; host?: string | null; port?: number; serial?: string | null; access_code?: string | null;
  hardened_nozzle?: boolean; enabled?: boolean; bed_type?: string; gateway?: string | null; status?: Printer["status"];
};
export type User = { id: string; email: string; name: string; role: "admin" | "user"; operator?: boolean; active: boolean; invited_by: string | null; created: number; last_login: number | null };
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
export type Phase = "submitted" | "previewing" | "previewed" | "pooled" | "assigned" | "slicing" | "ready" | "printing" | "printed" | "failed" | "unknown" | "cancelled" | "rejected";
export type Job = {
  id: string; name: string; state: string; phase?: Phase; error?: string;
  pickup_by?: number | null; at_risk?: boolean; waiting_for?: string; submitted_at?: number | null; auto_start?: boolean;
  allow_batching?: boolean; members?: string[]; batch_lead?: string | null; plate_share?: number | null;
  preview?: { time_s?: number; filament_g?: number; layers?: number };
  printer_id: string | null; printer_name: string | null; printer_model: string | null; filament: string; preset: string;
  queued: boolean; printed: boolean; created: number; bundle?: string | null; owner_email?: string; files_deleted_at?: number | null;
  result?: { outcome: "finished" | "failed" | "unknown"; summary: string; layer: number | null; total_layers: number | null;
             hms: { code: string; text: string }[]; duration_s: number | null } | null;
  stats?: { time_s?: number; filament_g?: number; layers?: number; mock?: boolean };
};
export type Forecast = { feasible: boolean; earliest_finish: number | null; jobs_ahead: number; printer_name: string | null; likely?: boolean };
export type JobEvent = { id: string; kind: string; at: number; actor: string | null; data: Record<string, unknown> };
export type PipelineSettings = { cooling_margin_min: number; operator_delay_min: number; ready_hold_min: number; estimate_buffer_pct: number; max_hold_h: number;
  batch_slack_min: number; plate_max_parts: number; plate_max_h: number };
export type Stats = {
  jobs: number; by_phase: Record<string, number>; events: number; tasks: Record<string, number>;
  by_user: Record<string, { jobs: number; finished: number; failed: number; print_s: number; filament_g: number }>;
  by_printer: Record<string, { name: string | null; jobs: number; finished: number; failed: number; print_s: number }>;
  estimate_accuracy: { samples: number; sliced_median_ratio: number | null; preview_median_ratio: number | null; preview_samples: number };
  queue_wait_s: { samples: number; median: number | null; max: number | null }; pickup: { met: number; missed: number };
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
  patchUser: (id: string, p: { role?: string; active?: boolean; operator?: boolean }) => send("PATCH", `/api/admin/users/${id}`, p).then(j<User>),
  deleteUser: (id: string) => send("DELETE", `/api/admin/users/${id}`).then(j<{ ok: boolean }>),
  storage: () => fetch("/api/admin/storage").then(j<StorageInfo>),
  saveStorage: (s: Partial<StorageInfo["settings"]>) => send("PUT", "/api/admin/storage/settings", s).then(j<StorageInfo["settings"]>),
  cleanup: (dry: boolean) => send("POST", `/api/admin/storage/cleanup?dry_run=${dry}`).then(j<CleanupReport>),
  audit: () => fetch("/api/admin/audit?limit=100").then(j<AuditEntry[]>),
  testPrinter: (p: { host: string; port: number; serial: string; access_code?: string; printer_id?: string; gateway?: string }) =>
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
  draft: (body: { model_id: string; filament: string; preset: string; settings: Record<string, unknown> } & Transform) =>
    post("/api/jobs/preview", body).then(j<Job>),
  forecast: (id: string, pickup_by?: number) => fetch(`/api/jobs/${id}/forecast${pickup_by ? `?pickup_by=${pickup_by}` : ""}`).then(j<Forecast>),
  submit: async (id: string, body: { pickup_by?: number | null; colour?: string; auto_start?: boolean; allow_batching?: boolean }) => {
    const res = await post(`/api/jobs/${id}/submit`, body);
    if (res.status === 409) { const d = await res.json().catch(() => ({})); if (d.earliest_pickup) throw Object.assign(new Error(d.detail), { earliest: d.earliest_pickup as number }); throw new Error(d.detail ?? "Cannot submit"); }
    return j<Job>(res);
  },
  discard: (id: string) => send("DELETE", `/api/jobs/${id}`).then(j<{ ok: boolean }>),
  cancel: (id: string) => post(`/api/jobs/${id}/cancel`).then(j<Job>),
  events: (id: string) => fetch(`/api/jobs/${id}/events`).then(j<JobEvent[]>),
  bedClear: (id: string, c: { bed_empty: boolean; plate_matches: boolean; filament_matches: boolean }) => post(`/api/printers/${id}/bed/clear`, c).then(j<Bed>),
  bedOccupied: (id: string) => post(`/api/printers/${id}/bed/occupied`).then(j<Bed>),
  pipeline: () => fetch("/api/admin/pipeline").then(j<PipelineSettings>),
  savePipeline: (s: Partial<PipelineSettings>) => send("PUT", "/api/admin/pipeline", s).then(j<PipelineSettings>),
  stats: () => fetch("/api/admin/stats").then(j<Stats>),
  job: (id: string) => fetch(`/api/jobs/${id}`).then(j<Job>),
  jobs: () => fetch("/api/jobs").then(j<Job[]>),
  queue: (id: string) => post(`/api/jobs/${id}/queue`).then(j<Job>),
  print: (id: string) => post(`/api/jobs/${id}/print`).then(j<{ ok: boolean; started: boolean; file: string }>),
  printers: () => fetch("/api/printers").then(j<Printer[]>),
  gateways: () => fetch("/api/admin/gateways").then(j<Gateway[]>),
  addGateway: (name: string) => send("POST", "/api/admin/gateways", { name }).then(j<Gateway & { token: string }>),
  deleteGateway: (id: string) => send("DELETE", `/api/admin/gateways/${id}`).then(j<{ ok: boolean }>),
  addPrinter: (p: PrinterInput) => send("POST", "/api/admin/printers", p).then(j<Printer>),
  updatePrinter: (id: string, p: PrinterInput) => send("PUT", `/api/admin/printers/${id}`, p).then(j<Printer>),
  deletePrinter: (id: string) => send("DELETE", `/api/admin/printers/${id}`).then(j<{ ok: boolean }>),
};

export const modelUrl = (id: string) => `/api/models/${id}/preview.stl`;
export const gcodeUrl = (jobId: string) => `/api/jobs/${jobId}/gcode`;
