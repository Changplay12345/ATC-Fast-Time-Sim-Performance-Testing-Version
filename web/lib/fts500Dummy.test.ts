/**
 * dummy_data/fts_500_flights_20260927.csv (scripts/make_fts500_flights.py)
 * must import through the generator panel's own parser: every row becomes a
 * complete plan tab.
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { parseFlightFile } from "./flightFile";

const PATH = resolve(__dirname, "../../dummy_data/fts_500_flights_20260927.csv");

describe.skipIf(!existsSync(PATH))("fts_500_flights_20260927.csv import", () => {
  it("parses 500+ complete, unique flight plans", async () => {
    const text = readFileSync(PATH, "utf-8");
    const recs = await parseFlightFile(new File([text], "fts_500_flights_20260927.csv"));
    expect(recs.length).toBeGreaterThanOrEqual(500);
    for (const r of recs) {
      expect(r.callsign).toMatch(/^[A-Z]{3}\d+$/);
      expect(r.actype).toBeTruthy();
      expect(r.adep).toMatch(/^[A-Z]{4}$/);
      expect(r.ades).toMatch(/^[A-Z]{4}$/);
      expect(r.eobt).toMatch(/^2026-09-27T\d{2}:\d{2}$/);
      expect(r.rfl).toBeGreaterThan(0);
      expect(r.gsKt).toBeGreaterThan(0);
      expect(r.route).toBeTruthy();
      expect(r.trajectory).toBeUndefined();
    }
    expect(new Set(recs.map((r) => r.callsign)).size).toBe(recs.length);
  });
});
