// Renders an HTML file to an A4 PDF with Edge (headless), plus a PNG preview
// of the printed layout. Usage: node make_pdf.mjs <in.html> <out.pdf> <preview.png>
import { chromium } from "playwright-core";
import { pathToFileURL } from "node:url";
const [html, pdf, png] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage();
await page.goto(pathToFileURL(html).href, { waitUntil: "load" });
await page.emulateMedia({ media: "print" });
await page.pdf({ path: pdf, format: "A4", printBackground: true, preferCSSPageSize: true });
// Preview: the page at A4 proportions (794 x 1123 px at 96 dpi), print styles on.
await page.setViewportSize({ width: 794, height: 1123 });
await page.addStyleTag({ content: "body{padding:68px 68px 60px}" });
await page.screenshot({ path: png, fullPage: true });
const h = await page.evaluate(() => document.documentElement.scrollHeight);
console.log(`pdf written: ${pdf}; printed height ≈ ${(h / 1123).toFixed(2)} A4 pages`);
await browser.close();
