import type { ReactNode } from "react";
import { X } from "lucide-react";

/** The same content shows as a pop-up (the workspace still uses a few) or inline as the body of a page. */
export default function Frame({ title, inline, cards, onClose, wide, header, children }: {
  title: string; inline?: boolean; cards?: boolean; onClose?: () => void; wide?: boolean; header?: ReactNode; children: ReactNode;
}) {
  if (inline) return <div className="page-body">{header}<div className={"page-stack" + (cards ? " cards" : "")}>{children}</div></div>;
  return (
    <div className="modal-back" onClick={onClose}>
      <div className={"modal" + (wide ? " wide-modal" : "")} role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <header><h3>{title}</h3>{header}<button className="icon" onClick={onClose} aria-label="Close"><X size={18} /></button></header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
