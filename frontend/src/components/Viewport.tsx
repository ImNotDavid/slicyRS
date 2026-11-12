import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import type { Transform } from "../api";
import { IDENTITY } from "../plate";
import { outlineOf, overlaps, settle, type Outline, type Placed } from "../footprint";
import { parseGcode, type FeatureInfo, type GcodeLayers } from "../gcode";
import { buildTopology, faceRegion, regionNormal, type Topology } from "../faceRegion";
import { overhangTriangles } from "../overhang";

export type ViewPart = { uid: string; url: string; transform: Transform };

type Props = {
  parts: ViewPart[];                                         // every part on the plate
  selectedUid: string | null;                                // the one the tools act on
  onSelect?: (uid: string) => void;                          // a part was clicked
  onOverlap?: (pairs: [string, string][]) => void;           // parts closer than the minimum gap
  bed: [number, number, number];
  gcode: string | null; // when set and mode==="preview", toolpaths are shown
  mode: "prepare" | "preview";
  layerRange: [number, number];
  onLayerCount: (n: number) => void;
  fitSignal: number;
  pickFace?: boolean;                                        // hover highlights the flat face under the cursor; a click picks it
  onPickFace?: (normal: [number, number, number]) => void;  // outward normal of the picked face, in the model's own frame
  overhangAngle?: number | null;                             // highlight faces that need support at this threshold angle; null = off
  onOverhang?: (percent: number | null) => void;             // share of the surface highlighted
  hiddenFeatures?: string[];                                 // G-code feature types switched off in the legend
  showTravel?: boolean;                                      // draw non-printing moves
  progress?: number | null;                                  // 0..1 playback position through the visible layers; null shows everything
  onFeatures?: (features: FeatureInfo[]) => void;            // what the loaded G-code contains
  moveMode?: boolean;                                        // dragging the part moves it on the bed
  onMove?: (uid: string, position: [number, number]) => void;   // new offset (mm) from the middle of the bed for a dragged part
};

