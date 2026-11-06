import * as THREE from "three";

// Colours for feature markers (";TYPE:" from Orca, "; FEATURE:" from Bambu Studio); unlabelled moves use "default".
const TYPE_COLORS: Record<string, number> = {
  "Outer wall": 0xff7043, "Inner wall": 0xffd54f, "Overhang wall": 0x2962ff, "Sparse infill": 0xb71c1c,
  "Internal solid infill": 0x9575cd, "Top surface": 0xef5350, "Bottom surface": 0x26c6da,
  "Bridge": 0x7e57c2, "Gap infill": 0xffffff, "Support": 0x66bb6a, "Support interface": 0x26a69a,
  "Skirt": 0xaaaaaa, "Brim": 0xaaaaaa, "Prime tower": 0xe0e0e0, "Floating vertical shell": 0x90a4ae, default: 0x4f8cff,
};
const SKIP_TYPES = new Set(["Custom"]); // start/end purge lines, not part of the model

export type GcodeLayers = { group: THREE.Group; layerZ: number[]; layers: THREE.LineSegments[] };

type Bucket = { pos: number[]; col: number[] };

export function parseGcode(text: string): GcodeLayers {
  const perLayer: Bucket[] = [];
  const layerZ: number[] = [];
  let x = 0, y = 0, z = 0, e = 0, relE = true, type = "default";
  let cur: Bucket | null = null;
  let layerHeight = 0; // z recorded by layer markers; moves within a layer (z-hops) are drawn at this height
  let markers = false;
  const color = new THREE.Color();

  const newLayer = (zv: number) => {
    cur = { pos: [], col: [] };
    perLayer.push(cur);
    layerZ.push(zv);
    layerHeight = zv;
  };

  const seg = (x0: number, y0: number, x1: number, y1: number) => {
    if (SKIP_TYPES.has(type)) return;
    if (!cur) newLayer(z);
    const zz = markers ? layerHeight : z;
    color.setHex(TYPE_COLORS[type] ?? TYPE_COLORS.default);
    cur!.pos.push(x0, y0, zz, x1, y1, zz);
    cur!.col.push(color.r, color.g, color.b, color.r, color.g, color.b);
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
    x = nx; y = ny; e = relE ? 0 : ne;
  }

  const group = new THREE.Group();
  const layers: THREE.LineSegments[] = [];
  const keptZ: number[] = [];
  perLayer.forEach((l, idx) => {
    if (!l.pos.length) return;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(l.pos, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(l.col, 3));
    const line = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true }));
    group.add(line);
    layers.push(line);
    keptZ.push(layerZ[idx]);
  });
  return { group, layerZ: keptZ, layers };
}
