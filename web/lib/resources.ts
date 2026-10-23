/**
 * The engine's resource meter (GET /api/resources, POST /api/loadtest):
 * what the server running the simulation uses, measured on that server.
 */

import { API_BASE, apiFetch } from "@/lib/backend";

export interface ResourceSample {
  t: number;
  rss_gb: number;
  cpu_pct: number;
  procs: number;
}

export interface LoadTestRun {
  user: number;
  run: number;
  flights: number;
  failed: number;
  seconds: number;
}

export interface LoadTestSummary {
  flights: number;
  failed: number;
  seconds_total: number;
  seconds_mean: number;
  seconds_worst: number;
  cpu_seconds: number;
  cpu_s_per_flight: number | null;
  rss_before_gb: number;
  rss_peak_gb: number;
  rss_after_gb: number;
  flights_per_s: number | null;
}

export interface LoadTestStatus {
  state: "idle" | "running" | "done" | "failed";
  users?: number;
  runs?: number;
  flights_per_run?: number;
  started_at?: number;
  finished_at?: number;
  progress?: Record<string, number>;
  done?: LoadTestRun[];
  summary?: LoadTestSummary;
  error?: string | null;
}

export interface Resources {
  version: string;
  machine: { cores: number; threads: number; ram_total_gb: number; ram_used_gb: number; cpu_pct: number };
  engine: {
    rss_gb: number;
    cpu_pct: number;
    processes: number;
    idle_rss_gb: number;
    peak_rss_gb: number;
    cpu_seconds_total: number;
    workers_configured: number;
    workers_running: number;
  };
  cache: { entries: number; cap: number };
  history: ResourceSample[];
  sample_every_s: number;
  load_test: LoadTestStatus;
}

export async function fetchResources(): Promise<Resources> {
  const r = await apiFetch(`${API_BASE}/api/resources`);
  if (!r.ok) throw new Error(`resources: HTTP ${r.status}`);
  return r.json();
}

export async function startLoadTest(users: number, runs: number, flights: number): Promise<void> {
  const r = await apiFetch(`${API_BASE}/api/loadtest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ users, runs, flights }),
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* no body */
    }
    throw new Error(detail);
  }
}

/**
 * What a server needs, from a measured test. The same arithmetic as
 * scripts/resource_meter.py, so the panel and the script agree.
 */
export function sizing(s: LoadTestSummary, flightsPerRun: number) {
  const cpuPerFlight = s.cpu_s_per_flight ?? 0;
  return {
    /** GB of RAM to provision: peak plus headroom plus an OS. */
    ramMinGb: Math.max(4, Math.round(s.rss_peak_gb + 2)),
    ramComfortableGb: Math.max(8, Math.round(2 * s.rss_peak_gb + 2)),
    /** Cores like this machine's, for one run in 60 s / 30 s. */
    coresFor60s: cpuPerFlight * flightsPerRun / 60,
    coresFor30s: cpuPerFlight * flightsPerRun / 30,
    /** A usage-billed cloud's memory charge for what stays resident. */
    cloudMemoryUsdPerMonth: s.rss_after_gb * 10,
  };
}