function ViewportGL(p: Props) {
  const host = useRef<HTMLDivElement>(null);
  const bedRef = useRef(p.bed);
  bedRef.current = p.bed;
  const onPickRef = useRef(p.onPickFace);
  onPickRef.current = p.onPickFace;
  const [loaded, setLoaded] = useState(0);        // counts model loads, so effects that need the geometry run after it arrives
  const onOverhangRef = useRef(p.onOverhang);
  onOverhangRef.current = p.onOverhang;
  const filterRef = useRef({ hidden: p.hiddenFeatures ?? [], travel: !!p.showTravel });
  filterRef.current = { hidden: p.hiddenFeatures ?? [], travel: !!p.showTravel };
  const onMoveRef = useRef(p.onMove);
  onMoveRef.current = p.onMove;
  const partsRef = useRef(p.parts);
  partsRef.current = p.parts;
  const onSelectRef = useRef(p.onSelect);
  onSelectRef.current = p.onSelect;
  const onOverlapRef = useRef(p.onOverlap);
  onOverlapRef.current = p.onOverlap;
  const selRef = useRef(p.selectedUid);
  selRef.current = p.selectedUid;
  const selected = p.parts.find((x) => x.uid === p.selectedUid);
  const selTransform = selected?.transform ?? IDENTITY;
  const outlines = useRef(new Map<string, Outline>());       // outline from above per part, by turn and scale (cheap to reuse while dragging)
  const outlineFor = (part: ViewPart): Outline => {
    const key = `${part.uid}|${part.transform.rotation.join(",")}|${part.transform.scale}`;
    let o = outlines.current.get(key);
    if (!o) {
      const m = three.current?.models.get(part.uid);
      o = outlineOf(m!.geometry.getAttribute("position").array, part.transform.rotation, part.transform.scale);
      outlines.current.set(key, o);
    }
    return o;
  };
  const userMoved = useRef(false); // once the user orbits, window resizes stop re-framing
  const three = useRef<{
    scene: THREE.Scene; camera: THREE.PerspectiveCamera; controls: OrbitControls; renderer: THREE.WebGLRenderer;
    bedGroup: THREE.Group; model?: THREE.Mesh; models: Map<string, THREE.Mesh>; gcode?: GcodeLayers;
  }>();

  // one-time scene setup
  useEffect(() => {
    const el = host.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, 1, 1, 5000);
    camera.up.set(0, 0, 1);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.maxPolarAngle = Math.PI * 0.495; // stay above the plate
    scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(200, -200, 400);
    scene.add(sun);
    const bedGroup = new THREE.Group();
    scene.add(bedGroup);
    three.current = { scene, camera, controls, renderer, bedGroup, models: new Map() };
    controls.addEventListener("start", () => { userMoved.current = true; });

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = el;
      renderer.setSize(w, h);
      camera.aspect = w / Math.max(h, 1);
      camera.updateProjectionMatrix();
      if (!userMoved.current) frame(three.current, bedRef.current);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();
    let raf = 0;
    const loop = () => { raf = requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); };
    loop();
    return () => {
      cancelAnimationFrame(raf); ro.disconnect(); controls.dispose(); renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, []);

  // bed + build volume
  useEffect(() => {
    const t = three.current!;
    t.bedGroup.clear();
    const [bx, by, bz] = p.bed;
    const plate = new THREE.Mesh(new THREE.PlaneGeometry(bx, by),
      new THREE.MeshBasicMaterial({ color: getComputedStyle(document.documentElement).getPropertyValue("--bed").trim() || "#15171a" }));
    plate.position.set(bx / 2, by / 2, -0.05);
    t.bedGroup.add(plate);
    const grid = new THREE.GridHelper(Math.max(bx, by), Math.max(bx, by) / 10, 0x3a3f47, 0x2a2e34);
    grid.rotation.x = Math.PI / 2;
    grid.position.set(bx / 2, by / 2, 0);
    t.bedGroup.add(grid);
    const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(bx, by, bz)),
      new THREE.LineBasicMaterial({ color: 0x5b6572, transparent: true, opacity: 0.3 }));
    box.position.set(bx / 2, by / 2, bz / 2);
    t.bedGroup.add(box);
    userMoved.current = false;
    frame(t, p.bed);
  }, [p.bed]);

  // parts come and go: load what is new, drop what is gone (copies of one model share its geometry)
  const urlKey = p.parts.map((x) => x.uid + "=" + x.url).join(",");
  useEffect(() => {
    const t = three.current!;
    const want = new Map(p.parts.map((x) => [x.uid, x]));
    for (const [uid, mesh] of t.models) {
      if (want.has(uid)) continue;
      t.scene.remove(mesh);
      t.models.delete(uid);
      outlines.current.forEach((_v, k) => k.startsWith(uid + "|") && outlines.current.delete(k));
      if (![...t.models.values()].some((m) => m.geometry === mesh.geometry)) mesh.geometry.dispose();
    }
    let cancelled = false;
    for (const part of p.parts) {
      if (t.models.has(part.uid)) continue;
      const shared = [...t.models.values()].find((m) => m.userData.url === part.url);
      const attach = (geo: THREE.BufferGeometry) => {
        if (cancelled || t.models.has(part.uid) || !partsRef.current.some((x) => x.uid === part.uid)) return;
        const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x8aa6d8, roughness: 0.6, metalness: 0.05 }));
        mesh.matrixAutoUpdate = false;
        mesh.userData = { uid: part.uid, url: part.url };
        t.models.set(part.uid, mesh);
        t.scene.add(mesh);
        place(mesh, part.transform, p.bed);
        recolour(t, selRef.current);
        setLoaded((n) => n + 1);
      };
      if (shared) attach(shared.geometry);
      else new STLLoader().load(part.url, (geo) => { geo.computeVertexNormals(); geo.computeBoundingBox(); attach(geo); });
    }
    return () => { cancelled = true; };
  }, [urlKey]);

  // every part sits where its transform says
  useEffect(() => {
    const t = three.current!;
    for (const part of p.parts) {
      const m = t.models.get(part.uid);
      if (m) place(m, part.transform, p.bed);
    }
  }, [p.parts, p.bed, loaded]);

  // the selected part is the one the tools act on; the others are paler
  useEffect(() => {
    const t = three.current!;
    recolour(t, p.selectedUid);
    setLoaded((n) => n + 1);       // effects below that read the selected mesh run again
  }, [p.selectedUid, urlKey]);

  // parts that are too close to each other
  useEffect(() => {
    const t = three.current!;
    const placed: Placed[] = [];
    for (const part of p.parts) {
      const m = t.models.get(part.uid);
      if (m) placed.push({ uid: part.uid, outline: outlineFor(part), position: part.transform.position });
    }
    onOverlapRef.current?.(overlaps(placed));
  }, [p.parts, loaded]);

  // faces that will need support at the current orientation (an estimate, drawn on the model)
  useEffect(() => {
    const m = three.current?.model;
    if (p.overhangAngle == null || !m) { onOverhangRef.current?.(null); return; }
    const r = overhangTriangles(m.geometry.getAttribute("position").array, selTransform.rotation, selTransform.scale, p.overhangAngle);
    const src = m.geometry.getAttribute("position").array as Float32Array;
    const out = new Float32Array(r.triangles.length * 9);
    r.triangles.forEach((tri, i) => out.set(src.subarray(tri * 9, tri * 9 + 9), i * 9));
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(out, 3));
    const overlay = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: 0xe53935, transparent: true, opacity: 0.85, side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }));
    m.add(overlay);
    onOverhangRef.current?.(r.percent);
    return () => { m.remove(overlay); g.dispose(); };
  }, [p.overhangAngle, selTransform.rotation[0], selTransform.rotation[1], selTransform.rotation[2], selTransform.scale, p.selectedUid, loaded]);

  // click a part to select it; in Move mode, drag any part (it stops at its neighbours and at the plate's edge)
  useEffect(() => {
    const t = three.current!;
    const el = t.renderer.domElement;
    if (p.pickFace) return;                              // picking a face owns the clicks
    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
    const aim = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, t.camera);
    };
    const point = (e: PointerEvent) => { aim(e); return ray.ray.intersectPlane(plane, new THREE.Vector3()) ?? undefined; };
    const hit = (e: PointerEvent): string | null => {
      aim(e);
      const visible = [...t.models.values()].filter((m) => m.visible);
      const h = ray.intersectObjects(visible, false)[0];
      return h ? (h.object.userData.uid as string) : null;
    };
    const partOf = (uid: string) => partsRef.current.find((x) => x.uid === uid);
    let drag: { uid: string; start: THREE.Vector3; from: [number, number] } | null = null;
    let downAt: [number, number] | null = null;
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return;
      downAt = [e.clientX, e.clientY];
      if (!p.moveMode) return;
      const uid = hit(e);
      const part = uid ? partOf(uid) : undefined;
      const start = part ? point(e) : undefined;
      if (!part || !start) return;
      onSelectRef.current?.(part.uid);
      drag = { uid: part.uid, start, from: [...part.transform.position] as [number, number] };
      t.controls.enabled = false;                        // dragging a part must not orbit the camera
      el.setPointerCapture(e.pointerId);
      el.style.cursor = "grabbing";
    };
    const move = (e: PointerEvent) => {
      if (!drag) { if (p.moveMode) el.style.cursor = hit(e) ? "grab" : ""; return; }
      const now = point(e);
      const part = partOf(drag.uid);
      if (!now || !part) return;
      const others: Placed[] = partsRef.current.filter((x) => x.uid !== drag!.uid && t.models.has(x.uid))
        .map((x) => ({ uid: x.uid, outline: outlineFor(x), position: x.transform.position }));
      const moving: Placed = { uid: part.uid, outline: outlineFor(part), position: part.transform.position };
      const wanted: [number, number] = [drag.from[0] + now.x - drag.start.x, drag.from[1] + now.y - drag.start.y];
      const pos = settle(moving, wanted, others, [bedRef.current[0], bedRef.current[1]]);
      onMoveRef.current?.(part.uid, [Math.round(pos[0] * 10) / 10, Math.round(pos[1] * 10) / 10]);
    };
    const up = (e: PointerEvent) => {
      if (drag) {
        drag = null;
        t.controls.enabled = true;
        el.releasePointerCapture(e.pointerId);
        el.style.cursor = hit(e) ? "grab" : "";
      } else if (downAt && Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) < 5 && !p.moveMode) {
        const uid = hit(e);                              // a click that was not a drag selects the part under the cursor
        if (uid) onSelectRef.current?.(uid);
      }
      downAt = null;
    };
    el.addEventListener("pointerdown", down, true);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    return () => {
      el.removeEventListener("pointerdown", down, true); el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up);
      t.controls.enabled = true;
      el.style.cursor = "";
    };
  }, [p.moveMode, p.pickFace, urlKey]);

  // pick a face to lay on the bed
  useEffect(() => {
    const t = three.current!;
    const el = t.renderer.domElement;
    if (!p.pickFace) return;
    el.style.cursor = "crosshair";
    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    let overlay: THREE.Mesh | undefined;
    let current: { seed: number; normal: [number, number, number] } | null = null;
    let down: [number, number] | null = null;
    let last = 0;
    const clear = () => { if (overlay) { overlay.parent?.remove(overlay); overlay.geometry.dispose(); overlay = undefined; } current = null; };
    const hit = (e: PointerEvent) => {
      const m = t.model;
      if (!m || !m.visible) return undefined;
      const r = el.getBoundingClientRect();
      ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, t.camera);
      return ray.intersectObject(m, false)[0];
    };
    const move = (e: PointerEvent) => {
      if (e.buttons || performance.now() - last < 30) return;
      last = performance.now();
      const h = hit(e);
      const m = t.model;
      if (!h || h.faceIndex == null || !m) { clear(); return; }
      if (current && current.seed === h.faceIndex) return;
      const topo: Topology = (m.userData.topo ??= buildTopology(m.geometry.getAttribute("position").array));
      const tris = faceRegion(topo, h.faceIndex);
      clear();
      const src = m.geometry.getAttribute("position").array;
      const out = new Float32Array(tris.length * 9);
      tris.forEach((tri, i) => out.set((src as Float32Array).subarray(tri * 9, tri * 9 + 9), i * 9));
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(out, 3));
      overlay = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: 0xffa726, transparent: true, opacity: 0.8, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }));
      m.add(overlay);
      current = { seed: h.faceIndex, normal: regionNormal(topo, tris) };
    };
    const downEv = (e: PointerEvent) => { down = [e.clientX, e.clientY]; };
    const up = (e: PointerEvent) => {
      const d = down; down = null;
      if (!d || Math.hypot(e.clientX - d[0], e.clientY - d[1]) > 5) return;   // a drag is an orbit, not a pick
      const h = hit(e);
      if (h && current) onPickRef.current?.(current.normal);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerdown", downEv);
    el.addEventListener("pointerup", up);
    return () => {
      el.removeEventListener("pointermove", move); el.removeEventListener("pointerdown", downEv); el.removeEventListener("pointerup", up);
      el.style.cursor = "";
      clear();
    };
  }, [p.pickFace, p.selectedUid]);

  // G-code load
  useEffect(() => {
    const t = three.current!;
    if (t.gcode) { t.scene.remove(t.gcode.group); t.gcode = undefined; }
    if (!p.gcode) { p.onLayerCount(0); return; }
    const parsed = parseGcode(p.gcode);
    parsed.apply(new Set(filterRef.current.hidden), filterRef.current.travel);
    t.gcode = parsed;
    p.onFeatures?.(parsed.features);
    t.scene.add(parsed.group);
    p.onLayerCount(parsed.layers.length);
  }, [p.gcode]);

  // which features are drawn: rebuilt only when the legend changes
  useEffect(() => {
    three.current?.gcode?.apply(new Set(p.hiddenFeatures ?? []), !!p.showTravel);
  }, [p.hiddenFeatures, p.showTravel, p.gcode]);

  // mode, layer range and playback visibility (runs after the features above, so draw ranges see the new segment counts)
  useEffect(() => {
    const t = three.current!;
    const preview = p.mode === "preview" && !!t.gcode;
    t.models.forEach((m) => { m.visible = !preview; });
    const g = t.gcode;
    if (!g) return;
    g.group.visible = preview;
    const [lo, hi] = p.layerRange;
    const reach = p.progress == null ? Infinity : p.progress * (hi - lo + 1);       // layers (and part of one) reached by the playback
    g.layers.forEach((l, i) => {
      const n = g.visibleSegments(i);
      const inRange = i >= lo && i <= hi && n > 0;
      const into = i - lo;
      if (!inRange || into >= reach) { l.visible = false; return; }
      l.visible = true;
      l.geometry.setDrawRange(0, into + 1 <= reach ? Infinity : Math.max(2, Math.ceil((reach - into) * n)) * 2);
    });
  }, [p.mode, p.layerRange, p.gcode, p.hiddenFeatures, p.showTravel, p.progress]);

  // reset camera
  useEffect(() => {
    const t = three.current!;
    userMoved.current = false;
    frame(t, p.bed);
  }, [p.fitSignal]);

  return <div ref={host} className="viewport-canvas" />;
}

