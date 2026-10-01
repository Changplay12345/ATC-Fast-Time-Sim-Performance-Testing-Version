"use client";

/**
 * FpsMeter — an on-screen frame-rate overlay in the spirit of the MSI
 * Afterburner / RivaTuner OSD: the live frame rate, frame time, 1 % low, and a
 * rolling graph of the frame rate over the last 30 seconds.
 *
 * It measures the page's own requestAnimationFrame cadence, i.e. what the map
 * actually delivers. It is kept out of React's render path on purpose — its own
 * rAF loop writes straight to a canvas and a few text nodes — so watching the
 * frame rate does not cost frame rate, and it keeps counting while the rest of
 * the app is busy (a stall shows up as a dip, not as a frozen meter).
 */

import { useEffect, useRef } from "react";

/** Graph sample period and length: 10 samples/s over the last 30 s. */
const SAMPLE_MS = 100;
const SAMPLES = 300;
/** Frame times kept for the 1 % low (≈ 10 s at 60 fps). */
const FRAME_WINDOW = 600;

const GOOD = "#22c55e";
const OK = "#eab308";
const BAD = "#ef4444";
const tone = (fps: number) => (fps >= 55 ? GOOD : fps >= 30 ? OK : BAD);

export default function FpsMeter() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fpsRef = useRef<HTMLSpanElement>(null);
  const msRef = useRef<HTMLSpanElement>(null);
  const lowRef = useRef<HTMLSpanElement>(null);
  const statsRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const W = canvas.clientWidth;
    const H = canvas.clientHeight;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.scale(dpr, dpr);

    const samples: number[] = [];
    const frameTimes = new Float32Array(FRAME_WINDOW);
    let ftCount = 0;
    let ftHead = 0;
    let frames = 0;
    let last = performance.now();
    let windowStart = last;
    let raf = 0;

    const draw = () => {
      ctx.clearRect(0, 0, W, H);
      if (samples.length < 2) return;
      const max = Math.max(60, ...samples);
      const top = Math.ceil(max / 30) * 30; // scale in 30-fps steps
      const x = (i: number) => (i / (SAMPLES - 1)) * W;
      const y = (v: number) => H - (Math.min(v, top) / top) * (H - 2) - 1;
      const off = SAMPLES - samples.length;

      // Reference lines at 30 / 60 (and 120 / 240 when the scale reaches them).
      ctx.lineWidth = 1;
      ctx.font = "9px ui-monospace, monospace";
      for (const ref of [30, 60, 120, 240]) {
        if (ref >= top) continue;
        const yy = Math.round(y(ref)) + 0.5;
        ctx.strokeStyle = "rgba(148,163,184,0.22)";
        ctx.beginPath();
        ctx.moveTo(0, yy);
        ctx.lineTo(W, yy);
        ctx.stroke();
        ctx.fillStyle = "rgba(148,163,184,0.6)";
        ctx.fillText(String(ref), 2, yy - 2);
      }

      const cur = samples[samples.length - 1];
      const color = tone(cur);
      // Filled area under the line.
      ctx.beginPath();
      ctx.moveTo(x(off), H);
      samples.forEach((v, i) => ctx.lineTo(x(off + i), y(v)));
      ctx.lineTo(x(off + samples.length - 1), H);
      ctx.closePath();
      ctx.fillStyle = color + "33";
      ctx.fill();
      // The line.
      ctx.beginPath();
      samples.forEach((v, i) =>
        i === 0 ? ctx.moveTo(x(off + i), y(v)) : ctx.lineTo(x(off + i), y(v)),
      );
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    };

    const onePercentLow = () => {
      if (ftCount < 10) return null;
      const arr = Array.from(frameTimes.subarray(0, ftCount)).sort((a, b) => b - a);
      const worst = arr.slice(0, Math.max(1, Math.floor(ftCount / 100)));
      const avg = worst.reduce((s, v) => s + v, 0) / worst.length;
      return 1000 / avg;
    };

    let textTick = 0;
    const tick = (now: number) => {
      const dt = now - last;
      last = now;
      frames++;
      frameTimes[ftHead] = dt;
      ftHead = (ftHead + 1) % FRAME_WINDOW;
      ftCount = Math.min(ftCount + 1, FRAME_WINDOW);

      if (now - windowStart >= SAMPLE_MS) {
        const fps = (frames * 1000) / (now - windowStart);
        samples.push(fps);
        if (samples.length > SAMPLES) samples.shift();
        frames = 0;
        windowStart = now;
        draw();

        // Text a few times a second — faster is unreadable.
        if (++textTick % 3 === 0) {
          const recent = samples.slice(-5);
          const avgFps = recent.reduce((s, v) => s + v, 0) / recent.length;
          if (fpsRef.current) {
            fpsRef.current.textContent = String(Math.round(avgFps));
            fpsRef.current.style.color = tone(avgFps);
          }
          if (msRef.current) msRef.current.textContent = (1000 / avgFps).toFixed(1);
          const low = onePercentLow();
          if (lowRef.current) {
            lowRef.current.textContent = low == null ? "–" : String(Math.round(low));
            lowRef.current.style.color = low == null ? "" : tone(low);
          }
          if (statsRef.current) {
            let mn = Infinity;
            let mx = 0;
            let sum = 0;
            for (const v of samples) {
              mn = Math.min(mn, v);
              mx = Math.max(mx, v);
              sum += v;
            }
            statsRef.current.textContent = `min ${Math.round(mn)} · avg ${Math.round(
              sum / samples.length,
            )} · max ${Math.round(mx)}`;
          }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="fps-meter" role="status" aria-label="Frame rate">
      <div className="fps-meter-head">
        <span className="fps-meter-big">
          <span ref={fpsRef}>–</span>
          <small>FPS</small>
        </span>
        <span className="fps-meter-side">
          <span>
            <span ref={msRef}>–</span> ms
          </span>
          <span>
            1% low <span ref={lowRef}>–</span>
          </span>
        </span>
      </div>
      <canvas ref={canvasRef} className="fps-meter-graph" />
      <span ref={statsRef} className="fps-meter-stats">
        –
      </span>
    </div>
  );
}
