"use client";

/**
 * DataBanner — tells the user that newer navigation data has been downloaded
 * and is waiting for a restart. Desktop build only.
 *
 * Data packs arrive on their own (the shell downloads and verifies them in
 * the background) but are only used from the next start, so a running
 * simulation never has its data changed under it. This is the nudge to
 * restart; "Later" hides it for this session, and the pack is picked up
 * whenever the app is next opened anyway.
 */

import { useEffect, useState } from "react";

import type { DesktopData } from "@/lib/desktop";

interface Props {
  data: DesktopData;
  /** A replay is running — hold the restart button. */
  busy: boolean;
}

const DISMISS_KEY = "atc.dataDismissed";

export default function DataBanner({ data, busy }: Props) {
  const [dismissed, setDismissed] = useState<string | null>(null);
  useEffect(() => {
    try {
      setDismissed(window.sessionStorage.getItem(DISMISS_KEY));
    } catch {
      /* storage unavailable: the banner simply is not remembered */
    }
  }, []);

  const pending = data.status?.pending;
  if (!pending || dismissed === pending.version) return null;

  const later = () => {
    setDismissed(pending.version);
    try {
      window.sessionStorage.setItem(DISMISS_KEY, pending.version);
    } catch {
      /* see above */
    }
  };

  return (
    <div className="update-banner" role="status" aria-live="polite">
      <div className="update-banner-text">
        <strong>New navigation data is ready</strong>
        <span>
          {pending.airac ? `AIRAC ${pending.airac} (${pending.version}). ` : `${pending.version}. `}
          {busy
            ? "Pause the replay to restart."
            : "Restart to use it, or it starts with the app next time."}
        </span>
      </div>
      <div className="update-banner-actions">
        <button
          type="button"
          className="about-btn primary"
          onClick={() => void data.restart()}
          disabled={busy}
          title={busy ? "Pause the replay first — this restarts the app" : undefined}
        >
          Restart now
        </button>
        <button type="button" className="about-btn" onClick={later}>
          Later
        </button>
      </div>
    </div>
  );
}
