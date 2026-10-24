// Import -> (optionally) Generate all, measuring main-thread cost with the
// Long Tasks API (immune to rAF throttling), heap, and foreground frame rate.
// node e2e_longtask.mjs <csv> [url] [generate=1|0] [watchSecs=60]
import { chromium } from "playwright-core";
const [csv, url = "http://127.0.0.1:3000", gen = "1", watchArg = "60"] = process.argv.slice(2);
const watch = Number(watchArg);
const browser = await chromium.launch({ channel: "msedge", headless: false, args: ["--enable-precise-memory-info"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const reqs = []; const logs = [];
page.on("console", (m) => { if (["error","warning"].includes(m.type())) logs.push(m.type() + ": " + m.text().slice(0, 200)); });
page.on("pageerror", (e) => logs.push("pageerror: " + String(e).slice(0, 200)));
page.on("response", async (res) => { if (res.url().includes("/api/generate_batch")) { let b = 0; try { b = (await res.body()).length; } catch {} reqs.push({ t: Date.now(), bytes: b }); } });
await page.goto(url, { waitUntil: "load", timeout: 300000 });
await page.waitForSelector('input[type="file"]', { state: "attached", timeout: 300000 });
await page.bringToFront();
await page.evaluate(() => { window.__lt = { ms: 0, n: 0, max: 0 }; new PerformanceObserver((l) => { for (const e of l.getEntries()) { window.__lt.ms += e.duration; window.__lt.n++; window.__lt.max = Math.max(window.__lt.max, e.duration); } }).observe({ type: "longtask", buffered: true }); });
await page.waitForTimeout(2500);
const lt = () => page.evaluate(() => ({ ...window.__lt }));
const heap = () => page.evaluate(() => Math.round(performance.memory.usedJSHeapSize / 1048576));
const fps = (s = 4) => page.evaluate((s) => new Promise((ok) => { let n = 0; const t0 = performance.now(); const f = () => { n++; if (performance.now() - t0 < s * 1000) requestAnimationFrame(f); else ok(Math.round(n / s)); }; requestAnimationFrame(f); }), s);
const reset = () => page.evaluate(() => { window.__lt = { ms: 0, n: 0, max: 0 }; });
const out = { url, csv: csv.split(/[\\/]/).pop() };

await reset(); let t0 = Date.now();
await page.setInputFiles('input[type="file"]', csv);
await page.waitForFunction(() => document.body.innerText.includes("flights into tabs"), null, { timeout: 600000 });
out.importNoteMs = Date.now() - t0;
await page.waitForTimeout(8000);
out.importLongTasks8s = await lt(); out.heapAfterImportMB = await heap(); out.fpsAfterImport = await fps();
out.flights = Number((await page.evaluate(() => document.body.innerText)).match(/Imported (\d+) flights/)?.[1]);

if (gen === "1") {
  await reset(); t0 = Date.now();
  await page.getByRole("button", { name: /Generate all/ }).click();
  await page.waitForFunction(() => { const b = [...document.querySelectorAll("button")].find((x) => /Generate all/.test(x.textContent || "")); return b && !b.disabled; }, null, { timeout: 1200000, polling: 500 });
  out.generateWallMs = Date.now() - t0;
  out.generateLongTasks = await lt(); out.heapAfterGenerateMB = await heap();
  out.chunks = reqs.length; out.bytesMB = Math.round(reqs.reduce((a, r) => a + r.bytes, 0) / 1e6);
  // watch the aftermath in slices so the shape over time is visible
  out.after = [];
  for (let i = 0; i < watch / 10; i++) { await reset(); const f = await fps(10); const l = await lt(); out.after.push({ secs: (i + 1) * 10, fps: f, longTaskMs: Math.round(l.ms), tasks: l.n, maxTaskMs: Math.round(l.max), heapMB: await heap() }); }
}
out.consoleProblems = logs.slice(0, 12);
console.log(JSON.stringify(out, null, 1));
await browser.close();
