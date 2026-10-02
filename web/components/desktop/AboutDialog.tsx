"use client";

/**
 * AboutDialog — the desktop app's "About / updates" panel: which version is
 * running (app, engine, navdata cycle), whether a newer one exists, and the
 * support actions (open the logs or exports folder). Desktop build only.
 *
 * Installing an update restarts the app, so the button is held back while a
 * replay is running — the update waits for the user, never the reverse.
 */

import { useEffect, useState } from "react";

import NavIcon from "@/components/nav/NavIcon";
import { API_BASE, BACKEND, apiFetch } from "@/lib/backend";
import {
  appInfo,
  openExportsFolder,
  openLicence,
  openLogsFolder,
  type AppInfo,
  type DesktopData,
  type DesktopUpdate,
} from "@/lib/desktop";

interface Props {
  onClose: () => void;
  update: DesktopUpdate;
  /** Navigation data: what is loaded and whether a newer pack is waiting. */
  data: DesktopData;
  /** A replay or generation is in progress — hold the install button. */
  busy: boolean;
}

interface EngineHealth {
  version?: string;
  airac?: string;
  waypoint_count?: number;
}

const SUPPORT_EMAIL = "kruammek@bearcat.co.th";
/** This build's release notes (RELEASE_NOTES.md, inlined by desktop/build.ps1),
 *  so after an update the user can see what it brought. */
const WHATS_NEW = (process.env.NEXT_PUBLIC_RELEASE_NOTES ?? "").trim();

function statusLine(u: DesktopUpdate): string {
  switch (u.status) {
    case "checking":
      return "Checking for updates…";
    case "none":
      return "You are up to date.";
    case "available":
      return `Version ${u.version} is available.`;
    case "downloading":
      return u.progress == null
        ? "Downloading the update…"
        : `Downloading the update… ${Math.round(u.progress * 100)}%`;
    case "error":
      // The technical reason is in the tooltip; this is what the user can act on.
      return "Could not reach the update server. Check your connection and try again.";
    default:
      return "Updates are checked automatically while the app is open.";
  }
}

function dataLine(d: DesktopData): string {
  const s = d.status;
  if (s?.pending) {
    return `Navigation data ${s.pending.version} has been downloaded. It is used the next time the app starts.`;
  }
  if (d.checking) return "Checking for new navigation data…";
  if (s?.error) {
    return "Could not check for new navigation data. Check your connection and try again.";
  }
  if (!s?.version) return "Navigation data updates arrive automatically while the app is open.";
  const origin = s.source === "pack" ? "a downloaded update" : "included with the app";
  return `Navigation data ${s.version} (${origin}).${d.checkedAt ? " It is up to date." : ""}`;
}

export default function AboutDialog({ onClose, update, data, busy }: Props) {
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [engine, setEngine] = useState<EngineHealth | null>(null);

  useEffect(() => {
    appInfo().then(setInfo).catch(() => setInfo(null));
    apiFetch(`${API_BASE}/api/health`)
      .then((r) => r.json())
      .then(setEngine)
      .catch(() => setEngine(null));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const working = update.status === "checking" || update.status === "downloading";

  return (
    <div className="dlm-backdrop" onClick={onClose} role="presentation">
      <div
        className="dlm-card about-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="about-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dlm-head">
          <h3 id="about-title">
            <NavIcon name="info" size={15} /> About
          </h3>
          <button className="dlm-close" onClick={onClose} aria-label="Close the About dialog">
            ✕
          </button>
        </div>

        <div className="about-body">
          <p className="about-name">ATC Fast-Time Simulation Tool</p>
          <p className="about-by">BearCat AEL Co</p>

          <dl className="about-facts">
            <div>
              <dt>Version</dt>
              <dd>{info?.version ?? BACKEND.version ?? "—"}</dd>
            </div>
            <div>
              <dt>Engine</dt>
              <dd>{engine?.version ?? "—"}</dd>
            </div>
            <div>
              <dt>Navdata cycle</dt>
              <dd>{engine?.airac ?? "—"}</dd>
            </div>
          </dl>

          <div className={`about-update about-update-${update.status}`} role="status">
            <p title={update.status === "error" ? update.error : undefined}>{statusLine(update)}</p>
            {update.status === "available" && update.notes && (
              <pre className="about-notes">{update.notes}</pre>
            )}
            {update.status === "downloading" && (
              <div className="about-bar" aria-hidden="true">
                <span
                  style={{
                    width: update.progress == null ? "100%" : `${Math.round(update.progress * 100)}%`,
                  }}
                  className={update.progress == null ? "indeterminate" : undefined}
                />
              </div>
            )}
            <div className="about-actions">
              {update.status === "available" || update.status === "downloading" ? (
                <button
                  type="button"
                  className="about-btn primary"
                  onClick={() => void update.install()}
                  disabled={working || busy}
                  title={
                    busy
                      ? "Pause the replay first — installing restarts the app"
                      : "Download the update and restart the app"
                  }
                >
                  Install and restart
                </button>
              ) : (
                <button
                  type="button"
                  className="about-btn"
                  onClick={() => void update.check()}
                  disabled={working}
                >
                  Check for updates
                </button>
              )}
            </div>
            {update.status === "available" && (
              <p className="about-hint">
                {busy
                  ? "A replay is running. Pause it to install — the app restarts."
                  : "The app closes, updates itself and reopens. There is no installer to click through."}
              </p>
            )}
          </div>

          <div
            className={`about-update${data.status?.pending ? " about-update-available" : ""}${
              !data.status?.pending && data.status?.error ? " about-update-error" : ""
            }`}
            role="status"
          >
            <p title={data.status?.error ?? undefined}>{dataLine(data)}</p>
            {data.status?.pending?.notes && (
              <pre className="about-notes">{data.status.pending.notes}</pre>
            )}
            <div className="about-actions">
              {data.status?.pending ? (
                <button
                  type="button"
                  className="about-btn primary"
                  onClick={() => void data.restart()}
                  disabled={busy}
                  title={busy ? "Pause the replay first — this restarts the app" : undefined}
                >
                  Restart now
                </button>
              ) : (
                <button
                  type="button"
                  className="about-btn"
                  onClick={() => void data.check()}
                  disabled={data.checking}
                >
                  Check for new data
                </button>
              )}
            </div>
          </div>

          {WHATS_NEW && (
            <div className="about-new">
              <p className="about-new-title">
                What&apos;s new in {info?.version ?? BACKEND.version ?? "this version"}
              </p>
              <pre className="about-notes">{WHATS_NEW}</pre>
            </div>
          )}

          <div className="about-support">
            <button type="button" className="about-btn" onClick={() => void openLogsFolder()}>
              Open logs folder
            </button>
            <button type="button" className="about-btn" onClick={() => void openExportsFolder()}>
              Open exports folder
            </button>
          </div>
          <div className="about-support">
            <button type="button" className="about-btn" onClick={() => void openLicence("eula")}>
              Licence agreement
            </button>
            <button
              type="button"
              className="about-btn"
              onClick={() => void openLicence("third-party")}
            >
              Third-party licences
            </button>
          </div>
          <p className="about-foot">
            Support: <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
          </p>
          <p className="about-foot">
            Copyright © 2026 BearCat AEL Co. Navigation and airspace data are for simulation
            only and are not certified for operational use.
          </p>
        </div>
      </div>
    </div>
  );
}
