import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { api, type NotifyPref } from "../api";

/** A person's own choices: which emails they want. Each switch saves at once. */
export default function NotifyPrefs({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [prefs, setPrefs] = useState<NotifyPref[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => { if (open) api.myNotifications().then(setPrefs).catch((e) => setErr(e.message)); }, [open]);
  const set = async (kind: string, on: boolean) => {
    setErr("");
    setPrefs((p) => p.map((x) => (x.kind === kind ? { ...x, on } : x)));
    try { setPrefs(await api.setMyNotifications({ [kind]: on })); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dlg-overlay" />
        <Dialog.Content className="dlg-content" aria-describedby={undefined}>
          <div className="dlg-head"><Dialog.Title>Notifications</Dialog.Title><Dialog.Close className="icon" aria-label="Close"><X size={18} /></Dialog.Close></div>
          <p className="muted">Choose which emails you get. Changes save as you click.</p>
          <div className="dlg-list">
            {prefs.map((p) => (
              <label className="check-row" key={p.kind}><input type="checkbox" checked={p.on} onChange={(e) => set(p.kind, e.target.checked)} /> {p.label}</label>
            ))}
          </div>
          {err && <p className="form-error" role="alert">{err}</p>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
