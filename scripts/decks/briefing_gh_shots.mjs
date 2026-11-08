// Screenshots of the project on GitHub for the management briefing (release page; the build-run list was taken by hand from actions/workflows/desktop.yml). Run from a folder that has playwright-core in node_modules: node scripts/decks/briefing_gh_shots.mjs <out_dir>
import { chromium } from "playwright-core";
const OUT = process.argv[2];
const REPO = "https://github.com/Changplay12345/ATC-Fast-Time-Sim-Performance-Testing-Version";
const browser = await chromium.launch({ channel: "msedge", headless: false });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
for (const [name, url] of [["github_releases", `${REPO}/releases/tag/v0.4.4`]]) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    const p = await ctx.newPage();
    try { await p.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }); await p.waitForTimeout(4000);
      const title = await p.title(); if (/504|time-out|unavailable/i.test(title + (await p.evaluate(() => document.body.innerText.slice(0, 200))))) throw new Error("gateway");
      await p.screenshot({ path: `${OUT}/${name}.png` }); console.log("saved", name, "attempt", attempt); await p.close(); break; }
    catch (e) { console.log(name, "attempt", attempt, "failed", String(e).slice(0, 60)); await p.close(); await new Promise(r => setTimeout(r, 5000)); }
  }
}
await browser.close();
