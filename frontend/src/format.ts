/** Seconds as "1h 05m" or "42m"; a dash when unknown. */
export const fmtTime = (s?: number | null) => {
  if (s == null) return "—";
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
};
