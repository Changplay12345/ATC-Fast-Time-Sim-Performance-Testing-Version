/**
 * The worker receives the plan flights as flat typed arrays. Whatever it
 * finds must be exactly what the main thread would have found from the
 * original objects — same pairs, same CPA numbers, same order — or the
 * Dashboard would show different conflicts depending on which thread ran.
 */
import { describe, expect, it } from "vitest";

import { resolveConfig } from "./config";
import { scanFlightPlanConflicts, type PlanFlight } from "./planScan";
import { cloneableConfig, packPlanFlights, unpackPlanFlights } from "./planScanPacked";

const cfg = resolveConfig();

/** A straight leg: `n` samples at 5 s, 450 kt, on a constant track. */
function leg(
  id: string,
  lat: number,
  lon: number,
  trackDeg: number,
  altFt: number | null,
  offsetSec = 0,
  n = 240,
): PlanFlight {
  const step = 5;
  const nmPerDegLon = Math.cos((lat * Math.PI) / 180) * 60;
  const east = Math.sin((trackDeg * Math.PI) / 180);
  const north = Math.cos((trackDeg * Math.PI) / 180);
  const samples = [];
  for (let i = 0; i < n; i++) {
    const nm = (450 * i * step) / 3600;
    samples.push({
      t: i * step,
      lat: lat + (nm * north) / 60,
      lon: lon + (nm * east) / nmPerDegLon,
      altitudeFt: altFt,
      gsKt: 450,
      tasKt: 450,
      track: trackDeg,
      phase: "cruise" as const,
    });
  }
  return { id, callsign: id, samples, offsetSec, durationSec: (n - 1) * step };
}

function fleet(): PlanFlight[] {
  const lat = 13;
  const nmPerDegLon = Math.cos((lat * Math.PI) / 180) * 60;
  const all: PlanFlight[] = [
    // Two head-on pairs, one of them offset in time so it only just overlaps.
    leg("A", lat, 100, 90, 35000),
    leg("B", lat, 100 + 40 / nmPerDegLon, 270, 35000),
    leg("C", lat + 1, 100, 90, 37000, 300),
    leg("D", lat + 1, 100 + 40 / nmPerDegLon, 270, 37000, 420),
    // Same track, 1 000 ft apart — a sub-buffer pass, not a loss.
    leg("E", lat + 2, 100, 90, 35000),
    leg("F", lat + 2, 100 + 40 / nmPerDegLon, 270, 36000),
    // No altitude at all on one of them.
    leg("G", lat + 3, 100, 90, null),
    leg("H", lat + 3, 100 + 40 / nmPerDegLon, 270, 35000),
  ];
  for (let i = 0; i < 60; i++) {
    all.push(leg(`N${i}`, 5 + (i % 12), 95 + Math.floor(i / 12) * 3, (i * 37) % 360, 30000 + (i % 9) * 1000, i * 50));
  }
  return all;
}

describe("packed plan flights", () => {
  it("survive the round trip exactly", () => {
    const flights = fleet();
    const back = unpackPlanFlights(packPlanFlights(flights));
    expect(back.length).toBe(flights.length);
    back.forEach((f, i) => {
      const o = flights[i];
      expect(f.id).toBe(o.id);
      expect(f.callsign).toBe(o.callsign);
      expect(f.offsetSec).toBe(o.offsetSec);
      expect(f.durationSec).toBe(o.durationSec);
      expect(f.samples.length).toBe(o.samples.length);
      f.samples.forEach((s, k) => {
        expect(s.t).toBe(o.samples[k].t);
        expect(s.lat).toBe(o.samples[k].lat);
        expect(s.lon).toBe(o.samples[k].lon);
        expect(s.altitudeFt).toBe(o.samples[k].altitudeFt);
      });
    });
  });

  it("scan to the same conflicts as the originals", () => {
    const flights = fleet();
    const direct = scanFlightPlanConflicts(flights, cfg);
    expect(direct.length).toBeGreaterThan(1);
    const viaPacked = scanFlightPlanConflicts(unpackPlanFlights(packPlanFlights(flights)), cfg);
    expect(viaPacked).toEqual(direct);
  });

  it("re-use a flight's packed samples across calls", () => {
    const flights = fleet();
    const a = packPlanFlights(flights);
    const b = packPlanFlights(flights);
    expect(b.t).toEqual(a.t); // same numbers …
    expect(b.t).not.toBe(a.t); // … in a fresh concatenation each time
  });

  it("drop only the resolver from the config", () => {
    const withFn = resolveConfig({ sepMinNmAt: () => 3 });
    const c = cloneableConfig(withFn);
    expect("sepMinNmAt" in c).toBe(false);
    expect(c.horizontal).toEqual(withFn.horizontal);
    expect(c.buffer).toEqual(withFn.buffer);
    // Everything left is structured-cloneable.
    expect(() => structuredClone(c)).not.toThrow();
  });
});
