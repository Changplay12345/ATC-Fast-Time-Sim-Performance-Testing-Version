"use client";

/**
 * usePlanScan — the strategic conflict scan, kept off the main thread.
 *
 * A FULL scan (new flight set, or a config change) goes to a Web Worker
 * (`planScan.worker.ts`) and the result lands asynchronously; `pending` is
 * true meanwhile. A scan that is superseded before it finishes — the airspace
 * polygons arriving one after another right after an import each changed the
 * config — is terminated rather than left to burn a core on an answer nobody
 * will read.
 *
 * An INCREMENTAL rescan (only some flights moved: an applied fix) is a couple
 * of milliseconds, so it stays synchronous on the main thread and lands in the
 * same tick, as before — the auto-resolve loop depends on that pace.
 *
 * Without `Worker` (tests, SSR) the full scan runs inline, as it always did.
 */

import { useEffect, useRef, useState } from "react";

import type { SectorCollection } from "@/lib/geojson";

import type { CdrConfig } from "./config";
import {
  rescanFlightPlanConflicts,
  scanFlightPlanConflicts,
  type PlanConflict,
  type PlanFlight,
} from "./planScan";
import {
  cloneableConfig,
  packPlanFlights,
  type PlanScanRequest,
  type PlanScanResponse,
} from "./planScanPacked";

export interface PlanScanResult {
  conflicts: PlanConflict[];
  /** A full scan is running in the worker; `conflicts` is the previous answer
   *  for the same flight set, or empty for a new one. */
  pending: boolean;
}

interface LastScan {
  flights: PlanFlight[];
  cfg: CdrConfig;
  conflicts: PlanConflict[];
}

const EMPTY: PlanConflict[] = [];
const IDLE: PlanScanResult = { conflicts: EMPTY, pending: false };

export function usePlanScan(
  active: boolean,
  flights: PlanFlight[],
  cfg: CdrConfig,
  tma: SectorCollection | null,
): PlanScanResult {
  const [result, setResult] = useState<PlanScanResult>(IDLE);
  const lastRef = useRef<LastScan | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const busyRef = useRef(false);
  const jobRef = useRef(0);

  useEffect(() => {
    const dropWorker = () => {
      workerRef.current?.terminate();
      workerRef.current = null;
      busyRef.current = false;
    };
    const settle = (conflicts: PlanConflict[]) => {
      lastRef.current = { flights, cfg, conflicts };
      setResult({ conflicts, pending: false });
    };

    if (!active || flights.length < 2) {
      jobRef.current++; // orphan any scan in flight
      if (busyRef.current) dropWorker();
      lastRef.current = null;
      setResult(IDLE);
      return;
    }

    // Same set, same config, some flights re-timed → patch the last answer.
    const prev = lastRef.current;
    let sameSet = false;
    if (prev && prev.cfg === cfg && prev.flights.length === flights.length) {
      const changed = new Set<string>();
      sameSet = true;
      for (let i = 0; i < flights.length; i++) {
        const a = flights[i];
        const b = prev.flights[i];
        if (a.id !== b.id) {
          sameSet = false;
          break;
        }
        if (
          a.samples !== b.samples ||
          a.offsetSec !== b.offsetSec ||
          a.durationSec !== b.durationSec
        ) {
          changed.add(a.id);
        }
      }
      if (sameSet) {
        jobRef.current++;
        if (busyRef.current) dropWorker();
        settle(rescanFlightPlanConflicts(prev.conflicts, flights, changed, cfg));
        return;
      }
    }

    if (typeof Worker === "undefined") {
      settle(scanFlightPlanConflicts(flights, cfg));
      return;
    }

    const id = ++jobRef.current;
    if (busyRef.current) {
      // eslint-disable-next-line no-console
      console.debug(`[plan-scan] job ${id - 1} superseded (${sameSet ? "config" : "flights"} changed)`);
      dropWorker(); // superseded mid-scan: don't wait for it
    }
    // eslint-disable-next-line no-console
    console.debug(`[plan-scan] job ${id}: ${flights.length} flights, tma ${tma ? "loaded" : "pending"}`);
    let worker = workerRef.current;
    if (!worker) {
      worker = new Worker(new URL("./planScan.worker.ts", import.meta.url));
      workerRef.current = worker;
    }
    worker.onmessage = (e: MessageEvent<PlanScanResponse>) => {
      if (e.data.id !== jobRef.current) return; // stale
      busyRef.current = false;
      // eslint-disable-next-line no-console
      console.debug(`[plan-scan] ${flights.length} flights → ${e.data.conflicts.length} conflicts in ${Math.round(e.data.ms)} ms (worker)`);
      settle(e.data.conflicts);
    };
    worker.onerror = (err) => {
      // eslint-disable-next-line no-console
      console.error("[plan-scan] worker failed, scanning inline:", err.message);
      dropWorker();
      if (id === jobRef.current) settle(scanFlightPlanConflicts(flights, cfg));
    };
    busyRef.current = true;
    // Keep showing the last answer while the same flights are re-scanned under
    // a new config; a new set starts from nothing (the old rows would name
    // flights that no longer exist).
    const sameFlights =
      prev != null &&
      prev.flights.length === flights.length &&
      prev.flights.every((f, i) => f.id === flights[i].id);
    setResult({ conflicts: sameFlights && prev ? prev.conflicts : EMPTY, pending: true });
    const req: PlanScanRequest = {
      id,
      packed: packPlanFlights(flights),
      cfg: cloneableConfig(cfg),
      tma,
    };
    worker.postMessage(req);
  }, [active, flights, cfg, tma]);

  // Tear the worker down with the component.
  useEffect(
    () => () => {
      workerRef.current?.terminate();
      workerRef.current = null;
    },
    [],
  );

  return result;
}
