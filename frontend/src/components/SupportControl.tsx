import { AlertTriangle } from "lucide-react";

type Value = number | string | boolean;
export const SUPPORT_KEYS = ["enable_support", "support_type", "support_on_build_plate_only", "support_threshold_angle"];

/** Supports in plain words: off, or normal or tree, with the two choices people actually make. The full list stays in All settings. */
export default function SupportControl({ settings, onChange, showOverhang, onToggleOverhang, overhangPercent }: {
  settings: Record<string, Value>; onChange: (key: string, v: Value) => void;
  showOverhang: boolean; onToggleOverhang: () => void; overhangPercent: number | null;
}) {
  const on = !!settings.enable_support;
  const kind = String(settings.support_type ?? "normal(auto)").startsWith("tree") ? "tree" : "normal";
  const pick = (v: "off" | "normal" | "tree") => {
    if (v === "off") { onChange("enable_support", false); return; }
    onChange("enable_support", true);
    onChange("support_type", v === "tree" ? "tree(auto)" : "normal(auto)");
  };
  const current = on ? kind : "off";
  return (
    <section className="support-ctl" aria-label="Supports">
      <div className="lbl">Supports</div>
      <div className="seg" role="radiogroup" aria-label="Supports">
        {([["off", "None"], ["normal", "Normal"], ["tree", "Tree"]] as const).map(([v, label]) => (
          <button key={v} role="radio" aria-checked={current === v} className={current === v ? "on" : ""} onClick={() => pick(v)}>{label}</button>
        ))}
      </div>
      {on && (
        <>
          <label className="check-row"><input type="checkbox" checked={!!settings.support_on_build_plate_only} onChange={(e) => onChange("support_on_build_plate_only", e.target.checked)} /> Only from the build plate</label>
          <label className="check-row angle">Overhang angle
            <span className="num"><input type="number" min={1} max={90} value={Number(settings.support_threshold_angle) || 30} aria-label="Support threshold angle in degrees"
              onChange={(e) => e.target.value !== "" && onChange("support_threshold_angle", Number(e.target.value))} /><em>°</em></span>
          </label>
        </>
      )}
      <button className={"mini wide" + (showOverhang ? " on" : "")} aria-pressed={showOverhang} onClick={onToggleOverhang}><AlertTriangle size={14} /> Show what needs support</button>
      {showOverhang && overhangPercent != null && (
        <p className={overhangPercent >= 15 && !on ? "form-error" : "muted"}>
          {overhangPercent < 0.5 ? "Nothing needs support at this orientation (estimate)."
            : `About ${Math.round(overhangPercent)}% of the surface needs support at this orientation (estimate, shown in red).${on ? "" : " Supports are off."}`}
        </p>
      )}
    </section>
  );
}
