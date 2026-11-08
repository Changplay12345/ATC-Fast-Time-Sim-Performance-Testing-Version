// Screenshots for the management briefing: the app with a day of traffic,
// the Server load panel, and the project's GitHub releases + build runs.
// Run from a folder that has playwright-core in node_modules.
import { chromium } from "playwright-core";
const OUT = process.argv[2];
const CSV = "D:\\ATC\\ATC-Fast-Time-Simulation-Tool-Trajectory-Generator-main\\dummy_data\\2000 flight.csv";
const REPO = "https://github.com/Changplay12345/ATC-Fast-Time-Sim-Performance-Testing-Version";
const browser = await chromium.launch({ channel: "msedge", headless: false });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });

// GitHub pages first (no servers needed)
for (const [name, url] of [["github_releases", `${REPO}/releases`], ["github_actions", `${REPO}/actions`]]) {
  const p = await ctx.newPage();
  try { await p.goto(url, { waitUntil: "networkidle", timeout: 60000 }); await p.waitForTimeout(1500); await p.screenshot({ path: `${OUT}/${name}.png` }); console.log("saved", name); }
  catch (e) { console.log(name, "failed", String(e).slice(0, 80)); }
  await p.close();
}

// the app
const page = await ctx.newPage();
await page.goto("http://127.0.0.1:3000", { waitUntil: "load", timeout: 120000 });
await page.waitForSelector('input[type="file"]', { state: "attached", timeout: 120000 });
await page.bringToFront();
await page.waitForTimeout(2500);
await page.setInputFiles('input[type="file"]', CSV);
await page.waitForFunction(() => document.body.innerText.includes("flights into tabs"), null, { timeout: 120000 });
await page.getByRole("button", { name: /Generate all/ }).click();
await page.waitForFunction(() => { const b = [...document.querySelectorAll("button")].find((x) => /Generate all/.test(x.textContent || "")); return b && !b.disabled; }, null, { timeout: 600000, polling: 500 });
await page.waitForTimeout(12000);
await page.screenshot({ path: `${OUT}/app_traffic.png` }); console.log("saved app_traffic");
// try to open the Server load panel through the Tool menu
try {
  const tool = page.getByRole("button", { name: /^Tool/ }).first();
  await tool.click({ timeout: 5000 });
  await page.getByText(/Server load/).first().click({ timeout: 5000 });
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${OUT}/app_serverload.png` }); console.log("saved app_serverload");
} catch (e) { console.log("server load panel not captured:", String(e).slice(0, 80)); }
await browser.close();
