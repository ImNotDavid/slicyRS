import { useCallback, useEffect, useState } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Bell, Box, ClipboardList, Columns3, ListChecks, LogOut, Moon, PanelLeft, Printer as PrinterIcon, ShieldCheck, Sun, SunMoon } from "lucide-react";
import { api, type Catalog, type Job, type JobPage as JobPageData, type Printer, type User } from "./api";
import JobPage from "./components/JobPage";
import QueuePage from "./components/QueuePage";
import App from "./App";
import FleetModal from "./components/FleetModal";
import { Harry, Lettermark } from "./components/Brand";
import NotifyPrefs from "./components/NotifyPrefs";
import ErrorBoundary from "./components/ErrorBoundary";
import JobsGrid from "./components/JobsGrid";
import AdminModal from "./components/AdminModal";

type Page = "workspace" | "jobs" | "alljobs" | "queue" | "job" | "fleet" | "admin";
type Theme = "auto" | "light" | "dark";
const TITLES: Record<Page, string> = { workspace: "Workspace", jobs: "My jobs", alljobs: "All jobs", queue: "Queue", job: "Job", fleet: "Fleet", admin: "Admin" };
const HASH: Record<Page, string> = { workspace: "workspace", jobs: "jobs", alljobs: "all-jobs", queue: "queue", job: "jobs", fleet: "fleet", admin: "admin" };

