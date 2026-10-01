/**
 * The web build and the desktop build are the same code; which engine they
 * talk to, and whether a session token goes along, is decided here.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { authHeaders, resolveBackend, withToken } from "./backend";

describe("resolveBackend", () => {
  it("is hosted, from the build-time address, when nothing is injected", () => {
    const b = resolveBackend(undefined, "https://api.example.com/");
    expect(b).toEqual({ mode: "hosted", apiBase: "https://api.example.com" });
  });

  it("falls back to the local dev server with no env either", () => {
    expect(resolveBackend(undefined, undefined).apiBase).toBe("http://localhost:8000");
  });

  it("takes the shell's injected config over the build-time address", () => {
    const b = resolveBackend(
      { mode: "local", apiBase: "http://127.0.0.1:51234/", token: "abc", version: "0.2.0" },
      "https://api.example.com",
    );
    expect(b).toEqual({
      mode: "local",
      apiBase: "http://127.0.0.1:51234",
      token: "abc",
      version: "0.2.0",
    });
  });

  it("ignores an injected config with no address", () => {
    const b = resolveBackend({ mode: "local", apiBase: "" }, "https://api.example.com");
    expect(b.mode).toBe("hosted");
    expect(b.apiBase).toBe("https://api.example.com");
  });

  it("treats an unknown mode as hosted", () => {
    const b = resolveBackend(
      { mode: "weird" as never, apiBase: "http://127.0.0.1:1" },
      undefined,
    );
    expect(b.mode).toBe("hosted");
  });
});

describe("authHeaders", () => {
  it("adds the bearer token and keeps the caller's headers", () => {
    const h = authHeaders("tok", { "Content-Type": "application/json" });
    expect(h.get("Authorization")).toBe("Bearer tok");
    expect(h.get("Content-Type")).toBe("application/json");
  });

  it("adds nothing without a token", () => {
    expect(authHeaders(undefined, { A: "1" }).has("Authorization")).toBe(false);
  });
});

describe("withToken", () => {
  it("leaves the URL alone without a token", () => {
    expect(withToken("http://x/api/download/a.csv", undefined)).toBe("http://x/api/download/a.csv");
  });

  it("appends the token, respecting an existing query", () => {
    expect(withToken("http://x/a.csv", "t k")).toBe("http://x/a.csv?t=t%20k");
    expect(withToken("http://x/a?b=1", "tk")).toBe("http://x/a?b=1&t=tk");
  });
});

describe("every engine call carries the session token", () => {
  // The desktop engine answers 401 to a request without the token. A bare
  // `fetch(` in these modules is a call the desktop app would silently lose
  // (it was: five procedure lookups, written across two lines, were missed).
  const files = ["lib/api.ts", "lib/cat62.ts", "components/DownloadModal.tsx"];
  for (const f of files) {
    it(`${f} has no bare fetch()`, () => {
      const src = readFileSync(resolve(__dirname, "..", f), "utf8");
      const bare = src.match(/(?<![A-Za-z.])fetch\(/g) ?? [];
      expect(bare).toEqual([]);
    });
  }
});
