/**
 * How the map colours a flight — the one place both scales live.
 *
 * The Tool menu's "Display by" switch picks which of the two paints the
 * aircraft symbol AND its trail together, so the map, the legend and the menu
 * all have to agree on the scale. They used to keep private copies "in
 * lock-step on purpose" (LeafletMap and AltitudeLegend); one module removes the
 * "on purpose".
 *
 * Pure and DOM-free so the legend can use it without pulling Leaflet — which
 * needs `window` — into the server render.
 */

/** What colours the aircraft symbol and its trail. */
export type ColorBy = "type" | "altitude";

export const DEFAULT_COLOR_BY: ColorBy = "altitude";

/** Top of the altitude scale (ft): everything at or above reads as the end
 *  colour, and the legend's top tick is labelled to match. */
export const ALT_SCALE_MAX_FT = 40000;

/** Altitude → colour: brighter (yellow) at low altitudes, saturated cyan/blue
 *  at cruise. The same scale is used for every flight so altitude reads
 *  consistently — different flights stay distinguishable by their physical path.
 *  A missing altitude is a neutral grey, not the bottom of the scale. */
export function altitudeColor(altFt: number | null | undefined): string {
  if (altFt == null || !Number.isFinite(altFt)) return "#94a3b8";
  // Normalise 0–FL400 onto 0–1; clamp so any altitude maps to a colour.
  const f = Math.max(0, Math.min(1, altFt / ALT_SCALE_MAX_FT));
  // Hue sweeps warm-yellow (50°) → cyan-blue (210°) as altitude climbs;
  // lightness drops 72 % → 38 % so low altitudes literally look brighter.
  const hue = 50 + f * 160;
  const light = 72 - f * 34;
  return `hsl(${hue.toFixed(0)}, 92%, ${light.toFixed(0)}%)`;
}

/** Aircraft-type → fill colour, so each type flies a distinct colour. Common
 *  Thai-fleet types get hand-picked hues; any other type falls back to a
 *  deterministic hash so it still gets a stable, distinct colour. */
const AIRCRAFT_COLORS: Record<string, string> = {
  // Boeing
  B737: "#22d3ee",
  B738: "#22d3ee",
  B739: "#0ea5e9",
  B763: "#34d399",
  B77W: "#fb7185",
  B772: "#f43f5e",
  B789: "#38bdf8",
  B788: "#60a5fa",
  // Airbus
  A319: "#fde047",
  A320: "#f472b6",
  A321: "#a3e635",
  A332: "#fb923c",
  A333: "#fbbf24",
  A359: "#c084fc",
  A35K: "#a855f7",
  A388: "#f87171",
  // Turboprops / regional
  AT72: "#2dd4bf",
  AT76: "#2dd4bf",
  DH8D: "#86efac",
};

export function aircraftColor(type: string | undefined): string {
  const t = (type ?? "").toUpperCase().trim();
  if (AIRCRAFT_COLORS[t]) return AIRCRAFT_COLORS[t];
  if (!t) return "#22d3ee";
  // Deterministic fallback: hash the type code → a stable hue so unknown
  // types are still visually separable (and consistent across frames).
  let h = 0;
  for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) % 360;
  return `hsl(${h}, 85%, 62%)`;
}

/** The distinct aircraft types in a set of flights, most numerous first (ties
 *  by code), each with the colour the map paints it. Blank types are dropped:
 *  they have no name to put beside a swatch. */
export function typeLegendEntries(
  types: ReadonlyArray<string | undefined>,
): { type: string; count: number; color: string }[] {
  const counts = new Map<string, number>();
  for (const raw of types) {
    const t = (raw ?? "").toUpperCase().trim();
    if (t) counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  return [...counts]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([type, count]) => ({ type, count, color: aircraftColor(type) }));
}

/* The palette above is CSS strings (hex / hsl); the GPU traffic layer
 * (components/GpuTraffic) wants RGBA bytes. */
export type Rgba = [number, number, number, number];
const colorCache = new Map<string, Rgba>();

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  s /= 100;
  l /= 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) =>
    l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [f(0) * 255, f(8) * 255, f(4) * 255];
}

/** CSS colour (#rgb, #rrggbb, #rrggbbaa, hsl()) → RGBA bytes, cached. */
export function cssToRgba(css: string, alpha = 1): Rgba {
  const key = `${css}|${alpha}`;
  const hit = colorCache.get(key);
  if (hit) return hit;
  let rgb: [number, number, number] = [148, 163, 184];
  let a = 1;
  const s = css.trim();
  if (s.startsWith("#")) {
    let hex = s.slice(1);
    if (hex.length === 3) hex = [...hex].map((c) => c + c).join("");
    const n = parseInt(hex.slice(0, 6), 16);
    rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    if (hex.length === 8) a = parseInt(hex.slice(6, 8), 16) / 255;
  } else {
    const m = s.match(/hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%/i);
    if (m) rgb = hslToRgb(Number(m[1]), Number(m[2]), Number(m[3]));
  }
  const out: Rgba = [
    Math.round(rgb[0]),
    Math.round(rgb[1]),
    Math.round(rgb[2]),
    Math.round(a * alpha * 255),
  ];
  colorCache.set(key, out);
  return out;
}
