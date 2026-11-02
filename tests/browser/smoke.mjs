// Headless smoke test: open the viewer in Chromium, exercise each feature, screenshot.
//   npm start   (in another terminal)   then   npm run test:browser
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url"; // .pathname breaks on Windows (/C:/...)
const { chromium } = createRequire(import.meta.url)("playwright"); // a local or global Playwright with Chromium installed

const THREE = process.env.THREE || "187";
const BASE = `${process.env.BASE || "http://127.0.0.1:5180/"}?three=${THREE}`;
const OUT = fileURLToPath(new URL(`../../shots/${THREE}/`, import.meta.url));
await mkdir(OUT, { recursive: true });

const browser = await chromium.launch({
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const logs = [];
async function open(viewport) {
  const page = await browser.newPage({ viewport });
  page.on("console", (m) => logs.push(`[${m.type()}] ${m.text()}`.slice(0, 400)));
  page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`.slice(0, 400)));
  await page.goto(BASE);
  await page.waitForFunction(() => window.__report?.ready || window.__report?.errors?.length, null, { timeout: 240000 });
  await page.waitForTimeout(2500);
  return page;
}
// Software GL recompiles MaterialX shaders on the CPU after a variant switch, which can block a frame
// for well over Playwright's 30 s default on a 2-core machine (CI runners included).
const shot = (page, name) => page.screenshot({ path: `${OUT}${name}.png`, timeout: 240000 });

const page = await open({ width: 1280, height: 720 });
await shot(page, "01-hydra-ember");
for (const v of ["gold", "chrome"]) {
  await page.evaluate((v) => window.__app.selectVariant(v), v);
  await page.waitForTimeout(1500);
  await shot(page, `02-hydra-${v}`);
}
await page.evaluate(() => window.__app.selectVariant("ember"));
await page.evaluate(() => window.__app.setProxy());
await page.waitForTimeout(1500);
await shot(page, "03-hydra-proxy-on");
await page.evaluate(() => window.__app.setProxy());
await page.evaluate(() => window.__app.setCamMode(false));
await page.waitForTimeout(800);
await shot(page, "04-hydra-orbit");
await page.evaluate(() => window.__app.setCamMode(true));
await page.evaluate(async () => { await window.__app.loadJsRoute(); window.__app.setRoute("js"); });
await page.waitForTimeout(1500);
await shot(page, "05-usdloader-js");
const report = await page.evaluate(() => ({ ...window.__report, inventoryNow: window.__inventory() }));
await page.close();

const phone = await open({ width: 390, height: 844 });
await shot(phone, "06-phone-usd-cam");
await phone.close();
await browser.close();

await writeFile(`${OUT}report.json`, JSON.stringify({ report, logs }, null, 2));
console.log(JSON.stringify(report, null, 2));
console.log("---- console (warnings/errors, deduped) ----");
console.log([...new Set(logs.filter((l) => /\[(warning|error|pageerror)\]/.test(l)))].slice(0, 40).join("\n"));

// Pass/fail: what has to hold on the fixture stage for the pipeline to count as working.
const checks = [
  ["page is cross-origin isolated (COOP/COEP)", report.isolated === true],
  ["stage composed and drew", report.ready === true],
  ["no page errors", report.errors.length === 0],
  ["every bound material reached a real shader", report.fallbackMaterials.length === 0],
  ["USD camera found", !!report.usdCamera],
  ["dome light became the environment", !!report.env],
  ["variant switch applied", report.lastVariantSwitch?.option === "ember"],
  ["USDLoader route loaded meshes", report.jsRoute?.meshes > 0],
];
console.log("---- checks ----");
for (const [name, ok] of checks) console.log(`${ok ? "ok  " : "FAIL"}  ${name}`);
if (checks.some(([, ok]) => !ok)) process.exitCode = 1;
