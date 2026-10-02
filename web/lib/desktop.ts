"use client";

/**
 * The page's side of the desktop shell: app info, logs, and auto-update.
 *
 * Only meaningful in the desktop build (`IS_DESKTOP`), where the shell exposes
 * `window.__TAURI__`. On the web every call rejects with "not a desktop
 * build" and the hook below stays idle, so nothing here needs guarding at the
 * call site beyond not rendering the UI.
 */

import { useCallback, useEffect, useRef, useState } from "react";

interface TauriGlobal {
  core: { invoke: <T>(cmd: string, args?: Record<string, unknown>) => Promise<T> };
  event: {
    listen: <T>(event: string, cb: (e: { payload: T }) => void) => Promise<() => void>;
  };
}

declare global {
  interface Window {
    __TAURI__?: TauriGlobal;
  }
}

function tauri(): TauriGlobal {
  const t = typeof window !== "undefined" ? window.__TAURI__ : undefined;
  if (!t) throw new Error("not a desktop build");
  return t;
}

export interface AppInfo {
  version: string;
  dataDir: string;
  logDir: string;
  exportsDir: string;
}

export interface UpdateCheck {
  available: boolean;
  /** This version is below the release's minimum and must update to go on. */
  required?: boolean;
  /** A newer release exists, but its staged rollout has not reached this
   *  installation and nobody asked for it. */
  heldBack?: boolean;
  /** The version on offer, when there is one. */
  version?: string;
  notes?: string;
  date?: string;
}

export const appInfo = () => tauri().core.invoke<AppInfo>("app_info");
export const openLogsFolder = () => tauri().core.invoke<void>("open_logs_folder");
export const openExportsFolder = () => tauri().core.invoke<void>("open_exports_folder");
/** Opens a licence text bundled with the app in the default viewer. */
export const openLicence = (which: "eula" | "third-party") =>
  tauri().core.invoke<void>("open_licence", { which });
/** `manual`: the user asked (About > Check for updates). A staged rollout
 *  holds a release back from the background check only. */
export const checkUpdate = (manual = false) =>
  tauri().core.invoke<UpdateCheck>("check_update", { manual });
/** Zips logs and versions into the exports folder; resolves to the file name. */
export const exportDiagnostics = () => tauri().core.invoke<string>("export_diagnostics");
/** Downloads and installs; on Windows the app closes itself when it finishes. */
export const installUpdate = () => tauri().core.invoke<void>("install_update");

export type UpdateStatus =
  | "idle"
  | "checking"
  | "none"
  | "available"
  | "downloading"
  | "error";

export interface DesktopUpdate {
  status: UpdateStatus;
  /** The offered version is mandatory: this one may not be used any more. */
  required: boolean;
  version?: string;
  notes?: string;
  /** 0–1 while downloading; null when the size is unknown. */
  progress: number | null;
  error?: string;
  /** When the last check finished. */
  checkedAt: number | null;
  /** `manual` = the user pressed the button (see `checkUpdate`). */
  check: (manual?: boolean) => Promise<void>;
  install: () => Promise<void>;
}

/** First check a few seconds after launch, so it never competes with start-up. */
const FIRST_CHECK_MS = 3_000;
/** Then hourly while the app is open, so a release reaches a running app the
 *  same day without the user having to restart it. */
const RECHECK_MS = 60 * 60 * 1000;

/**
 * Background update check for the desktop app. It only ever *reports* an
 * update — downloading and installing happen when the user asks, so a running
 * simulation is never interrupted.
 */
