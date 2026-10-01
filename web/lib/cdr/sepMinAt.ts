/**
 * Position-dependent horizontal separation minimum: 3 NM inside the Bangkok
 * TMA (terminal radar minimum), 5 NM everywhere else (en-route).
 *
 * One definition, used in two places that must agree exactly: MapApp feeds it
 * to the realtime detector and the advisory through `cdr.config`, and the
 * plan-scan worker rebuilds it from the same TMA polygon so a conflict it
 * reports is measured against the same minimum the map shows.
 */

import { airspaceAt, type AirspaceIndex } from "@/lib/airspace";

import { DEFAULT_CDR_CONFIG } from "./config";

export type SepMinNmAt = (lat: number, lon: number, altFt: number | null) => number;

/** The resolver, or undefined until the TMA polygon has loaded (callers then
 *  fall back to the flat en-route minimum). */
export function makeSepMinNmAt(index: AirspaceIndex): SepMinNmAt | undefined {
  if (!index.tma) return undefined;
  const { enrouteNm, terminalNm } = DEFAULT_CDR_CONFIG.horizontal;
  return (lat, lon, altFt) =>
    airspaceAt(index, lon, lat, altFt).tma === "BANGKOK TMA" ? terminalNm : enrouteNm;
}
