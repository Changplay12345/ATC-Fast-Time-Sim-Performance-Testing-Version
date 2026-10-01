"use client";

/**
 * UpdateBanner — tells the user a new version exists, wherever they are in
 * the app (the nav bar's About button is not on screen until a flight
 * exists, and a dot on an icon is easy to miss). Desktop build only.
 *
 * It appears when the background check finds an update and stays until the
 * user installs it or chooses "Later", which hides it for this session — the
 * next launch (or the next version) brings it back. Installing restarts the
 * app, so the button waits while a replay is running.
 */

import { useEffect, useState } from "react";

import type { DesktopUpdate } from "@/lib/desktop";

interface Props {
  update: DesktopUpdate;
  /** A replay is running — hold the install button. */
  busy: boolean;
  /** Open the About dialog (release notes, versions). */
  onDetails: () => void;
}

const DISMISS_KEY = "atc.updateDismissed";

export default function UpdateBanner({ update, busy, onDetails }: Props) {
  const [dismissed, setDismissed] = useState<string | null>(null);
  useEffect(() => {
    try {
      setDismissed(window.sessionStorage.getItem(DISMISS_KEY));
    } catch {
      /* storage unavailable: the banner simply is not remembered */
    }
  }, []);

  const offered =
    update.status === "available" ||
    update.status === "downloading" ||
    (update.status === "error" && !!update.version);
  if (!offered || !update.version) return null;
  if (update.status === "available" && dismissed === update.version) return null;

  const later = () => {
    setDismissed(update.version ?? null);
    try {
      window.sessionStorage.setItem(DISMISS_KEY, update.version ?? "");
    } catch {
      /* see above */
    }
  };

  const downloading = update.status === "downloading";
  const pct = update.progress == null ? null : Math.round(update.progress * 100);

  return (
    <div className="update-banner" role="status" aria-live="polite">
      <div className="update-banner-text">
        <strong>
          {downloading
            ? pct == null
              ? "Downloading the update…"
              : `Downloading the update… ${pct}%`
            : update.status === "error"
              ? "The update could not be installed"
              : `Version ${update.version} is available`}
        </strong>
        <span>
          {downloading
            ? "The app will restart when it is ready."
            : update.status === "error"
              ? "Check your connection and try again."
              : busy
                ? "Pause the replay to install — the app restarts."
                : "Install it now, or later from About."}
        </span>
        {downloading && (
          <div className="about-bar" aria-hidden="true">
            <span
              style={{ width: pct == null ? "100%" : `${pct}%` }}
              className={pct == null ? "indeterminate" : undefined}
            />
          </div>
        )}
      </div>
      {!downloading && (
        <div className="update-banner-actions">
          <button
            type="button"
            className="about-btn primary"
            onClick={() => void update.install()}
            disabled={busy}
            title={busy ? "Pause the replay first — installing restarts the app" : undefined}
          >
            Install and restart
          </button>
          <button type="button" className="about-btn" onClick={onDetails}>
            What&apos;s new
          </button>
          <button type="button" className="about-btn" onClick={later}>
            Later
          </button>
        </div>
      )}
    </div>
  );
}