// pages only some people have: Fleet for operators and admins, Admin for admins
const allowed = (p: Page, admin: boolean, operator: boolean) => (p === "admin" ? admin : p === "fleet" || p === "alljobs" || p === "queue" ? operator : true);
// routes: #/workspace, #/jobs, #/jobs/<id>, #/all-jobs, #/fleet, #/admin
const readRoute = (admin: boolean, operator: boolean): { page: Page; jobId: string | null } => {
  const [head, id] = window.location.hash.replace(/^#\/?/, "").split("/");
  if (head === "jobs" && id) return { page: "job", jobId: id };
  const p = (Object.keys(HASH) as Page[]).find((k) => HASH[k] === head && k !== "job");
  return { page: p && allowed(p, admin, operator) ? p : "workspace", jobId: null };
};
const storedTheme = (): Theme => { try { return (localStorage.getItem("theme") as Theme) || "auto"; } catch { return "auto"; } };

/** Pages with a left menu (a bottom tab bar on phones). The workspace stays mounted so a plate in progress survives a look at Jobs. */
export default function Shell({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const isAdmin = user.role === "admin";
  const isOperator = isAdmin || !!user.operator;
  const [route, setRoute] = useState(() => readRoute(isAdmin, isOperator));
  const page = route.page;
  const [reuseRequest, setReuseRequest] = useState<JobPageData | null>(null);
  const [wide, setWide] = useState(false);                   // the workspace menu is an icon strip unless expanded
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const [prefsOpen, setPrefsOpen] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [printers, setPrinters] = useState<Printer[]>([]);

  useEffect(() => {
    const onHash = () => setRoute(readRoute(isAdmin, isOperator));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [isAdmin, isOperator]);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch { /* private window: the choice just is not remembered */ }
  }, [theme]);

  const refreshPrinters = useCallback(() => api.printers().then(setPrinters).catch(() => {}), []);
  useEffect(() => {
    if ((page === "admin" || page === "job" || page === "jobs" || page === "alljobs") && !catalog) api.catalog().then(setCatalog).catch(() => {});
    if (page !== "fleet" && page !== "admin") return;
    refreshPrinters();
    const t = setInterval(refreshPrinters, 5000);
    return () => clearInterval(t);
  }, [page, catalog, refreshPrinters]);

  const go = (p: Page) => { window.location.hash = "/" + HASH[p]; };
  const openJobPage = (id: string) => { window.location.hash = "/jobs/" + id; };
  // staff pages carry the yellow staff marker so nobody mistakes them for something every member has
  const items: { id: Page; icon: typeof Box; staff?: string }[] = [
    { id: "workspace", icon: Box }, { id: "jobs", icon: ListChecks },
    ...(isOperator ? [{ id: "alljobs" as Page, icon: ClipboardList, staff: "Operators and admins only" }] : []),
    ...(isOperator ? [{ id: "queue" as Page, icon: Columns3, staff: "Operators and admins only" }] : []),
    ...(isOperator ? [{ id: "fleet" as Page, icon: PrinterIcon, staff: "Operators and admins only" }] : []),
    ...(isAdmin ? [{ id: "admin" as Page, icon: ShieldCheck, staff: "Admins only" }] : []),
  ];
  const staffNote = items.find((i) => i.id === page)?.staff;
  const compact = page === "workspace" && !wide;
  const initials = (user.name || user.email).split(/[\s.@]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  const nextTheme: Theme = theme === "auto" ? "light" : theme === "light" ? "dark" : "auto";
  const ThemeIcon = theme === "auto" ? SunMoon : theme === "light" ? Sun : Moon;

  return (
    <div className={"shell" + (compact ? " compact" : "")}>
      <aside className="sidebar">
        <div className="brand"><Harry height={28} className="brand-harry" /><div className="brand-text"><span className="mark">slicy<b>RS</b></span><Lettermark height={14} className="brand-letters" /></div></div>
        <nav aria-label="Main">
          {items.map(({ id, icon: Icon, staff }, i) => (
            <div key={id} className="nav-wrap">
              {staff && !items[i - 1]?.staff && <div className="staff-rule" aria-hidden="true"><span>Staff</span></div>}
              <button className={"nav" + (page === id || (page === "job" && id === "jobs") ? " on" : "") + (staff ? " staff" : "")} aria-current={page === id ? "page" : undefined}
                title={staff ? `${TITLES[id]} (${staff.toLowerCase()})` : TITLES[id]} onClick={() => go(id)}>
                <Icon size={18} strokeWidth={2} /><span>{TITLES[id]}</span>
              </button>
            </div>
          ))}
        </nav>
        <div className="side-foot">
          <div className="hatch-strip" aria-hidden="true" />
          {page === "workspace" && <button className="nav expand" title={wide ? "Collapse menu" : "Expand menu"} aria-label={wide ? "Collapse menu" : "Expand menu"} onClick={() => setWide(!wide)}><PanelLeft size={18} /></button>}
          <button className="nav" title={`Theme: ${theme}. Click for ${nextTheme}`} aria-label={`Theme: ${theme}`} onClick={() => setTheme(nextTheme)}><ThemeIcon size={18} /><span>Theme: {theme}</span></button>
          <Menu.Root>
            <Menu.Trigger className="who-box" aria-label="Account menu">
              <span className="av">{initials}</span>
              <span className="who-text"><b>{user.name || user.email}</b><i>{isAdmin ? "Admin" : user.operator ? "Operator" : "Member"}</i></span>
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Content className="acct-menu" side="top" align="start" sideOffset={8}>
                <div className="menu-head"><strong>{user.name || user.email}</strong><span className="muted">{user.email}</span></div>
                <Menu.Item className="menu-item" onSelect={() => setPrefsOpen(true)}><Bell size={16} /> Notifications</Menu.Item>
                <Menu.Item className="menu-item" onSelect={onSignOut}><LogOut size={16} /> Sign out</Menu.Item>
                <Menu.Item className="menu-item" onSelect={async () => { await api.logoutAll().catch(() => {}); onSignOut(); }}><LogOut size={16} /> Sign out all devices</Menu.Item>
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
        </div>
      </aside>

      <NotifyPrefs open={prefsOpen} onOpenChange={setPrefsOpen} />
      <div className="page" hidden={page !== "workspace"}>
        <ErrorBoundary what="The workspace"><App user={user}
          reuseRequest={reuseRequest} onReused={() => { setReuseRequest(null); go("workspace"); }} /></ErrorBoundary>
      </div>
      {page !== "workspace" && (
        <main className="page">
          {page !== "job" && <header className="page-head"><h1>{TITLES[page]}</h1>{staffNote ? <span className="staff-tag">{staffNote}</span> : <span className="hatch-chip wide" aria-hidden="true" />}</header>}
          {page === "jobs" && <JobsGrid scope="mine" catalog={catalog} onOpen={openJobPage} />}
          {page === "queue" && isOperator && <QueuePage onOpenJob={openJobPage} />}
          {page === "alljobs" && isOperator && <JobsGrid scope="all" catalog={catalog} onOpen={openJobPage} />}
          {page === "job" && route.jobId && <JobPage id={route.jobId} catalog={catalog} onBack={() => window.history.back()} onReuse={setReuseRequest} />}
          {page === "fleet" && isOperator && <FleetModal inline printers={printers} onChanged={refreshPrinters} onOpenJob={openJobPage} />}
          {page === "admin" && isAdmin && (catalog ? <AdminModal inline catalog={catalog} me={user} onPrintersChanged={refreshPrinters} /> : <p className="muted">Loading…</p>)}
        </main>
      )}
    </div>
  );
}
