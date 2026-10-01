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
  /** The version on offer, when there is one. */
  version?: string;
  notes?: string;
  date?: string;
}

export const appInfo = () => tauri().core.invoke<AppInfo>("app_info");
export const openLogsFolder = () => tauri().core.invoke<void>("open_logs_folder");
export const openExportsFolder = () => tauri().core.invoke<void>("open_exports_folder");
export const checkUpdate = () => tauri().core.invoke<UpdateCheck>("check_update");
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
  version?: string;
  notes?: string;
  /** 0–1 while downloading; null when the size is unknown. */
  progress: number | null;
  error?: string;
  /** When the last check finished. */
  checkedAt: number | null;
  check: () => Promise<void>;
  install: () => Promise<void>;
}

/** First check a few seconds after launch, so it never competes with start-up. */
const FIRST_CHECK_MS = 8_000;
/** Then, like most desktop software, a few times a day while the app is open. */
const RECHECK_MS = 4 * 60 * 60 * 1000;

/**
 * Background update check for the desktop app. It only ever *reports* an
 * update — downloading and installing happen when the user asks, so a running
 * simulation is never interrupted.
 */
export function useDesktopUpdate(enabled: boolean): DesktopUpdate {
  const [state, setState] = useState<Omit<DesktopUpdate, "check" | "install">>({
    status: "idle",
    progress: null,
    checkedAt: null,
  });
  const busy = useRef(false);

  const check = useCallback(async () => {
    if (!enabled || busy.current) return;
    busy.current = true;
    setState((s) => ({ ...s, status: "checking", error: undefined }));
    try {
      const r = await checkUpdate();
      setState({
        status: r.available ? "available" : "none",
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
    const first = window.setTimeout(check, FIRST_CHECK_MS);
    const every = window.setInterval(check, RECHECK_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(every);
    };
  }, [enabled, check]);

  return { ...state, check, install };
}
