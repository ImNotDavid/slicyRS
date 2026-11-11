import * as THREE from "three";

// Colours for feature markers (";TYPE:" from Orca, "; FEATURE:" from Bambu Studio); unlabelled moves use "default".
const TYPE_COLORS: Record<string, number> = {
  "Outer wall": 0xff7043, "Inner wall": 0xffd54f, "Overhang wall": 0x2962ff, "Sparse infill": 0xb71c1c,
  "Internal solid infill": 0x9575cd, "Top surface": 0xef5350, "Bottom surface": 0x26c6da,
  "Bridge": 0x7e57c2, "Gap infill": 0xffffff, "Support": 0x66bb6a, "Support interface": 0x26a69a, "Support transition": 0x9ccc65,
  "Skirt": 0xaaaaaa, "Brim": 0xaaaaaa, "Prime tower": 0xe0e0e0, "Floating vertical shell": 0x90a4ae, default: 0x4f8cff,
};
const SKIP_TYPES = new Set(["Custom"]); // start/end purge lines, not part of the model

export const TRAVEL = "Travel moves";
const TRAVEL_COLOR = 0x8a8f98;

export type FeatureInfo = { name: string; color: number; segments: number };
export type GcodeLayers = {
  group: THREE.Group; layerZ: number[]; layers: THREE.LineSegments[];
  features: FeatureInfo[];                                             // what the G-code contains, in order of first appearance (travel moves last)
  apply: (hidden: ReadonlySet<string>, travel: boolean) => void;       // show only these features (and travel moves if asked)
  visibleSegments: (layer: number) => number;                          // segments currently drawn in a layer, in print order
};

type Layer = { pos: number[]; col: number[]; kind: number[] };           // kind: index into the feature list, -1 for a travel move