// The selected part is the one the tools act on (and the only dark blue one when there are several).
function recolour(t: { models: Map<string, THREE.Mesh>; model?: THREE.Mesh }, selected: string | null) {
  t.model = selected ? t.models.get(selected) : undefined;
  for (const [uid, m] of t.models) (m.material as THREE.MeshStandardMaterial).color.setHex(uid === selected || t.models.size === 1 ? 0x2f6bff : 0x8aa6d8);
}

// Mirrors backend apply_transform: scale, then rotate about X, Y, Z, drop to z=0, centre on the bed, then shift by the position the user chose.
function place(mesh: THREE.Mesh, t: Transform, bed: [number, number, number]) {
  const rs = new THREE.Matrix4()
    .makeRotationFromEuler(new THREE.Euler(
      THREE.MathUtils.degToRad(t.rotation[0]), THREE.MathUtils.degToRad(t.rotation[1]),
      THREE.MathUtils.degToRad(t.rotation[2]), "ZYX"))
    .multiply(new THREE.Matrix4().makeScale(t.scale, t.scale, t.scale));
  const box = mesh.geometry.boundingBox!.clone().applyMatrix4(rs);
  const c = box.getCenter(new THREE.Vector3());
  const tr = new THREE.Matrix4().makeTranslation(bed[0] / 2 + t.position[0] - c.x, bed[1] / 2 + t.position[1] - c.y, -box.min.z);
  mesh.matrix.copy(tr.multiply(rs));
  mesh.matrixWorldNeedsUpdate = true;
}

