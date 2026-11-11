import { useState } from "react";
import { Search } from "lucide-react";
import type { Catalog, Field } from "../api";

export type Mode = "basic" | "simple" | "advanced";
const RANK: Record<Mode, number> = { basic: 0, simple: 1, advanced: 2 };
type Value = number | string | boolean;

type Props = {
  catalog: Catalog;
  values: Record<string, Value>;
  baseline: Record<string, Value>; // preset defaults, used to flag edited values
  onChange: (key: string, v: Value) => void;
};

export default function SettingsPanel({ catalog, values, baseline, onChange }: Props) {
  const [mode, setMode] = useState<Mode>("advanced");
  const [tab, setTab] = useState(catalog.settings_schema[0].tab);
  const [query, setQuery] = useState("");

  const visible = (f: Field) => RANK[f.level] <= RANK[mode];
  const q = query.trim().toLowerCase();
  const tabs = catalog.settings_schema;
  const active = tabs.find((t) => t.tab === tab) ?? tabs[0];

  const groups = q
    ? tabs.flatMap((t) => t.groups.map((g) => ({ ...g, fields: g.fields.filter((f) => visible(f) && f.label.toLowerCase().includes(q)) })))
        .filter((g) => g.fields.length)
    : active.groups.map((g) => ({ ...g, fields: g.fields.filter(visible) })).filter((g) => g.fields.length);

  return (
    <div className="settings">
     <div className="settings-head">
      <div className="segmented" role="tablist">
        {(["basic", "simple", "advanced"] as Mode[]).map((m) => (
          <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>
            {m[0].toUpperCase() + m.slice(1)}
          </button>
        ))}
      </div>
      <label className="search">
        <Search size={18} />
        <input placeholder="Search for a setting" value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      {!q && (
        <div className="tabs">
          {tabs.map((t) => (
            <button key={t.tab} className={t.tab === active.tab ? "on" : ""} onClick={() => setTab(t.tab)}>{t.tab}</button>
          ))}
        </div>
      )}
     </div>
      <div className="fields">
        {groups.map((g) => (
          <section key={g.name}>
            <h4>{g.name}</h4>
            {g.fields.map((f) => (
              <FieldRow key={f.key} f={f} value={values[f.key]} edited={values[f.key] !== baseline[f.key]} onChange={(v) => onChange(f.key, v)} />
            ))}
          </section>
        ))}
        {!groups.length && <p className="muted pad">No settings match.</p>}
      </div>
    </div>
  );
}

function FieldRow({ f, value, edited, onChange }: { f: Field; value: Value; edited: boolean; onChange: (v: Value) => void }) {
  const step = f.integer ? 1 : f.unit === "mm/s" ? 5 : f.unit === "mm" ? 0.01 : "any";
  return (
    <div className={"field" + (edited ? " edited" : "")}>
      <span>{f.label}</span>
      {f.type === "bool" ? (
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
      ) : f.type === "select" ? (
        <select value={String(value)} onChange={(e) => onChange(e.target.value)}>
          {f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      ) : f.type === "number" || f.type === "percent" ? (
        <div className="num">
          <input type="number" step={step} min={f.min} max={f.max} value={value as number}
            onChange={(e) => e.target.value !== "" && onChange(Number(e.target.value))} />
          {f.unit && <em>{f.unit}</em>}
        </div>
      ) : (
        <div className="num">
          <input type="text" inputMode={f.type === "number_or_percent" ? "decimal" : undefined} value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value)} />
          {f.unit && <em>{f.unit}</em>}
        </div>
      )}
    </div>
  );
}
