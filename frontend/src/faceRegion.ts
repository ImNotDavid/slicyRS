// Which triangles make up the flat face under the cursor: the connected triangles whose normals stay within ~2 degrees of
// the one that was hit. Works on a plain triangle soup (STL), so vertices are matched by position.

export type Topology = {
  triangles: number;
  normals: Float32Array;                // unit normal per triangle (zeros for degenerate ones)
  areas: Float32Array;
  neighbours: Map<number, number[]>;    // edge key -> triangles sharing that edge
  edgeKeys: Int32Array | Float64Array;  // 3 per triangle
};

const GRID = 1000; // positions are matched to 0.001 model units

export function buildTopology(pos: ArrayLike<number>): Topology {
  const triangles = Math.floor(pos.length / 9);
  const ids = new Map<string, number>();
  const vid = new Int32Array(triangles * 3);
  for (let v = 0; v < triangles * 3; v++) {
    const k = `${Math.round(pos[v * 3] * GRID)},${Math.round(pos[v * 3 + 1] * GRID)},${Math.round(pos[v * 3 + 2] * GRID)}`;
    let id = ids.get(k);
    if (id === undefined) { id = ids.size; ids.set(k, id); }
    vid[v] = id;
  }
  const V = ids.size + 1;
  const normals = new Float32Array(triangles * 3);
  const areas = new Float32Array(triangles);
  const neighbours = new Map<number, number[]>();
  const edgeKeys = new Float64Array(triangles * 3);
  for (let t = 0; t < triangles; t++) {
    const o = t * 9;
    const ux = pos[o + 3] - pos[o], uy = pos[o + 4] - pos[o + 1], uz = pos[o + 5] - pos[o + 2];
    const wx = pos[o + 6] - pos[o], wy = pos[o + 7] - pos[o + 1], wz = pos[o + 8] - pos[o + 2];
    const cx = uy * wz - uz * wy, cy = uz * wx - ux * wz, cz = ux * wy - uy * wx;
    const len = Math.hypot(cx, cy, cz);
    areas[t] = len / 2;
    if (len > 1e-12) { normals[t * 3] = cx / len; normals[t * 3 + 1] = cy / len; normals[t * 3 + 2] = cz / len; }
    for (let e = 0; e < 3; e++) {
      const a = vid[t * 3 + e], b = vid[t * 3 + ((e + 1) % 3)];
      const key = a < b ? a * V + b : b * V + a;
      edgeKeys[t * 3 + e] = key;
      const list = neighbours.get(key);
      if (list) list.push(t); else neighbours.set(key, [t]);
    }
  }
  return { triangles, normals, areas, neighbours, edgeKeys };
}

export function faceRegion(topo: Topology, seed: number, cosTolerance = 0.9994): number[] {
  const n = topo.normals;
  if (n[seed * 3] === 0 && n[seed * 3 + 1] === 0 && n[seed * 3 + 2] === 0) return [seed];
  const nx = n[seed * 3], ny = n[seed * 3 + 1], nz = n[seed * 3 + 2];
  const seen = new Set<number>([seed]);
  const queue = [seed];
  for (let i = 0; i < queue.length; i++) {
    const t = queue[i];
    for (let e = 0; e < 3; e++) {
      for (const other of topo.neighbours.get(topo.edgeKeys[t * 3 + e]) ?? []) {
        if (seen.has(other)) continue;
        if (n[other * 3] * nx + n[other * 3 + 1] * ny + n[other * 3 + 2] * nz < cosTolerance) continue;
        seen.add(other);
        queue.push(other);
      }
    }
  }
  return queue;
}

/** Area-weighted outward normal of a set of triangles. */
export function regionNormal(topo: Topology, tris: number[]): [number, number, number] {
  let x = 0, y = 0, z = 0;
  for (const t of tris) {
    const a = topo.areas[t];
    x += topo.normals[t * 3] * a; y += topo.normals[t * 3 + 1] * a; z += topo.normals[t * 3 + 2] * a;
  }
  const len = Math.hypot(x, y, z) || 1;
  return [x / len, y / len, z / len];
}