export function parseGcode(text: string): GcodeLayers {
  const perLayer: Layer[] = [];
  const layerZ: number[] = [];
  const featureIndex = new Map<string, number>();
  const featureNames: string[] = [];
  let travelSegments = 0;
  let x = 0, y = 0, z = 0, e = 0, relE = true, type = "default";
  let cur: Layer | null = null;
  let layerHeight = 0; // z recorded by layer markers; moves within a layer (z-hops) are drawn at this height
  let markers = false;
  const color = new THREE.Color();
  const counts: number[] = [];

  const newLayer = (zv: number) => {
    cur = { pos: [], col: [], kind: [] };
    perLayer.push(cur);
    layerZ.push(zv);
    layerHeight = zv;
  };

  const seg = (x0: number, y0: number, x1: number, y1: number, travel = false) => {
    if (SKIP_TYPES.has(type)) return;
    if (!cur) newLayer(z);
    const zz = markers ? layerHeight : z;
    let kind = -1;
    if (travel) {
      color.setHex(TRAVEL_COLOR);
      travelSegments++;
    } else {
      let idx = featureIndex.get(type);
      if (idx === undefined) { idx = featureNames.length; featureIndex.set(type, idx); featureNames.push(type); counts.push(0); }
      kind = idx;
      counts[idx]++;
      color.setHex(TYPE_COLORS[type] ?? TYPE_COLORS.default);
    }
    cur!.pos.push(x0, y0, zz, x1, y1, zz);
    cur!.col.push(color.r, color.g, color.b, color.r, color.g, color.b);
    cur!.kind.push(kind);
  };

  for (const raw of text.split("\n")) {
    if (raw.charCodeAt(0) === 59) { // ';' comment line
      if (raw.startsWith(";TYPE:")) type = raw.slice(6).trim();
      else if (raw.startsWith("; FEATURE:")) type = raw.slice(10).trim();
      else if (raw.startsWith("; Z_HEIGHT:") || raw.startsWith(";Z:")) {
        markers = true;
        newLayer(parseFloat(raw.slice(raw.indexOf(":") + 1)));
      }
      continue;
    }
    if (raw.startsWith("M82")) { relE = false; continue; }
    if (raw.startsWith("M83")) { relE = true; continue; }
    const isArc = raw.startsWith("G2") || raw.startsWith("G3");
    if (!isArc && !raw.startsWith("G1") && !raw.startsWith("G0")) continue;
    let nx = x, ny = y, nz = z, ne = e, hasE = false, i = 0, j = 0;
    for (const tok of raw.split(";")[0].split(" ")) {
      const v = parseFloat(tok.slice(1));
      if (Number.isNaN(v)) continue;
      switch (tok[0]) {
        case "X": nx = v; break;
        case "Y": ny = v; break;
        case "Z": nz = v; break;
        case "E": ne = v; hasE = true; break;
        case "I": i = v; break;
        case "J": j = v; break;
      }
    }
    const extruding = hasE && (relE ? ne > 0 : ne > e) && (nx !== x || ny !== y);
    if (nz !== z) {
      z = nz;
      if (!markers && (!cur || layerZ[layerZ.length - 1] !== z)) newLayer(z); // no layer markers: split on Z
    }
    if (extruding) {
      if (isArc) {
        // G2 = clockwise, G3 = counter-clockwise, centre = start + (I, J); approximate with short chords
        const cx = x + i, cy = y + j, r = Math.hypot(x - cx, y - cy);
        const a0 = Math.atan2(y - cy, x - cx);
        let a1 = Math.atan2(ny - cy, nx - cx);
        const cw = raw.startsWith("G2");
        if (cw && a1 >= a0) a1 -= 2 * Math.PI;
        if (!cw && a1 <= a0) a1 += 2 * Math.PI;
        const steps = Math.max(2, Math.ceil(Math.abs(a1 - a0) * r / 0.6));
        let px = x, py = y;
        for (let s = 1; s <= steps; s++) {
          const a = a0 + ((a1 - a0) * s) / steps;
          const qx = s === steps ? nx : cx + r * Math.cos(a);
          const qy = s === steps ? ny : cy + r * Math.sin(a);
          seg(px, py, qx, qy);
          px = qx; py = qy;
        }
      } else {
        seg(x, y, nx, ny);
      }
    }
    else if (!isArc && (nx !== x || ny !== y)) seg(x, y, nx, ny, true);   // a move without extruding
    x = nx; y = ny; e = relE ? 0 : ne;
  }

  const features: FeatureInfo[] = featureNames.map((name, i) => ({ name, color: TYPE_COLORS[name] ?? TYPE_COLORS.default, segments: counts[i] }));
  if (travelSegments) features.push({ name: TRAVEL, color: TRAVEL_COLOR, segments: travelSegments });

  const group = new THREE.Group();
  const layers: THREE.LineSegments[] = [];
  const data: Layer[] = [];
  const keptZ: number[] = [];
  const shown: number[] = [];
  perLayer.forEach((l, idx) => {
    if (!l.pos.length) return;
    const line = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ vertexColors: true }));
    group.add(line);
    layers.push(line);
    data.push(l);
    keptZ.push(layerZ[idx]);
    shown.push(0);
  });

  const apply = (hidden: ReadonlySet<string>, travel: boolean) => {
    const visible = featureNames.map((n) => !hidden.has(n));
    data.forEach((l, i) => {
      let n = 0;
      for (const k of l.kind) if (k < 0 ? travel : visible[k]) n++;
      const pos = new Float32Array(n * 6), col = new Float32Array(n * 6);
      let o = 0;
      l.kind.forEach((k, s) => {
        if (k < 0 ? !travel : !visible[k]) return;
        pos.set(l.pos.slice(s * 6, s * 6 + 6), o * 6);
        col.set(l.col.slice(s * 6, s * 6 + 6), o * 6);
        o++;
      });
      const g = layers[i].geometry;
      g.dispose();
      g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      g.setAttribute("color", new THREE.BufferAttribute(col, 3));
      g.setDrawRange(0, Infinity);
      shown[i] = n;
    });
  };
  apply(new Set(), false);                                     // travel moves are off until asked for
  return { group, layerZ: keptZ, layers, features, apply, visibleSegments: (i) => shown[i] ?? 0 };
}
