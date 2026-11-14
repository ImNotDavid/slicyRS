import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { api, type User } from "../api";

/** The person's own groups (3.0): their role in each, and which one is their main group (picked first when submitting). */
export default function AccountGroups({ user, open, onOpenChange, onChanged }: { user: User; open: boolean; onOpenChange: (o: boolean) => void; onChanged: () => void }) {
  const [err, setErr] = useState("");
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dlg-overlay" />
        <Dialog.Content className="dlg-content" aria-describedby={undefined}>
          <div className="dlg-head"><Dialog.Title>My groups</Dialog.Title><Dialog.Close className="icon" aria-label="Close"><X size={18} /></Dialog.Close></div>
          <div className="dlg-list">
            {(user.groups ?? []).map((g) => (
              <div key={g.id} className={"home-row" + (g.main ? "" : " quiet")} style={{ cursor: "default" }}>
                <b>{g.name}</b>{g.main ? <span className="tg tg-pin">Main group</span> : <button className="mini" onClick={() => api.setMainGroup(g.id).then(onChanged).catch((e) => setErr(e.message))}>Make main</button>}
                <span className="muted">Your role: {g.role}</span></div>
            ))}
          </div>
          <p className="muted small-text">Your main group is chosen for you when you submit a print; you can pick another each time. To join a group, ask one of its operators.</p>
          {err && <p className="form-error" role="alert">{err}</p>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
