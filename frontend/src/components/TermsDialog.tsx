import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api, type TermsDoc } from "../api";

/** Terms and safety rules (3.6): read and accept before the first print, and again after an admin changes them. */
export default function TermsDialog({ onAccepted, onClose }: { onAccepted: () => void; onClose: () => void }) {
  const [terms, setTerms] = useState<TermsDoc | null>(null);
  const [again, setAgain] = useState(false);
  const [ticked, setTicked] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.terms().then((t) => { setTerms(t.terms); setAgain(!!t.accepted); if (!t.needed) onAccepted(); }).catch((e) => setErr(e.message)); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const accept = async () => {
    setBusy(true); setErr("");
    try { await api.acceptTerms(); window.dispatchEvent(new Event("user-changed")); onAccepted(); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" role="dialog" aria-label="Terms and safety rules" onClick={(e) => e.stopPropagation()}>
        <header><h3>Terms and safety rules</h3><button className="icon" onClick={onClose} aria-label="Close"><X size={18} /></button></header>
        <div className="modal-body">
          {!terms && !err && <p className="muted">Loading…</p>}
          {terms && <>
            <p className="muted">{again ? "The rules have changed since you last accepted them. Please read them again before your next print." : "Please read these before your first print."}</p>
            <div className="terms-text" tabIndex={0}>{terms.text}</div>
            <label className="check"><input type="checkbox" checked={ticked} onChange={(e) => setTicked(e.target.checked)} /> I have read the terms and safety rules and will follow them</label>
          </>}
          {err && <p className="form-error" role="alert">{err}</p>}
          <div className="row-actions">
            <button className="btn primary" disabled={!terms || !ticked || busy} onClick={accept}>Accept</button>
            <button className="btn" onClick={onClose}>Not now</button>
          </div>
        </div>
      </div>
    </div>
  );
}
