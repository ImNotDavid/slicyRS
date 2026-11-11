// Which faces of the part, as it is turned on the bed, need support: a face needs it when it looks downward within `threshold`
// degrees of straight down (the slicer's "threshold angle": the flattest slope it prints without support), and is not the base
// resting on the bed. An estimate from the part's geometry; the slicer's own supports are shown after slicing.

export type OverhangResult = { triangles: number[]; percent: number };

/** The z row of the rotation the app applies (turn about X, then Y, then Z; degrees). A turn about Z does not change heights. */
function zRow(rotationDeg: [number, number, number]): [number, number, number] {
  const [ax, ay] = [rotationDeg[0], rotationDeg[1]].map((d) => (d * Math.PI) / 180);
  return [-Math.sin(ay), Math.cos(ay) * Math.sin(ax), Math.cos(ay) * Math.cos(ax)];
}

export function overhangTriangles(pos: ArrayLike<number>, rotationDeg: [number, number, number], scale: number, thresholdDeg: number): OverhangResult {
  const n = Math.floor(pos.length / 9);
  const [r0, r1, r2] = zRow(rotationDeg);
  let minZ = Infinity;
  for (let v = 0; v < n * 3; v++) minZ = Math.min(minZ, r0 * pos[v * 3] + r1 * pos[v * 3 + 1] + r2 * pos[v * 3 + 2]);
  const contactTol = 0.15 / Math.max(scale, 1e-9);            // the base is what lies within 0.15 mm (scaled) of the lowest point
  const limit = -Math.cos((Math.max(thresholdDeg, 0) * Math.PI) / 180);
  const out: number[] = [];
  let area = 0, over = 0;
  for (let t = 0; t < n; t++) {
    const o = t * 9;
    const ux = pos[o + 3] - pos[o], uy = pos[o + 4] - pos[o + 1], uz = pos[o + 5] - pos[o + 2];
    const wx = pos[o + 6] - pos[o], wy = pos[o + 7] - pos[o + 1], wz = pos[o + 8] - pos[o + 2];
    const cx = uy * wz - uz * wy, cy = uz * wx - ux * wz, cz = ux * wy - uy * wx;
    const len = Math.hypot(cx, cy, cz);
    if (len < 1e-12) continue;
    area += len / 2;
    const nz = (r0 * cx + r1 * cy + r2 * cz) / len;
    if (nz >= limit) continue;
    const z = [0, 1, 2].map((k) => r0 * pos[o + k * 3] + r1 * pos[o + k * 3 + 1] + r2 * pos[o + k * 3 + 2]);
    if (nz < -0.99 && Math.max(...z) - minZ < contactTol) continue;     // resting on the bed
    out.push(t);
    over += len / 2;
  }
  return { triangles: out, percent: area ? (100 * over) / area : 0 };
}
