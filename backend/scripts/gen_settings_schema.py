"""Generate backend/app/settings_schema.json from Bambu Studio's own source, so the settings panel has the same pages, groups,
order, labels, units, ranges and choices as Bambu Studio (Milestone 1.1).

  python backend/scripts/gen_settings_schema.py --tag v02.09.01.70          # downloads Tab.cpp and PrintConfig.cpp
  python backend/scripts/gen_settings_schema.py --src /path/with/the/two/files

Use the tag of the Bambu Studio release pinned in the Dockerfile, and run this again whenever that changes. Pages and groups come
from TabPrint::build() in Tab.cpp; each option's label, unit, type, range, choices and default come from PrintConfig.cpp. What is
left out on purpose: options Bambu Studio only shows in its developer mode, anything that runs a program (post-processing scripts),
free-text notes, and the long explanatory tooltips (Bambu Studio is AGPL-3.0 licensed prose; see ROADMAP "Risks").
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/bambulab/BambuStudio/{tag}/{path}"
FILES = {"Tab.cpp": "src/slic3r/GUI/Tab.cpp", "PrintConfig.cpp": "src/libslic3r/PrintConfig.cpp"}
EXCLUDE = {"post_process", "process_notes", "filename_format"}      # run a program / free text / not a print setting
STR = re.compile(r'"((?:[^"\\]|\\.)*)"')


def literal(text: str) -> str:
    """Join the C string literals in `text` ("a" "b" -> ab) and undo escapes."""
    return "".join(STR.findall(text)).replace('\\"', '"').replace("\\n", " ").replace("\\\\", "\\")


def parse_definitions(cpp: str) -> dict[str, dict]:
    defs: dict[str, dict] = {}
    starts = [(m.start(), m.group(1), m.group(2)) for m in re.finditer(r'def\s*=\s*this->add\(\s*"(\w+)"\s*,\s*(co\w+)\s*\)\s*;', cpp)]
    for i, (pos, key, ctype) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(cpp)
        block = cpp[pos:end]
        d: dict = {"key": key, "ctype": ctype, "mode": "simple"}
        if m := re.search(r"def->label\s*=\s*(?:_?L\()?((?:\s*\"(?:[^\"\\]|\\.)*\")+)", block):
            d["label"] = literal(m.group(1))
        if m := re.search(r"def->sidetext\s*=\s*(?:_?L\()?((?:\s*\"(?:[^\"\\]|\\.)*\")+)", block):
            d["unit"] = literal(m.group(1))
        if m := re.search(r"def->min\s*=\s*(-?[\d.]+)", block):
            d["min"] = float(m.group(1))
        if m := re.search(r"def->max\s*=\s*(-?[\d.]+)", block):
            d["max"] = float(m.group(1))
        if m := re.search(r"def->mode\s*=\s*(com\w+)", block):
            d["mode"] = {"comSimple": "simple", "comAdvanced": "advanced", "comDevelop": "develop"}[m.group(1)]
        vals = re.findall(r"def->enum_values\.(?:push|emplace)_back\(\s*(?:_?L\()?\s*\"((?:[^\"\\]|\\.)*)\"", block)
        labels = re.findall(r"def->enum_labels\.(?:push|emplace)_back\(\s*(?:_?L\()?\s*\"((?:[^\"\\]|\\.)*)\"", block)
        if vals:
            d["options"] = [{"value": v, "label": labels[j] if j < len(labels) else v} for j, v in enumerate(vals)]
        if m := re.search(r"set_default_value\(new ConfigOption(\w+)(?:<[^>]*>)?\s*[({]\s*([-\w.\"]+)", block):
            d["default_raw"] = m.group(2)
        if m := re.search(r"def->enum_values\s*=\s*(\w+)->enum_values", block):    # choices copied from another option
            holder = m.group(1)
            src = next((k for (q, k, _t) in starts if re.search(rf"\b{holder}\s*=\s*def\s*;", cpp[q:starts[[x[1] for x in starts].index(k) + 1][0] if [x[1] for x in starts].index(k) + 1 < len(starts) else len(cpp)])), None)
            if src and src in defs and "options" in defs[src]:
                d["options"] = defs[src]["options"]
        defs[key] = d
    return defs


def parse_layout(tab_cpp: str) -> list[dict]:
    start = tab_cpp.index("void TabPrint::build()")
    end = tab_cpp.index("\nvoid ", start + 10)
    body = tab_cpp[start:end]
    pages: list[dict] = []
    group = None
    skip_depth = 0
    for raw in body.split("\n"):
        line = raw.strip()
        if line.startswith("#if 0") or line.startswith("#ifdef"):
            skip_depth += 1
        elif line.startswith("#endif") and skip_depth:
            skip_depth -= 1
        if skip_depth or line.startswith("//"):
            continue
        if m := re.search(r'add_options_page\(L\("([^"]+)"\)', line):
            pages.append({"tab": m.group(1), "groups": []})
            group = None
        elif m := re.search(r'new_optgroup\(L\("([^"]+)"\)', line):
            group = {"name": m.group(1), "keys": []}
            pages[-1]["groups"].append(group)
        elif group is not None:
            for pat in (r'append_single_option_line\("(\w+)"', r'append_multi_nozzle_auto_line\(optgroup, "(\w+)"',
                        r'append_nozzle_scaled_option\(optgroup, "(\w+)"', r'line\.append_option\(optgroup->get_option\("(\w+)"',
                        r'(?<!line\.append_option\()optgroup->get_option\("(\w+)"\);'):
                for key in re.findall(pat, line):
                    if key not in group["keys"]:
                        group["keys"].append(key)
    return pages


def default_value(d: dict):
    raw = d.get("default_raw")
    t = d["ctype"]
    if raw is None:
        return None
    if t in ("coBool", "coBools"):
        return raw == "true"
    if t in ("coInt", "coInts", "coFloat", "coFloats", "coPercent", "coPercents"):
        try:
            v = float(raw)
        except ValueError:
            return None
        return int(v) if t in ("coInt", "coInts") or v == int(v) and t in ("coPercent", "coPercents") else v
    if t in ("coString", "coStrings"):
        return raw.strip('"')
    return None


FIELD_TYPE = {"coFloat": "number", "coFloats": "number", "coInt": "number", "coInts": "number", "coPercent": "percent", "coPercents": "percent",
              "coBool": "bool", "coBools": "bool", "coEnum": "select", "coString": "text", "coStrings": "text",
              "coFloatOrPercent": "number_or_percent", "coFloatsOrPercents": "number_or_percent"}


def build(tab_cpp: str, config_cpp: str, tag: str) -> dict:
    defs = parse_definitions(config_cpp)
    out_tabs, missing, skipped = [], [], []
    for page in parse_layout(tab_cpp):
        groups = []
        for g in page["groups"]:
            fields = []
            for key in g["keys"]:
                d = defs.get(key)
                if key in EXCLUDE:
                    continue
                if d is None:
                    missing.append(key)
                    continue
                kind = FIELD_TYPE.get(d["ctype"])
                if kind is None or d["mode"] == "develop" or "label" not in d:
                    skipped.append(key)
                    continue
                f = {"key": key, "label": d["label"], "type": kind, "level": d["mode"]}
                if kind == "percent":
                    f["unit"] = "%"
                elif d.get("unit"):
                    f["unit"] = d["unit"]
                if d["ctype"] in ("coInt", "coInts"):
                    f["integer"] = True
                for k in ("min", "max"):
                    if k in d:
                        f[k] = d[k]
                if kind == "select":
                    if not d.get("options"):
                        skipped.append(key)
                        continue
                    f["options"] = d["options"]
                dv = default_value(d)
                if dv is not None:
                    f["default"] = dv
                fields.append(f)
            if fields:
                groups.append({"name": g["name"], "fields": fields})
        if groups:
            out_tabs.append({"tab": page["tab"], "groups": groups})
    return {"source": f"Bambu Studio {tag}: TabPrint::build (pages, groups, order) and PrintConfig.cpp (labels, units, ranges, choices, defaults)",
            "tabs": out_tabs, "_notes": {"missing_definitions": missing, "skipped": skipped}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v02.09.01.70")
    ap.add_argument("--src", help="directory holding Tab.cpp and PrintConfig.cpp (skips the download)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "app" / "settings_schema.json"))
    a = ap.parse_args()
    texts = {}
    for name, path in FILES.items():
        if a.src:
            texts[name] = (Path(a.src) / name).read_text(encoding="utf-8", errors="replace")
        else:
            with urllib.request.urlopen(RAW.format(tag=a.tag, path=path), timeout=60) as r:
                texts[name] = r.read().decode("utf-8", "replace")
    schema = build(texts["Tab.cpp"], texts["PrintConfig.cpp"], a.tag)
    notes = schema.pop("_notes")
    Path(a.out).write_text(json.dumps(schema, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(len(g["fields"]) for t in schema["tabs"] for g in t["groups"])
    print(f"{len(schema['tabs'])} pages, {n} options -> {a.out}", file=sys.stderr)
    print("not in PrintConfig.cpp:", notes["missing_definitions"], file=sys.stderr)
    print("skipped (developer mode, unsupported type or no label):", len(notes["skipped"]), file=sys.stderr)


if __name__ == "__main__":
    main()
