/**
 * Web Worker entry for the strategic flight-plan conflict scan.
 *
 * The full scan is O(pairs) — ~2 million pairs for a traffic day, several
 * seconds of arithmetic — and it used to run inside a `useMemo` on the main
 * thread, freezing the tab for that long the moment a big import finished
 * (and again each time the airspace polygons arrived and changed the config).
 * Here it runs off-thread; `usePlanScan` posts the packed flights and takes
 * the result when it lands. Same scan function, same inputs, same answer.
 */

import { buildAirspaceIndex } from "@/lib/airspace";

import { scanFlightPlanConflicts } from "./planScan";
import {
  unpackPlanFlights,
  type PlanScanRequest,
  type PlanScanResponse,
} from "./planScanPacked";
import { makeSepMinNmAt } from "./sepMinAt";

// tsconfig's lib is "dom", so `self` is typed as a Window; this is the slice
// of the worker scope the entry actually uses.
const ctx = self as unknown as {
  postMessage(msg: PlanScanResponse): void;
  onmessage: ((e: MessageEvent<PlanScanRequest>) => void) | null;
};

ctx.onmessage = (e) => {
  const { id, packed, cfg, tma } = e.data;
  const t0 = performance.now();
  const flights = unpackPlanFlights(packed);
  // The TMA polygon alone decides the terminal/en-route minimum (see
  // `makeSepMinNmAt`); the other layers are not consulted by the scan.
  const sepMinNmAt = tma ? makeSepMinNmAt(buildAirspaceIndex({ tma })) : undefined;
  const conflicts = scanFlightPlanConflicts(flights, { ...cfg, sepMinNmAt });
  ctx.postMessage({ id, conflicts, ms: performance.now() - t0 });
};
