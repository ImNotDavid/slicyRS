// Outlines of parts seen from above, for the plate editor: how close parts are, and where a dragged part may go. Pure geometry, no 3D library.

export type Pt = [number, number];
export type Outline = { hull: Pt[]; width: number; depth: number };   // hull is centred on the part's bounding box

/** Turn about X, then Y, then Z (degrees), scale, and take the outline from above: the convex hull of the x/y of every vertex, centred on the
 *  bounding box (the same centring the server applies, so positions mean the same thing on both sides). */
export function outlineOf(pos: ArrayLike<number>, rotationDeg: [number, number, number], scale: number): Outline {
  const [ax, ay, az] = rotationDeg.map((d) => (d * Math.PI) / 180);
  const [sx, cx, sy, cy, sz, cz] = [Math.sin(ax), Math.cos(ax), Math.sin(ay), Math.cos(ay), Math.sin(az), Math.cos(az)];
  const n = Math.floor(pos.length / 3);
  const pts: Pt[] = new Array(n);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i++) {
    const x = pos[i * 3] * scale, y = pos[i * 3 + 1] * scale, z = pos[i * 3 + 2] * scale;
    const y1r = y * cx - z * sx, z1 = y * sx + z * cx;            // about X
    const x2 = x * cy + z1 * sy;                                   // about Y (z is not needed from here on)
    const px = x2 * cz - y1r * sz, py = x2 * sz + y1r * cz;       // about Z
    pts[i] = [px, py];
    x0 = Math.min(x0, px); x1 = Math.max(x1, px); y0 = Math.min(y0, py); y1 = Math.max(y1, py);
  }
  const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
  const hull = convexHull(pts).map(([px, py]) => [px - mx, py - my] as Pt);
  return { hull, width: x1 - x0, depth: y1 - y0 };
}

/** Andrew's monotone chain; counter-clockwise, no repeated first point. */
export function convexHull(points: Pt[]): Pt[] {
  const p = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: Pt[] = [];
  for (const q of p) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop(); lower.push(q); }
  const upper: Pt[] = [];
  for (let i = p.length - 1; i >= 0; i--) { const q = p[i]; while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop(); upper.push(q); }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

const shift = (h: Pt[], dx: number, dy: number): Pt[] => h.map(([x, y]) => [x + dx, y + dy]);

function inside(h: Pt[], q: Pt): boolean {                      // h is counter-clockwise
  for (let i = 0; i < h.length; i++) {
    const a = h[i], b = h[(i + 1) % h.length];
    if ((b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0]) < 0) return false;
  }
  return true;
}

function segDistance(q: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(q[0] - (a[0] + t * dx), q[1] - (a[1] + t * dy));
}

function segmentsCross(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  const o = (p: Pt, q: Pt, r: Pt) => Math.sign((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]));
  return o(a, b, c) !== o(a, b, d) && o(c, d, a) !== o(c, d, b);
}

/** Shortest distance between two convex outlines (0 if they overlap or touch). */
export function distance(a: Pt[], b: Pt[]): number {
  if (!a.length || !b.length) return Infinity;
  if (a.length >= 3 && b.length >= 3) {
    if (a.some((q) => inside(b, q)) || b.some((q) => inside(a, q))) return 0;
    for (let i = 0; i < a.length; i++) for (let j = 0; j < b.length; j++) if (segmentsCross(a[i], a[(i + 1) % a.length], b[j], b[(j + 1) % b.length])) return 0;
  }
  let best = Infinity;
  for (const [p, h] of [[a, b], [b, a]] as [Pt[], Pt[]][]) for (const q of p) for (let i = 0; i < h.length; i++) best = Math.min(best, segDistance(q, h[i], h[(i + 1) % h.length]));
  return best;
}

export const MIN_GAP_MM = 1;      // the editor keeps parts at least this far apart (the server insists on 0.5)
export const BED_MARGIN_MM = 3;   // the same margin the server keeps from the plate's edge

export type Placed = { uid: string; outline: Outline; position: Pt };

export const placedHull = (p: Placed): Pt[] => shift(p.outline.hull, p.position[0], p.position[1]);

/** Pairs of parts closer than the minimum gap. */
export function overlaps(parts: Placed[], gap = MIN_GAP_MM): [string, string][] {
  const out: [string, string][] = [];
  for (let i = 0; i < parts.length; i++) for (let j = i + 1; j < parts.length; j++) if (distance(placedHull(parts[i]), placedHull(parts[j])) < gap) out.push([parts[i].uid, parts[j].uid]);
  return out;
}

/** Where a dragged part ends up: the wanted spot if it is free, otherwise slid along one axis, otherwise where it was. Always inside the bed. */
export function settle(moving: Placed, wanted: Pt, others: Placed[], bed: [number, number], gap = MIN_GAP_MM): Pt {
  const limit = [Math.max(0, bed[0] / 2 - BED_MARGIN_MM - moving.outline.width / 2), Math.max(0, bed[1] / 2 - BED_MARGIN_MM - moving.outline.depth / 2)];
  const clamp = (p: Pt): Pt => [Math.max(-limit[0], Math.min(limit[0], p[0])), Math.max(-limit[1], Math.min(limit[1], p[1]))];
  const free = (p: Pt) => others.every((o) => distance(shift(moving.outline.hull, p[0], p[1]), placedHull(o)) >= gap);
  const want = clamp(wanted);
  if (free(want)) return want;
  const slideX: Pt = [want[0], moving.position[1]], slideY: Pt = [moving.position[0], want[1]];
  if (free(slideX) && Math.abs(want[0] - moving.position[0]) >= Math.abs(want[1] - moving.position[1])) return slideX;
  if (free(slideY)) return slideY;
  if (free(slideX)) return slideX;
  return moving.position;
}
