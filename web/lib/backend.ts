/**
 * Where the engine is, and how to talk to it.
 *
 * One front-end build serves two products:
 *
 *  - **hosted** (the web app): the engine is a public API whose address is
 *    inlined at build time from `NEXT_PUBLIC_API_BASE`.
 *  - **local** (the desktop app): the shell starts an engine on this machine,
 *    on a port it picked at launch, protected by a token it generated for
 *    this session. Neither is known at build time, so the shell injects
 *    `window.__APP_CONFIG__` before any page script runs.
 *
 * Everything that calls the engine goes through here: `API_BASE` for the
 * address, `apiFetch` to attach the session token, `withToken` for the few
 * requests a header cannot ride on (a download started by navigation).
 */

export type BackendMode = "local" | "hosted";

export interface AppConfig {
  mode: BackendMode;
  /** Engine origin, no trailing slash. */
  apiBase: string;
  /** Per-session bearer token (local mode only). */
  token?: string;
  /** Product version, for the About box. */
  version?: string;
}

declare global {
  interface Window {
    __APP_CONFIG__?: AppConfig;
  }
}

/** Resolve the config. Exported for tests; the app uses `BACKEND`. */
export function resolveBackend(
  injected: AppConfig | undefined,
  envBase: string | undefined,
): AppConfig {
  if (injected && typeof injected.apiBase === "string" && injected.apiBase) {
    return {
      mode: injected.mode === "local" ? "local" : "hosted",
      apiBase: injected.apiBase.replace(/\/+$/, ""),
      token: injected.token || undefined,
      version: injected.version,
    };
  }
  return { mode: "hosted", apiBase: (envBase ?? "http://localhost:8000").replace(/\/+$/, "") };
}

export const BACKEND: AppConfig = resolveBackend(
  typeof window !== "undefined" ? window.__APP_CONFIG__ : undefined,
  process.env.NEXT_PUBLIC_API_BASE,
);

export const API_BASE = BACKEND.apiBase;
export const IS_DESKTOP = BACKEND.mode === "local";

/** Headers for an engine request: the caller's, plus the session token. */
export function authHeaders(
  token: string | undefined,
  init?: HeadersInit,
): Headers {
  const headers = new Headers(init);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

/** `fetch` for the engine — identical to `fetch` when there is no token. */
export function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  if (!BACKEND.token) return fetch(input, init);
  return fetch(input, { ...init, headers: authHeaders(BACKEND.token, init?.headers) });
}

/** The same URL carrying the token as a query parameter — only for GETs the
 *  browser makes itself (an iframe or link download), where no header can be
 *  set. The engine accepts `?t=` on GET requests only. */
export function withToken(url: string, token: string | undefined = BACKEND.token): string {
  if (!token) return url;
  return `${url}${url.includes("?") ? "&" : "?"}t=${encodeURIComponent(token)}`;
}
