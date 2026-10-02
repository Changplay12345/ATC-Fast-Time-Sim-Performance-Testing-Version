"use client";

/**
 * RequiredUpdate — shown instead of the app when the installed version is
 * below the minimum the current release supports (a critical fix: the kind
 * of bug that would make results wrong). Desktop build only.
 *
 * Unlike the update banner this cannot be dismissed, and it does not wait
 * for a replay to be paused: results from this version are not to be relied
 * on. The only way on is to install the update; the app restarts itself.
 */

import type { DesktopUpdate } from "@/lib/desktop";

interface Props {
  update: DesktopUpdate;
}

export default function RequiredUpdate({ update }: Props) {
  if (!update.required) return null;

  const downloading = update.status === "downloading";
  const failed = update.status === "error";
  const pct = update.progress == null ? null : Math.round(update.progress * 100);

  return (
    <div className="dlm-backdrop required-update" role="presentation">
      <div
        className="dlm-card about-card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="required-update-title"
        aria-describedby="required-update-text"
      >
        <div className="dlm-head">
          <h3 id="required-update-title">Update required</h3>
        </div>
        <div className="about-body">
          <p id="required-update-text" className="about-name">
            This version can no longer be used.
          </p>
          <p className="about-by">
            Version {update.version} fixes a problem serious enough that earlier versions are
            withdrawn. Install it to continue — the app closes, updates itself and reopens.
          </p>
          {update.notes && <pre className="about-notes">{update.notes}</pre>}
          <div
            className={`about-update about-update-${failed ? "error" : "available"}`}
            role="status"
          >
            <p title={failed ? update.error : undefined}>
              {downloading
                ? pct == null
                  ? "Downloading the update…"
                  : `Downloading the update… ${pct}%`
                : failed
                  ? "The update could not be downloaded. Check your connection and try again."
                  : `Version ${update.version} is ready to install.`}
            </p>
            {downloading && (
              <div className="about-bar" aria-hidden="true">
                <span
                  style={{ width: pct == null ? "100%" : `${pct}%` }}
                  className={pct == null ? "indeterminate" : undefined}
                />
              </div>
            )}
            <div className="about-actions">
              <button
                type="button"
                className="about-btn primary"
                onClick={() => void update.install()}
                disabled={downloading}
                autoFocus
              >
                {failed ? "Try again" : "Install and restart"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
