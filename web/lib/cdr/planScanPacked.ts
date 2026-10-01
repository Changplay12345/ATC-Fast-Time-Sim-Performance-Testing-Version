/**
 * The plan scan's inputs as flat typed arrays, for the trip to the worker.
 *
 * A traffic day is ~2 000 flights of ~500 samples: a million small objects.
 * Structured-cloning those into a worker is a main-thread stall in its own
 * right (hundreds of ms), which would defeat the point of the worker. Packed
 * into a handful of Float64Arrays the same data copies in a few ms, and
 * unpacks on the worker side into the exact numbers the scan would have read
 * here — so the worker's result is the one the main thread would have found.
 *
 * Only the fields the scan reads are carried: the sample time, position and
 * altitude (see `scanPair`/`boundsOf`). The rest of an `AircraftState` is
 * filled with placeholders on the way back; nothing in a `PlanConflict`
 * derives from them.
 */

import type { AircraftState } from "@/lib/useSimPlayback";
import type { SectorCollection } from "@/lib/geojson";

import type { CdrConfig } from "./config";
import type { PlanConflict, PlanFlight } from "./planScan";

type Sample = PlanFlight["samples"][number];

export interface PackedPlanFlights {
  ids: string[];
  callsigns: string[];
  offsets: Float64Array;
  durations: Float64Array;
  /** Samples per flight, in order; the arrays below are their concatenation. */
  counts: Int32Array;
  t: Float64Array;
  lat: Float64Array;
  lon: Float64Array;
  /** NaN where the sample has no altitude. */
  alt: Float64Array;
}

/** Worker request: the packed flights plus the config with its function
 *  (`sepMinNmAt`) left out — a function cannot be cloned, so the worker
 *  rebuilds it from the TMA polygon (see `makeSepMinNmAt`). */
export interface PlanScanRequest {
  id: number;
  packed: PackedPlanFlights;
  cfg: Omit<CdrConfig, "sepMinNmAt">;
  tma: SectorCollection | null;
}

export interface PlanScanResponse {
  id: number;
  conflicts: PlanConflict[];
  /** Scan wall time in the worker, for the console. */
  ms: number;
}

interface PackedSamples {
  t: Float64Array;
  lat: Float64Array;
  lon: Float64Array;
  alt: Float64Array;
}

// A flight's sample table is immutable once built (`toSamples` caches it on
// the point array), so its packed form is cached the same way: an applied fix
// re-packs only the flight it moved.
const packedCache = new WeakMap<Sample[], PackedSamples>();

function packSamples(samples: Sample[]): PackedSamples {
  const hit = packedCache.get(samples);
  if (hit) return hit;
  const n = samples.length;
  const out: PackedSamples = {
    t: new Float64Array(n),
    lat: new Float64Array(n),
    lon: new Float64Array(n),
    alt: new Float64Array(n),
  };
  for (let i = 0; i < n; i++) {
    const s = samples[i];
    out.t[i] = s.t;
    out.lat[i] = s.lat;
    out.lon[i] = s.lon;
    out.alt[i] = s.altitudeFt == null ? NaN : s.altitudeFt;
  }
  packedCache.set(samples, out);
  return out;
}

export function packPlanFlights(flights: PlanFlight[]): PackedPlanFlights {
  const per = flights.map((f) => packSamples(f.samples));
  let total = 0;
  for (const p of per) total += p.t.length;
  const out: PackedPlanFlights = {
    ids: flights.map((f) => f.id),
    callsigns: flights.map((f) => f.callsign),
    offsets: Float64Array.from(flights, (f) => f.offsetSec),
    durations: Float64Array.from(flights, (f) => f.durationSec),
    counts: Int32Array.from(per, (p) => p.t.length),
    t: new Float64Array(total),
    lat: new Float64Array(total),
    lon: new Float64Array(total),
    alt: new Float64Array(total),
  };
  let at = 0;
  for (const p of per) {
    out.t.set(p.t, at);
    out.lat.set(p.lat, at);
    out.lon.set(p.lon, at);
    out.alt.set(p.alt, at);
    at += p.t.length;
  }
  return out;
}

export function unpackPlanFlights(packed: PackedPlanFlights): PlanFlight[] {
  const flights: PlanFlight[] = [];
  let at = 0;
  for (let i = 0; i < packed.ids.length; i++) {
    const n = packed.counts[i];
    const samples: Sample[] = new Array(n);
    for (let k = 0; k < n; k++) {
      const j = at + k;
      const alt = packed.alt[j];
      samples[k] = {
        t: packed.t[j],
        lat: packed.lat[j],
        lon: packed.lon[j],
        altitudeFt: Number.isNaN(alt) ? null : alt,
        // Placeholders — not read by the scan (see the module comment).
        track: 0,
        gsKt: 0,
        tasKt: null,
        phase: "cruise" as AircraftState["phase"],
      };
    }
    at += n;
    flights.push({
      id: packed.ids[i],
      callsign: packed.callsigns[i],
      samples,
      offsetSec: packed.offsets[i],
      durationSec: packed.durations[i],
    });
  }
  return flights;
}

/** The config without its resolver function, so it can be posted. */
export function cloneableConfig(cfg: CdrConfig): Omit<CdrConfig, "sepMinNmAt"> {
  const { sepMinNmAt: _fn, ...rest } = cfg;
  void _fn;
  return rest;
}
