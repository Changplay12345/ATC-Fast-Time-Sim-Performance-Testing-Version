"use client";

/**
 * ServerLoadPanel — what the simulation engine is using, measured on the
 * machine it runs on: memory and CPU now and over the last ten minutes, the
 * worker pool, and a built-in test that replays the generated flights as
 * several people at once and turns the result into sizing figures.
 *
 * Works against any engine (the desktop app's own, an office server, the
 * cloud): the numbers always describe the server, never this computer.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import NavIcon from "@/components/nav/NavIcon";
import {
  fetchResources,
  sizing,
  startLoadTest,
  type LoadTestStatus,
  type Resources,
} from "@/lib/resources";

interface Props {
  onClose: () => void;
  /** Flights have been generated: the load test has something to replay. */
  hasFlights: boolean;
}

const gb = (v: number | undefined | null) => (v == null ? "—" : `${v.toFixed(2)} GB`);
const secs = (v: number) => (v >= 90 ? `${(v / 60).toFixed(1)} min` : `${v.toFixed(1)} s`);

/** Ten minutes of memory (filled area) and CPU (line), drawn to scale. */
function History({ data, cores }: { data: Resources["history"]; cores: number }) {
  const W = 560;
  const H = 110;
  const pad = { l: 34, r: 34, t: 8, b: 18 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  if (data.length < 2) {
    return <p className="about-hint">Collecting the first samples…</p>;
  }
  const t0 = data[0].t;
  const t1 = data[data.length - 1].t;
  const span = Math.max(60, t1 - t0);
  const memMax = Math.max(1, Math.ceil(Math.max(...data.map((d) => d.rss_gb)) * 1.2));
  const cpuMax = Math.max(100, cores * 100);
  const x = (t: number) => pad.l + ((t - t0) / span) * iw;
  const yMem = (v: number) => pad.t + ih - (v / memMax) * ih;
  const yCpu = (v: number) => pad.t + ih - (Math.min(v, cpuMax) / cpuMax) * ih;
  const mem = data.map((d) => `${x(d.t).toFixed(1)},${yMem(d.rss_gb).toFixed(1)}`).join(" ");
  const cpu = data.map((d) => `${x(d.t).toFixed(1)},${yCpu(d.cpu_pct).toFixed(1)}`).join(" ");
  const area = `${x(data[0].t).toFixed(1)},${(pad.t + ih).toFixed(1)} ${mem} ${x(t1).toFixed(1)},${(pad.t + ih).toFixed(1)}`;
  return (
    <svg className="srv-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Engine memory and CPU over the last ten minutes">
      <line x1={pad.l} x2={W - pad.r} y1={pad.t + ih} y2={pad.t + ih} className="srv-axis" />
      <line x1={pad.l} x2={W - pad.r} y1={pad.t + ih / 2} y2={pad.t + ih / 2} className="srv-grid" />
      <polygon points={area} className="srv-mem-area" />
      <polyline points={mem} className="srv-mem" />
      <polyline points={cpu} className="srv-cpu" />
      <text x={pad.l - 4} y={pad.t + 4} textAnchor="end" className="srv-tick srv-mem-t">{memMax} GB</text>
      <text x={pad.l - 4} y={pad.t + ih / 2 + 4} textAnchor="end" className="srv-tick srv-mem-t">{(memMax / 2).toFixed(1)}</text>
      <text x={W - pad.r + 4} y={pad.t + 4} className="srv-tick srv-cpu-t">{cores} cores</text>
      <text x={W - pad.r + 4} y={pad.t + ih / 2 + 4} className="srv-tick srv-cpu-t">{(cores / 2).toFixed(0)}</text>
      <text x={pad.l} y={H - 4} className="srv-tick">{Math.round(span / 60)} min ago</text>
      <text x={W - pad.r} y={H - 4} textAnchor="end" className="srv-tick">now</text>
    </svg>
  );
}

function TestResult({ test }: { test: LoadTestStatus }) {
  if (test.state !== "done" && test.state !== "failed") return null;
  const s = test.summary;
  if (!s) return <p className="about-hint">The test failed: {test.error ?? "no result"}</p>;
  const z = sizing(s, test.flights_per_run ?? s.flights);
  return (
    <div className="srv-result">
      <table className="srv-table">
        <thead>
          <tr><th>User</th><th>Run</th><th>Flights</th><th>Waited</th></tr>
        </thead>
        <tbody>
          {(test.done ?? []).map((r) => (
            <tr key={`${r.user}-${r.run}`}>
              <td>{r.user}</td><td>{r.run}</td>
              <td>{r.flights}{r.failed ? ` (${r.failed} failed)` : ""}</td>
              <td>{secs(r.seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="srv-facts">
        <div><dt>Wait per run</dt><dd>{secs(s.seconds_mean)} mean · {secs(s.seconds_worst)} worst</dd></div>
        <div><dt>Engine memory</dt><dd>{gb(s.rss_before_gb)} before · {gb(s.rss_peak_gb)} peak · {gb(s.rss_after_gb)} kept</dd></div>
        <div><dt>CPU per flight</dt><dd>{s.cpu_s_per_flight == null ? "—" : `${s.cpu_s_per_flight.toFixed(3)} s`} · {s.flights_per_s ?? "—"} flights/s</dd></div>
      </dl>
      <div className="srv-sizing">
        <p className="about-new-title">What a server needs for this</p>
        <ul>
          <li>RAM: <b>{z.ramMinGb} GB</b> minimum, <b>{z.ramComfortableGb} GB</b> comfortable.</li>
          <li>Cores like this machine's: <b>{z.coresFor60s.toFixed(1)}</b> for a run in 60 s, <b>{z.coresFor30s.toFixed(1)}</b> for 30 s. A cloud vCPU is about half a desktop core: double these there.</li>
          <li>Usage-billed cloud memory for what stays resident: about <b>US${z.cloudMemoryUsdPerMonth.toFixed(0)}/month</b>.</li>
          {(test.users ?? 1) > 1 && (
            <li>{test.users} people at once shared the same cores; each waited about {test.users}× the single-user time.</li>
          )}
        </ul>
      </div>
    </div>
  );
}

export default function ServerLoadPanel({ onClose, hasFlights }: Props) {
  const [res, setRes] = useState<Resources | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [users, setUsers] = useState(3);
  const [runs, setRuns] = useState(1);
  const [flights, setFlights] = useState(1000);
  const [starting, setStarting] = useState(false);

  const running = res?.load_test.state === "running";

  const refresh = useCallback(async () => {
    try {
      setRes(await fetchResources());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), running ? 1000 : 2000);
    return () => window.clearInterval(id);
  }, [refresh, running]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const start = async () => {
    setStarting(true);
    try {
      await startLoadTest(users, runs, flights);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const progress = useMemo(() => {
    const t = res?.load_test;
    if (!t || t.state !== "running") return null;
    const total = (t.users ?? 1) * (t.runs ?? 1) * (t.flights_per_run ?? 0);
    const done = Object.values(t.progress ?? {}).reduce((a, b) => a + b, 0);
    return { done, total, pct: total ? Math.round((100 * done) / total) : 0 };
  }, [res]);

  const e = res?.engine;
  const m = res?.machine;

  return (
    <div className="dlm-backdrop" onClick={onClose} role="presentation">
      <div
        className="dlm-card about-card srv-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="srv-title"
        onClick={(ev) => ev.stopPropagation()}
      >
        <div className="dlm-head">
          <h3 id="srv-title">
            <NavIcon name="chart" size={15} /> Server load
          </h3>
          <button className="dlm-close" onClick={onClose} aria-label="Close the server load panel">✕</button>
        </div>

        <div className="about-body">
          <p className="about-hint">
            Measured on the machine running the simulation engine
            {res ? ` (engine ${res.version}, ${m?.cores} cores / ${m?.threads} threads, ${m?.ram_total_gb} GB RAM)` : ""}.
            {error && <> Could not read it: {error}</>}
          </p>

          <dl className="about-facts">
            <div>
              <dt>Engine memory</dt>
              <dd>{gb(e?.rss_gb)}</dd>
              <dd className="srv-sub">idle {gb(e?.idle_rss_gb)} · peak {gb(e?.peak_rss_gb)}</dd>
            </div>
            <div>
              <dt>Engine CPU</dt>
              <dd>{e ? `${(e.cpu_pct / 100).toFixed(1)} cores` : "—"}</dd>
              <dd className="srv-sub">{e ? `${e.workers_running} of ${e.workers_configured} workers running` : ""}</dd>
            </div>
            <div>
              <dt>Whole machine</dt>
              <dd>{m ? `${m.ram_used_gb} / ${m.ram_total_gb} GB` : "—"}</dd>
              <dd className="srv-sub">{m ? `${m.cpu_pct}% CPU · ${res?.cache.entries ?? 0} flights cached` : ""}</dd>
            </div>
          </dl>

          <History data={res?.history ?? []} cores={m?.cores ?? 1} />

          <div className="about-update srv-test">
            <p className="about-new-title">Simulate several people at once</p>
            <p className="about-hint">
              Replays the flights you have generated as if several people pressed Generate all together. Results are
              discarded. Takes about as long as the runs would.
            </p>
            <div className="srv-form">
              <label>Users
                <select value={users} onChange={(ev) => setUsers(Number(ev.target.value))} disabled={running}>
                  {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <label>Runs each
                <select value={runs} onChange={(ev) => setRuns(Number(ev.target.value))} disabled={running}>
                  {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <label>Flights per run
                <select value={flights} onChange={(ev) => setFlights(Number(ev.target.value))} disabled={running}>
                  {[100, 500, 1000, 2000].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}
                </select>
              </label>
              <button
                type="button"
                className="about-btn primary"
                onClick={() => void start()}
                disabled={!hasFlights || running || starting}
                title={hasFlights ? undefined : "Generate some flights first; the test replays them"}
              >
                {running ? "Running…" : "Run test"}
              </button>
            </div>
            {progress && (
              <div className="about-bar" aria-hidden="true">
                <span style={{ width: `${progress.pct}%` }} />
              </div>
            )}
            {progress && (
              <p className="about-hint">{progress.done.toLocaleString()} of {progress.total.toLocaleString()} flights</p>
            )}
            {res && <TestResult test={res.load_test} />}
          </div>
        </div>
      </div>
    </div>
  );
}
