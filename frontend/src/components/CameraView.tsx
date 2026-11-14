import { useEffect, useState } from "react";

/** A printer's camera as a picture that refreshes every few seconds while the page is visible (2.7). */
export default function CameraView({ printerId, every = 3000 }: { printerId: string; every?: number }) {
  const [tick, setTick] = useState(() => Date.now());
  const [err, setErr] = useState("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === "visible") setTick(Date.now()); }, every);
    return () => clearInterval(t);
  }, [every]);
  return (
    <div className="camera-view">
      {!err && <img src={`/api/printers/${printerId}/camera.jpg?t=${tick}`} alt="Live picture from the printer's camera"
        onLoad={() => setLoaded(true)}
        onError={() => fetch(`/api/printers/${printerId}/camera.jpg`).then((r) => r.json()).then((j) => setErr(j.detail ?? "No picture")).catch(() => setErr("No picture"))} />}
      {!loaded && !err && <span className="muted">Connecting to the camera…</span>}
      {err && <span className="muted">{err}</span>}
      {!err && <span className="cam-badge">Live · every {Math.round(every / 1000)} s</span>}
    </div>
  );
}