export function useDesktopUpdate(enabled: boolean): DesktopUpdate {
  const [state, setState] = useState<Omit<DesktopUpdate, "check" | "install">>({
    status: "idle",
    required: false,
    progress: null,
    checkedAt: null,
  });
  const busy = useRef(false);
  // Once the user has asked for an update by hand, later background checks
  // ask the same way: an update they were shown must not vanish an hour later
  // because the rollout has not reached them.
  const askedByHand = useRef(false);

  const check = useCallback(async (manual = false) => {
    if (!enabled || busy.current) return;
    busy.current = true;
    if (manual) askedByHand.current = true;
    setState((s) => ({ ...s, status: "checking", error: undefined }));
    try {
      const r = await checkUpdate(manual || askedByHand.current);
      setState({
        status: r.available ? "available" : "none",
        required: !!r.required,
        version: r.version,
        notes: r.notes,
        progress: null,
        checkedAt: Date.now(),
      });
    } catch (e) {
      setState((s) => ({
        ...s,
        status: "error",
        error: e instanceof Error ? e.message : String(e),
        checkedAt: Date.now(),
      }));
    } finally {
      busy.current = false;
    }
  }, [enabled]);

  const install = useCallback(async () => {
    if (!enabled || busy.current) return;
    busy.current = true;
    setState((s) => ({ ...s, status: "downloading", progress: 0, error: undefined }));
    let unlisten: (() => void) | undefined;
    try {
      unlisten = await tauri().event.listen<{ downloaded: number; total: number | null }>(
        "update-progress",
        (e) =>
          setState((s) => ({
            ...s,
            progress: e.payload.total ? e.payload.downloaded / e.payload.total : null,
          })),
      );
      await installUpdate();
      // On Windows the app has exited by now; elsewhere the shell restarts it.
    } catch (e) {
      setState((s) => ({
        ...s,
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      }));
    } finally {
      unlisten?.();
      busy.current = false;
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    const first = window.setTimeout(() => void check(), FIRST_CHECK_MS);
    const every = window.setInterval(() => void check(), RECHECK_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(every);
    };
  }, [enabled, check]);

  return { ...state, check, install };
}

// ---------------------------------------------------------------------------
// Navigation-data packs: newer data without a new program.
// ---------------------------------------------------------------------------

/** A data pack that has been downloaded and is used from the next start. */
export interface DataPending {
  version: string;
  airac?: string | null;
  notes?: string | null;
}

export interface DataStatus {
  /** The data the running engine loaded, e.g. "2026.09.03.1". */
  version?: string | null;
  /** Shipped with the program, or a downloaded pack. */
  source?: "bundled" | "pack" | null;
  pending?: DataPending | null;
  /** Why the last check failed, if it did. */
  error?: string | null;
}

export const dataStatus = () => tauri().core.invoke<DataStatus>("data_status");
/** Looks for a newer pack now; one that is found is downloaded and verified. */
export const checkDataUpdate = () => tauri().core.invoke<DataStatus>("check_data_update");
export const restartApp = () => tauri().core.invoke<void>("restart_app");

export interface DesktopData {
  status: DataStatus | null;
  checking: boolean;
  /** When the last manual check finished. */
  checkedAt: number | null;
  check: () => Promise<void>;
  restart: () => Promise<void>;
}

/**
 * The state of the navigation data: what is loaded, and whether a newer pack
 * has arrived. The shell checks, downloads and verifies in the background on
 * its own schedule and announces the result with a `data-status` event; this
 * hook only mirrors it (and lets About ask for a check right now).
 */
export function useDesktopData(enabled: boolean): DesktopData {
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let unlisten: (() => void) | undefined;
    dataStatus()
      .then((s) => alive && setStatus(s))
      .catch(() => undefined);
    tauri()
      .event.listen<DataStatus>("data-status", (e) => setStatus(e.payload))
      .then((off) => {
        if (alive) unlisten = off;
        else off();
      })
      .catch(() => undefined);
    return () => {
      alive = false;
      unlisten?.();
    };
  }, [enabled]);

  const check = useCallback(async () => {
    if (!enabled) return;
    setChecking(true);
    try {
      setStatus(await checkDataUpdate());
    } catch (e) {
      setStatus((s) => ({ ...(s ?? {}), error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setChecking(false);
      setCheckedAt(Date.now());
    }
  }, [enabled]);

  const restart = useCallback(async () => {
    if (enabled) await restartApp();
  }, [enabled]);

  return { status, checking, checkedAt, check, restart };
}
