// Same flow as e2e_load.mjs, but records a CPU profile of the page's main
// thread for `secs` seconds starting the moment "Generate all" returns, and
// prints the hottest functions by self time. Run against `next dev` so the
// function names are readable.
// node e2e_profile.mjs <csv> [url] [secs]
import { chromium } from "playwright-core";
import { writeFileSync } from "node:fs";
const [csv, url = "http://127.0.0.1:3000", secsArg = "45"] = process.argv.slice(2);
const secs = Number(secsArg);
const browser = await chromium.launch({ channel: "msedge", headless: false });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto(url, { waitUntil: "load", timeout: 300000 });
await page.waitForSelector('input[type="file"]', { state: "attached", timeout: 300000 });
await page.waitForTimeout(3000);
let t0 = Date.now();
await page.setInputFiles('input[type="file"]', csv);
await page.waitForFunction(() => document.body.innerText.includes("flights into tabs"), null, { timeout: 300000 });
console.log("import note after", Date.now() - t0, "ms");
await page.waitForTimeout(3000);
const cdp = await page.context().newCDPSession(page);
await cdp.send("Profiler.enable");
await cdp.send("Profiler.setSamplingInterval", { interval: 1000 });
t0 = Date.now();
await page.getByRole("button", { name: /Generate all/ }).click();
await page.waitForFunction(() => { const b = [...document.querySelectorAll("button")].find((x) => /Generate all/.test(x.textContent || "")); return b && !b.disabled; }, null, { timeout: 1200000, polling: 500 });
console.log("generate returned after", Date.now() - t0, "ms; profiling main thread for", secs, "s");
await cdp.send("Profiler.start");
await new Promise((r) => setTimeout(r, secs * 1000));
const { profile } = await cdp.send("Profiler.stop");
writeFileSync("main_profile.cpuprofile", JSON.stringify(profile));
// self time per node from timeDeltas
const byId = new Map(profile.nodes.map((n) => [n.id, n]));
const self = new Map();
for (let i = 0; i < profile.samples.length; i++) {
  const id = profile.samples[i]; const dt = profile.timeDeltas[i] || 0;
  self.set(id, (self.get(id) || 0) + dt);
}
const total = [...self.values()].reduce((a, b) => a + b, 0) / 1000;
// group by function + file
const agg = new Map();
for (const [id, us] of self) {
  const n = byId.get(id); const f = n.callFrame;
  const file = (f.url || "").split("/").slice(-2).join("/").split("?")[0];
  const key = `${f.functionName || "(anon)"}  ${file}:${f.lineNumber + 1}`;
  agg.set(key, (agg.get(key) || 0) + us / 1000);
}
// inclusive time per function name (sum over stacks that contain it), to see owners
const parent = new Map(); for (const n of profile.nodes) for (const c of n.children || []) parent.set(c, n.id);
const incl = new Map();
for (const [id, us] of self) {
  const seen = new Set(); let cur = id;
  while (cur !== undefined) { const n = byId.get(cur); const f = n.callFrame; const file = (f.url || "").split("/").slice(-1)[0].split("?")[0];
    const key = `${f.functionName || "(anon)"}  ${file}`; if (!seen.has(key)) { seen.add(key); incl.set(key, (incl.get(key) || 0) + us / 1000); } cur = parent.get(cur); }
}
console.log(`profiled ${total.toFixed(0)} ms of samples over ${secs} s`);
console.log("--- top self time (ms) ---");
for (const [k, v] of [...agg].sort((a, b) => b[1] - a[1]).slice(0, 30)) console.log(v.toFixed(0).padStart(7), k);
console.log("--- top inclusive time (ms), app code only ---");
for (const [k, v] of [...incl].filter(([k]) => /components|lib|app\/|\.tsx|\.ts/.test(k) && !/node_modules/.test(k)).sort((a, b) => b[1] - a[1]).slice(0, 35)) console.log(v.toFixed(0).padStart(7), k);
await browser.close();