// Place the camera so the whole plate is visible above the bottom bar, whatever the pane's aspect ratio.
function frame(t: { camera: THREE.PerspectiveCamera; controls: OrbitControls; renderer: THREE.WebGLRenderer } | undefined, bed: [number, number, number]) {
  if (!t) return;
  const [bx, by] = bed;
  const el = t.renderer.domElement;
  const aspect = Math.max(el.clientWidth, 1) / Math.max(el.clientHeight, 1);
  const half = Math.tan(THREE.MathUtils.degToRad(t.camera.fov) / 2);
  const dist = Math.max((Math.max(bx, by) * 0.62) / (half * Math.min(aspect, 1.6)), (by * 0.8) / half) * 1.1;
  const target = new THREE.Vector3(bx / 2, by / 2, 0);
  const dir = new THREE.Vector3(0, -0.72, 0.69).normalize();
  t.controls.target.copy(target);
  t.camera.position.copy(target).addScaledVector(dir, dist);
  t.camera.near = dist / 50;
  t.camera.far = dist * 10;
  t.camera.updateProjectionMatrix();
  t.controls.update();
}

const hasWebGL = (() => { try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); } catch { return false; } })();

/** The 3D view needs WebGL; without it the rest of the workspace still loads and says why the view is empty. */
export default function Viewport(props: React.ComponentProps<typeof ViewportGL>) {
  if (!hasWebGL) return <div className="viewport-canvas no-gl" role="alert"><p>This browser cannot show 3D graphics (WebGL is off or unavailable). Try another browser or turn on hardware acceleration.</p></div>;
  return <ViewportGL {...props} />;
}
