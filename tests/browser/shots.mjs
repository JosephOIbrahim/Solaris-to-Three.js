// Final screenshots + checks on three r187 (MaterialX 1.8.0 override).
// Software GL (SwiftShader) is slow with MaterialX: frames take seconds here, not on a GPU.
//   npm start   (in another terminal)   then   node tests/browser/shots.mjs
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url"; // .pathname breaks on Windows (/C:/...)
const { chromium } = createRequire(import.meta.url)("playwright");

const OUT = fileURLToPath(new URL("../../shots/final/", import.meta.url));
await mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const results = {};

async function open(query, viewport) {
  const page = await browser.newPage({ viewport });
  const errors = new Set(), warnings = new Set();
  page.on("pageerror", (e) => errors.add(e.message.slice(0, 160)));
  page.on("console", (m) => { if (m.type() === "warning" || m.type() === "error") warnings.add(m.text().slice(0, 160)); });
  await page.goto(`http://127.0.0.1:5180/?${query}`);
  await page.waitForFunction(() => window.__report?.ready, null, { timeout: 300000, polling: 2000 });
  await page.evaluate(() => window.__app.hydra.setPlaying(false)); // freeze the ring for comparable frames
  await page.waitForTimeout(6000);
  return { page, errors, warnings };
}
const snap = (page, name) => page.screenshot({ path: `${OUT}${name}.png`, timeout: 240000 });
const state = (page) => page.evaluate(() => ({
  revision: window.__report.revision, timings: window.__report.timings, materials: window.__inventory().materials,
  fallback: window.__report.fallbackMaterials, lastSwitch: window.__report.lastVariantSwitch, jsRoute: window.__report.jsRoute }));

{
  const { page, errors, warnings } = await open("clean", { width: 960, height: 540 });
  await snap(page, "r187-ember");
  for (const v of ["gold", "chrome"]) {
    await page.evaluate((v) => window.__app.selectVariant(v), v);
    await page.waitForTimeout(6000);
    await snap(page, `r187-${v}`);
  }
  await page.evaluate(() => window.__app.setProxy());
  await page.waitForTimeout(6000);
  await snap(page, "r187-proxy-on");
  await page.evaluate(() => window.__app.setProxy());
  await page.evaluate(async () => { await window.__app.loadJsRoute(); window.__app.setRoute("js"); });
  await page.waitForTimeout(4000);
  await snap(page, "r187-usdloader-js");
  results.r187 = { ...(await state(page)), errors: [...errors], warnings: [...warnings] };
  await page.close();
}
{
  const { page } = await open("clean", { width: 390, height: 844 });
  await snap(page, "r187-phone");
  await page.close();
}
await browser.close();
await writeFile(`${OUT}results.json`, JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
