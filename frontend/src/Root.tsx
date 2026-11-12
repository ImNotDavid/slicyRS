import { useEffect, useRef, useState } from "react";
import { api, type AuthConfig, type User } from "./api";
import Shell from "./Shell";
import { Harry, Lettermark } from "./components/Brand";

const LOGIN_ERRORS: Record<string, string> = {
  not_invited: "Your account has not been invited yet. Ask a slicyRS admin to invite your Imperial email address.",
  wrong_tenant: "That account is not from the Imperial College tenant.",
  domain_not_allowed: "That email domain is not allowed.",
  disabled: "Your account has been disabled. Contact an admin.",
  no_email: "Your Imperial account did not share an email address with this app. Ask an admin to check the app's optional claims (email, upn).",
  sso_failed: "Sign-in with Imperial did not complete. Please try again.",
};

export default function Root() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null)).finally(() => setReady(true));
    const expired = () => setUser(null);
    window.addEventListener("auth-expired", expired);
    return () => window.removeEventListener("auth-expired", expired);
  }, []);

  if (!ready) return <div className="loading">Loading…</div>;
  if (!user) return <Login onSignedIn={setUser} />;
  return <Shell user={user} onSignOut={async () => { await api.logout(); setUser(null); }} />;
}

function Login({ onSignedIn }: { onSignedIn: (u: User) => void }) {
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [error, setError] = useState(() => {
    const code = new URLSearchParams(window.location.search).get("login_error");
    return code ? LOGIN_ERRORS[code] ?? "Sign-in failed." : "";
  });

  useEffect(() => {
    api.authConfig().then(setCfg).catch(() => setError("Cannot reach the server."));
    if (window.location.search) window.history.replaceState({}, "", window.location.pathname);
  }, []);

  const none = cfg && !cfg.sso && !cfg.dev && !cfg.email_login;
  return (
    <main className="login">
      <div className="login-card">
        <div className="login-marks"><Harry height={64} /><Lettermark height={26} /></div>
        <h1>slicyRS</h1>
        <p className="muted">Cloud slicing for the Bambu Lab printer fleet. The right printer is chosen for you.</p>
        {error && <p className="form-error" role="alert">{error}</p>}
        {cfg?.sso && <a className="btn primary big wide" href="/auth/login">Sign in with {cfg.sso_name}</a>}
        {cfg?.email_login && <EmailCode onSignedIn={onSignedIn} setError={setError} divider={!!cfg.sso} />}
        {none && <p className="muted">No sign-in method is configured on this server.</p>}
        {cfg?.dev && <DevLogin onSignedIn={onSignedIn} setError={setError} />}
        <p className="muted small">Access is by invitation only.</p>
      </div>
    </main>
  );
}

type LoginProps = { onSignedIn: (u: User) => void; setError: (m: string) => void };

/** Two steps: enter the invited email address, then the 6-digit code that was sent to it. */
function EmailCode({ onSignedIn, setError, divider }: LoginProps & { divider: boolean }) {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState("");
  const codeInput = useRef<HTMLInputElement>(null);

  useEffect(() => { if (step === "code") codeInput.current?.focus(); }, [step]);

  const send = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setError(""); setInfo(""); setBusy(true);
    try {
      const r = await api.emailRequest(email);
      setStep("code"); setCode("");
      setInfo(`If ${email.trim()} has been invited, a 6-digit code is on its way. It is valid for ${r.ttl_minutes} minutes.`);
    } catch (err) { setError((err as Error).message); }
    setBusy(false);
  };

  const verify = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(""); setBusy(true);
    try { onSignedIn(await api.emailVerify(email, code)); }
    catch (err) { setError((err as Error).message); setCode(""); }
    setBusy(false);
  };

  return (
    <div className="email-login">
      {divider && <div className="or"><span>or</span></div>}
      {step === "email" ? (
        <form onSubmit={send}>
          <label className="field-label" htmlFor="login-email">Email address</label>
          <input id="login-email" type="email" required autoComplete="email" placeholder="name@imperial.ac.uk"
            value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="btn dark wide" type="submit" disabled={busy || !email}>{busy ? "Sending…" : "Email me a sign-in code"}</button>
        </form>
      ) : (
        <form onSubmit={verify}>
          <p className="muted" role="status">{info}</p>
          <label className="field-label" htmlFor="login-code">6-digit code</label>
          <input id="login-code" ref={codeInput} className="code-input" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]*"
            maxLength={7} placeholder="000000" value={code} onChange={(e) => setCode(e.target.value.replace(/[^0-9 ]/g, ""))} />
          <button className="btn primary wide" type="submit" disabled={busy || code.replace(/\s/g, "").length !== 6}>{busy ? "Checking…" : "Sign in"}</button>
          <div className="link-row">
            <button type="button" className="link" onClick={send} disabled={busy}>Send a new code</button>
            <button type="button" className="link" onClick={() => { setStep("email"); setError(""); }}>Use a different email</button>
          </div>
        </form>
      )}
    </div>
  );
}

function DevLogin({ onSignedIn, setError }: LoginProps) {
  const [email, setEmail] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try { onSignedIn(await api.devLogin(email)); }
    catch (err) { setError(LOGIN_ERRORS[(err as Error).message] ?? (err as Error).message); }
  };
  return (
    <form onSubmit={submit} className="dev-login">
      <p className="warn-note">Development sign-in (no password). Disable DEV_AUTH on any public server.</p>
      <input type="email" required placeholder="invited email address" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Dev email" />
      <button className="btn wide" type="submit">Dev sign-in</button>
    </form>
  );
}
