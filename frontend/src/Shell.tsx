import { useCallback, useEffect, useState } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Box, ListChecks, LogOut, Moon, PanelLeft, Printer as PrinterIcon, ShieldCheck, Sun, SunMoon } from "lucide-react";
import { api, type Catalog, type Job, type Printer, type User } from "./api";
import App from "./App";
import FleetModal from "./components/FleetModal";
import ErrorBoundary from "./components/ErrorBoundary";
import JobsModal from "./components/JobsModal";
import AdminModal from "./components/AdminModal";

type Page = "workspace" | "jobs" | "fleet" | "admin";
type Theme = "auto" | "light" | "dark";
const TITLES: Record<Page, string> = { workspace: "Workspace", jobs: "Jobs", fleet: "Fleet", admin: "Admin" };

const readPage = (admin: boolean): Page => {
  const p = window.location.hash.replace(/^#\/?/, "") as Page;
  return p in TITLES && (p !== "admin" || admin) ? p : "workspace";
};
const storedTheme = (): Theme => { try { return (localStorage.getItem("theme") as Theme) || "auto"; } catch { return "auto"; } };

/** Pages with a left menu (a bottom tab bar on phones). The workspace stays mounted so a plate in progress survives a look at Jobs. */
export default function Shell({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const isAdmin = user.role === "admin";
  const [page, setPage] = useState<Page>(() => readPage(isAdmin));
  const [wide, setWide] = useState(false);                   // the workspace menu is an icon strip unless expanded
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const [openRequest, setOpenRequest] = useState<Job | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [printers, setPrinters] = useState<Printer[]>([]);

  useEffect(() => {
    const onHash = () => setPage(readPage(isAdmin));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [isAdmin]);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch { /* private window: the choice just is not remembered */ }
  }, [theme]);

  const refreshPrinters = useCallback(() => api.printers().then(setPrinters).catch(() => {}), []);
  useEffect(() => {
    if (page !== "fleet" && page !== "admin") return;
    refreshPrinters();
    if (page === "admin" && !catalog) api.catalog().then(setCatalog).catch(() => {});
    const t = setInterval(refreshPrinters, 5000);
    return () => clearInterval(t);
  }, [page, catalog, refreshPrinters]);

  const go = (p: Page) => { window.location.hash = "/" + p; };
  const items: { id: Page; icon: typeof Box }[] = [
    { id: "workspace", icon: Box }, { id: "jobs", icon: ListChecks }, { id: "fleet", icon: PrinterIcon },
    ...(isAdmin ? [{ id: "admin" as Page, icon: ShieldCheck }] : []),
  ];
  const compact = page === "workspace" && !wide;
  const initials = (user.name || user.email).split(/[\s.@]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  const nextTheme: Theme = theme === "auto" ? "light" : theme === "light" ? "dark" : "auto";
  const ThemeIcon = theme === "auto" ? SunMoon : theme === "light" ? Sun : Moon;

  return (
    <div className={"shell" + (compact ? " compact" : "")}>
      <aside className="sidebar">
        <div className="brand"><span className="mark">slicy<b>RS</b></span><span className="hatch-chip" aria-hidden="true" /></div>
        <nav aria-label="Main">
          {items.map(({ id, icon: Icon }) => (
            <button key={id} className={"nav" + (page === id ? " on" : "")} aria-current={page === id ? "page" : undefined} title={TITLES[id]} onClick={() => go(id)}>
              <Icon size={18} strokeWidth={2} /><span>{TITLES[id]}</span>
            </button>
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
                <Menu.Item className="menu-item" onSelect={onSignOut}><LogOut size={16} /> Sign out</Menu.Item>
                <Menu.Item className="menu-item" onSelect={async () => { await api.logoutAll().catch(() => {}); onSignOut(); }}><LogOut size={16} /> Sign out all devices</Menu.Item>
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
        </div>
      </aside>

      <div className="page" hidden={page !== "workspace"}>
        <ErrorBoundary what="The workspace"><App user={user} openRequest={openRequest} onOpened={() => { setOpenRequest(null); go("workspace"); }} /></ErrorBoundary>
      </div>
      {page !== "workspace" && (
        <main className="page">
          <header className="page-head"><h1>{TITLES[page]}</h1><span className="hatch-chip wide" aria-hidden="true" /></header>
          {page === "jobs" && <JobsModal inline showOwner={isAdmin || !!user.operator} onOpen={setOpenRequest} />}
          {page === "fleet" && <FleetModal inline printers={printers} onChanged={refreshPrinters} />}
          {page === "admin" && isAdmin && (catalog ? <AdminModal inline catalog={catalog} me={user} onPrintersChanged={refreshPrinters} /> : <p className="muted">Loading…</p>)}
        </main>
      )}
    </div>
  );
}
