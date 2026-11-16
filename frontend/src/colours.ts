/** Named filament colours (the Filament page's list) and the nearest name for a colour a printer reports. */
export const COLOURS: [string, string][] = [["Black", "#111111"], ["White", "#f4f4f4"], ["Grey", "#8a8a8a"], ["Silver", "#c0c0c0"], ["Red", "#d0242b"], ["Orange", "#f07a1a"],
  ["Yellow", "#f5d000"], ["Green", "#2f9e44"], ["Blue", "#1f5fd6"], ["Light blue", "#58a6ff"], ["Purple", "#7b3fbf"], ["Pink", "#f06aa6"], ["Brown", "#7a4b2a"],
  ["Beige", "#d8c3a0"], ["Gold", "#c9a227"], ["Transparent", "#e8eef2"]];

const rgb = (hex: string) => { const n = parseInt(hex.replace("#", "").slice(0, 6).padEnd(6, "0"), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };

export function colourName(hex: string): string {
  const [r, g, b] = rgb(hex);
  let best = COLOURS[0][0], d = Infinity;
  for (const [name, h] of COLOURS) {
    const [r2, g2, b2] = rgb(h);
    const dd = 2 * (r - r2) ** 2 + 4 * (g - g2) ** 2 + 3 * (b - b2) ** 2;
    if (dd < d) { d = dd; best = name; }
  }
  return best;
}
