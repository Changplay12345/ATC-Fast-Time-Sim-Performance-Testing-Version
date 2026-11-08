// More GitHub pictures for the management briefing deck:
//   repo home, commit list, one commit (Claude named as co-author), a release
//   build run's job graph, the releases list, and the performance notes as
//   GitHub renders them.
// Run from a folder that has playwright-core in node_modules:
//   node gh_more_shots.mjs <out_dir> <run_id> <commit_sha>
import { chromium } from "playwright-core";
const [OUT, RUN, SHA] = process.argv.slice(2);
const REPO = "https://github.com/Changplay12345/ATC-Fast-Time-Sim-Performance-Testing-Version";
const browser = await chromium.launch({ channel: "msedge", headless: false });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
const shots = [
  ["github_home", `${REPO}`],
  ["github_commits", `${REPO}/commits/main`],
  ["github_commit", `${REPO}/commit/${SHA}`],
  ["github_run", `${REPO}/actions/runs/${RUN}`],
  ["github_releases_list", `${REPO}/releases`],
  ["github_notes", `${REPO}/blob/main/PERFORMANCE_NOTES.md`],
];
for (const [name, url] of shots) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    const p = await ctx.newPage();
    try {
      await p.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
      await p.waitForTimeout(4000);
      const head = (await p.title()) + (await p.evaluate(() => document.body.innerText.slice(0, 300)));
      if (/504|time-out|unavailable|Unicorn/i.test(head)) throw new Error("gateway");
      if (name === "github_commit") {
        // expand the full commit message so the co-author line shows
        const btn = p.locator('button[aria-label*="Show description"], button.ellipsis-expander, button[aria-label*="Toggle commit message"]').first();
        if (await btn.count()) { await btn.click({ timeout: 3000 }).catch(() => {}); await p.waitForTimeout(800); }
      }
      await p.screenshot({ path: `${OUT}/${name}.png` });
      console.log("saved", name, "attempt", attempt);
      await p.close();
      break;
    } catch (e) {
      console.log(name, "attempt", attempt, "failed", String(e).slice(0, 80));
      await p.close();
      await new Promise((r) => setTimeout(r, 5000));
    }
  }
}
await browser.close();
