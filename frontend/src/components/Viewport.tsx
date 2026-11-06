import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import type { Transform } from "../api";
import { parseGcode, type GcodeLayers } from "../gcode";

type Props = {
  modelUrl: string | null;
  bed: [number, number, number];
  transform: Transform;
  gcode: string | null; // when set and mode==="preview", toolpaths are shown
  mode: "prepare" | "preview";
  layerRange: [number, number];
  onLayerCount: (n: number) => void;
  fitSignal: number;
};

export default function Viewport(p: Props) {
  const host = useRef<HTMLDivElement>(null);
  const bedRef = useRef(p.bed);
  bedRef.current = p.bed;
  const userMoved = useRef(false); // once the user orbits, window resizes stop re-framing
  const three = useRef<{
    scene: THREE.Scene; camera: THREE.PerspectiveCamera; controls: OrbitControls; renderer: THREE.WebGLRenderer;
    bedGroup: THREE.Group; model?: THREE.Mesh; gcode?: GcodeLayers;
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
    three.current = { scene, camera, controls, renderer, bedGroup };
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

  // model load
  useEffect(() => {
    const t = three.current!;
    if (t.model) { t.scene.remove(t.model); t.model.geometry.dispose(); t.model = undefined; }
    if (!p.modelUrl) return;
    let cancelled = false;
    new STLLoader().load(p.modelUrl, (geo) => {
      if (cancelled) return;
      geo.computeVertexNormals();
      geo.computeBoundingBox();
      const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x2f6bff, roughness: 0.6, metalness: 0.05 }));
      mesh.matrixAutoUpdate = false;
      t.model = mesh;
      t.scene.add(mesh);
      place(mesh, p.transform, p.bed);
    });
    return () => { cancelled = true; };
  }, [p.modelUrl]);

  // transform / bed change
  useEffect(() => {
    const m = three.current?.model;
    if (m) place(m, p.transform, p.bed);
  }, [p.transform, p.bed]);

  // G-code load
  useEffect(() => {
    const t = three.current!;
    if (t.gcode) { t.scene.remove(t.gcode.group); t.gcode = undefined; }
    if (!p.gcode) { p.onLayerCount(0); return; }
    const parsed = parseGcode(p.gcode);
    t.gcode = parsed;
    t.scene.add(parsed.group);
    p.onLayerCount(parsed.layers.length);
  }, [p.gcode]);

  // mode + layer range visibility
  useEffect(() => {
    const t = three.current!;
    const preview = p.mode === "preview" && !!t.gcode;
    if (t.model) t.model.visible = !preview;
    if (t.gcode) {
      t.gcode.group.visible = preview;
      t.gcode.layers.forEach((l, i) => { l.visible = i >= p.layerRange[0] && i <= p.layerRange[1]; });
    }
  }, [p.mode, p.layerRange, p.gcode]);

  // reset camera
  useEffect(() => {
    const t = three.current!;
    userMoved.current = false;
    frame(t, p.bed);
  }, [p.fitSignal]);

  return <div ref={host} className="viewport-canvas" />;
}

// Mirrors backend apply_transform: scale, then rotate about X, Y, Z, drop to z=0, centre on the bed.
function place(mesh: THREE.Mesh, t: Transform, bed: [number, number, number]) {
  const rs = new THREE.Matrix4()
    .makeRotationFromEuler(new THREE.Euler(
      THREE.MathUtils.degToRad(t.rotation[0]), THREE.MathUtils.degToRad(t.rotation[1]),
      THREE.MathUtils.degToRad(t.rotation[2]), "ZYX"))
    .multiply(new THREE.Matrix4().makeScale(t.scale, t.scale, t.scale));
  const box = mesh.geometry.boundingBox!.clone().applyMatrix4(rs);
  const c = box.getCenter(new THREE.Vector3());
  const tr = new THREE.Matrix4().makeTranslation(bed[0] / 2 - c.x, bed[1] / 2 - c.y, -box.min.z);
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
