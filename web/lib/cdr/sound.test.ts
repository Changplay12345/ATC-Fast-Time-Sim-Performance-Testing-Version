import { describe, expect, it } from "vitest";

import { DEFAULT_VOLUME, alertGain, playAlert } from "./sound";

describe("alertGain", () => {
  it("is silent at 0", () => {
    expect(alertGain(0)).toBe(0);
  });

  it("keeps the loudness the app always had at the default", () => {
    // 0.14 was the fixed gain before the volume could be changed.
    expect(alertGain(DEFAULT_VOLUME)).toBeCloseTo(0.14, 5);
  });

  it("rises all the way up the slider, and never past the maximum", () => {
    let last = -1;
    for (let v = 0; v <= 100; v += 5) {
      const g = alertGain(v);
      expect(g).toBeGreaterThan(last);
      last = g;
    }
    expect(alertGain(100)).toBeLessThan(1); // a full-scale sine would clip
    expect(alertGain(100)).toBeGreaterThan(alertGain(DEFAULT_VOLUME));
  });

  it("clamps values a damaged setting could hold", () => {
    expect(alertGain(-20)).toBe(0);
    expect(alertGain(250)).toBe(alertGain(100));
    expect(alertGain(Number.NaN)).toBe(0);
    expect(alertGain(Number.POSITIVE_INFINITY)).toBe(0);
  });
});

describe("playAlert", () => {
  it("does nothing, and does not throw, where there is no audio", () => {
    // No Web Audio here, as on the server render: it must simply do nothing.
    expect(() => playAlert("LOS", 80)).not.toThrow();
    expect(() => playAlert("MTCD", 0)).not.toThrow();
  });
});
