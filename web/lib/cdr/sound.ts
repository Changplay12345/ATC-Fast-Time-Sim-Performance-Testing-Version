/**
 * Conflict alert tones — synthesised with the Web Audio API so no audio assets
 * ship with the app. Each severity gets a distinct, escalating signature:
 *   LOS  — three urgent high beeps
 *   STCA — two mid beeps
 *   MTCD — one soft low beep
 *
 * The AudioContext is created lazily (browsers block it until a user gesture,
 * which the Play button provides) and reused. All of this is a no-op on the
 * server and degrades silently if Web Audio is unavailable or the volume is 0.
 */

import type { Severity } from "./types";

let ctx: AudioContext | null = null;

function audioCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (ctx) return ctx;
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext;
  if (!Ctor) return null;
  try {
    ctx = new Ctor();
  } catch {
    return null;
  }
  return ctx;
}

/** The volume a fresh install starts at, 0–100. Sounds exactly as loud as the
 *  app did before the volume could be changed. */
export const DEFAULT_VOLUME = 50;

/** Peak gain at 100 %. Four times the old fixed level (+12 dB): room to be
 *  heard over a busy ops room, still well short of clipping a single sine. */
const MAX_GAIN = 0.56;

/**
 * Slider position (0–100) → oscillator gain.
 *
 * Squared, because loudness is not linear in amplitude: with a straight line
 * the top half of the slider would all sound "loud" and everything useful
 * would be crammed into the bottom. 0 is silence; 50 is the level the app
 * always had (0.14); out-of-range and non-numeric values are clamped.
 */
export function alertGain(volume: number): number {
  if (!Number.isFinite(volume)) return 0;
  const v = Math.min(100, Math.max(0, volume)) / 100;
  return MAX_GAIN * v * v;
}

/** One short sine blip at `freq` Hz starting `at` seconds from now. */
function blip(c: AudioContext, freq: number, at: number, dur: number, gain: number) {
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = "sine";
  osc.frequency.value = freq;
  const t0 = c.currentTime + at;
  // Short attack/decay envelope so beeps don't click.
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(gain, t0 + 0.01);
  g.gain.linearRampToValueAtTime(0, t0 + dur);
  osc.connect(g).connect(c.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

/** Per-severity tone signature: [frequency Hz, beep count, gap seconds]. */
const TONES: Record<Severity, { freq: number; beeps: number; gap: number }> = {
  LOS: { freq: 1000, beeps: 3, gap: 0.16 },
  STCA: { freq: 760, beeps: 2, gap: 0.18 },
  MTCD: { freq: 520, beeps: 1, gap: 0 },
};

/**
 * Play the alert tone for a severity at `volume` (0–100; the caller passes 0
 * when muted). Silent at 0; safe to call anywhere.
 */
export function playAlert(severity: Severity, volume: number = DEFAULT_VOLUME): void {
  const gain = alertGain(volume);
  if (gain <= 0) return;
  const c = audioCtx();
  if (!c) return;
  if (c.state === "suspended") void c.resume();
  const { freq, beeps, gap } = TONES[severity];
  const dur = 0.11;
  for (let i = 0; i < beeps; i++) blip(c, freq, i * (dur + gap), dur, gain);
}
