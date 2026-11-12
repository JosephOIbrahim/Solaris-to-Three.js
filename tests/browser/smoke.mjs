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

// Dome orientation: find the sun of fixtures/stage/env/sky.hdr where USD says it is.
// The sky's writer (tools/make_stage.py) puts the sun at texel u = atan2(z, x) / 2pi, row = acos(y) / pi
// of SUN below. USD reads a latlong image with the OpenEXR convention (UsdLux DomeLight docs): latitude
// +pi/2 on the top row, longitude +pi at the left edge falling to -pi at the right, longitude 0 = +Z,
// longitude pi/2 = +X. Then the dome's rotateY places it in the world.
const DOME_ROTATE_Y = 75; // must match dome.AddRotateYOp().Set(...) in tools/make_stage.py
const sun = (() => {
  const [x, y, z] = [-0.55, 0.62, 0.56], n = Math.hypot(x, y, z);
  const u = ((Math.atan2(z / n, x / n) + 2 * Math.PI) % (2 * Math.PI)) / (2 * Math.PI);
  const lat = Math.PI / 2 - Math.acos(y / n), lon = Math.PI - 2 * Math.PI * u;
  const local = [Math.cos(lat) * Math.sin(lon), Math.sin(lat), Math.cos(lat) * Math.cos(lon)];
  const a = (DOME_ROTATE_Y * Math.PI) / 180, [lx, ly, lz] = local;
  const world = [lx * Math.cos(a) + lz * Math.sin(a), ly, -lx * Math.sin(a) + lz * Math.cos(a)];
  // Where the viewer drew the sun before this fix: three's own equirect frame, dome xform ignored.
  const alpha = 2 * Math.PI * (u - 0.5);
  const old = [Math.cos(lat) * Math.cos(alpha), Math.sin(lat), Math.cos(lat) * Math.sin(alpha)];
  return { world, unrotated: local, old };
})();
const sunProbe = await page.evaluate((s) => ({
  world: window.__app.probeEnv(s.world), unrotated: window.__app.probeEnv(s.unrotated), old: window.__app.probeEnv(s.old),
}), sun);
for (const v of ["gold", "chrome"]) {
  await page.evaluate((v) => window.__app.selectVariant(v), v);
  await page.waitForTimeout(1500);
  await shot(page, `02-hydra-${v}`);
}
await page.evaluate(() => window.__app.selectVariant("ember"));
const meshes = () => page.evaluate(() => window.__inventory().meshes);
const meshesBefore = await meshes();
await page.evaluate(() => window.__app.setProxy());
await page.waitForTimeout(1500);
const meshesProxyOn = await meshes();
await shot(page, "03-hydra-proxy-on");
await page.evaluate(() => window.__app.setProxy());
await page.waitForTimeout(1500);
const meshesProxyOff = await meshes();
await page.evaluate(() => window.__app.setCamMode(false));
await page.waitForTimeout(800);
await shot(page, "04-hydra-orbit");
await page.evaluate(() => window.__app.setCamMode(true));
await page.evaluate(async () => { await window.__app.loadJsRoute(); window.__app.setRoute("js"); });
await page.waitForTimeout(1500);
await shot(page, "05-usdloader-js");
const report = await page.evaluate(() => ({ ...window.__report, inventoryNow: window.__inventory(),
  pageTitle: document.title, hudHeading: document.querySelector("#hud h1")?.textContent }));
await page.close();

const phone = await open({ width: 390, height: 844 });
await shot(phone, "06-phone-usd-cam");
await phone.close();

// /WebSettings: a root layer that sublayers shot.usda and authors web:title and web:showHud = false.
const WS_TITLE = "WebSettings Fixture"; // must match TITLE in tools/make_websettings_stage.py
const ORIGIN = new URL(BASE).origin;
const wsPage = await browser.newPage({ viewport: { width: 1280, height: 720 } });
wsPage.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`.slice(0, 400)));
await wsPage.goto(`${BASE}&stage=/fixtures/stage/shot_websettings.usda`);
await wsPage.waitForFunction(() => window.__report?.ready || window.__report?.errors?.length, null, { timeout: 240000 });
const ws = await wsPage.evaluate(() => ({
  title: document.title, hudDisplay: getComputedStyle(document.getElementById("hud")).display,
  report: window.__report.webSettings, errors: window.__report.errors, env: !!window.__report.env,
}));
await shot(wsPage, "07-websettings");
await wsPage.close();

// The glTF player: the exporter's own fixture, loaded through three's GLTFLoader (MIT).
const gltfPage = await browser.newPage({ viewport: { width: 1280, height: 720 } });
gltfPage.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`.slice(0, 400)));
await gltfPage.goto(`${ORIGIN}/players/gltf.html?three=${THREE}&model=/fixtures/stage-flat/hero.glb`);
await gltfPage.waitForFunction(() => window.__gltf?.ready || window.__gltf?.errors?.length, null, { timeout: 120000 });
await gltfPage.waitForTimeout(800);
const gltf = await gltfPage.evaluate(() => ({ ...window.__gltf }));
await shot(gltfPage, "08-gltf-player");
await gltfPage.close();

// The X3D player: X_ITE (MIT) renders the exported fixture; the page also counts the file it
// renders, because X_ITE exposes no node statistics.
const x3dPage = await browser.newPage({ viewport: { width: 1280, height: 720 } });
x3dPage.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`.slice(0, 400)));
await x3dPage.goto(`${ORIGIN}/players/x3d.html?model=/fixtures/stage-flat/hero.x3d`);
await x3dPage.waitForFunction(() => window.__x3d?.ready, null, { timeout: 120000 });
await x3dPage.waitForTimeout(2000);
const x3d = await x3dPage.evaluate(() => ({ ...window.__x3d }));
await shot(x3dPage, "09-x3d-player");
await x3dPage.close();
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
  [`dome light became the environment (${report.env?.type})`, !!report.env],
  ["the dome is DomeLight_1, the schema Houdini 22 writes", report.env?.type === "DomeLight_1"],
  [`dome sun where USD puts it, rotated ${DOME_ROTATE_Y} deg (radiance ${sunProbe.world.toFixed(1)}; ` +
    `unrotated ${sunProbe.unrotated.toFixed(1)}, old viewer ${sunProbe.old.toFixed(1)})`,
    sunProbe.world > 10 && sunProbe.unrotated < 5 && sunProbe.old < 5],
  ["variant switch applied", report.lastVariantSwitch?.option === "ember"],
  [`proxy shows, then hides again (${meshesBefore} -> ${meshesProxyOn} -> ${meshesProxyOff} meshes)`,
    meshesProxyOn > meshesBefore && meshesProxyOff === meshesBefore],
  ["USDLoader route loaded meshes", report.jsRoute?.meshes > 0],
  [`glTF player loads the exported fixture (${gltf.meshes} meshes, ${gltf.instanced} instances, ` +
    `${gltf.triangles} triangles, ${gltf.errors.length} errors)`,
    gltf.ready === true && gltf.errors.length === 0 && gltf.meshes >= 8 && gltf.instanced === 36
    && gltf.extensions.includes("EXT_mesh_gpu_instancing")],
  [`X3D player loads the exported fixture (${x3d.shapes} shapes, ${x3d.uses} instances, ` +
    `${x3d.switches} switches, ${x3d.errors.length} errors)`,
    x3d.ready === true && x3d.errors.length === 0 && x3d.uses === 36 && x3d.switches >= 2
    && x3d.viewpoints === 1 && x3d.loaded === true],
  [`/WebSettings title applied (document.title = "${ws.title}")`, ws.title === WS_TITLE && ws.report?.applied?.titleSource === "web:title"],
  [`/WebSettings showHud = false hides #hud (display ${ws.hudDisplay})`, ws.hudDisplay === "none"],
  ["/WebSettings stage loaded clean, dome intact", ws.errors.length === 0 && ws.env],
  ["a stage with no /WebSettings keeps its defaults", report.webSettings?.read?.present === false && report.webSettings.applied.showHud === undefined],
  [`no web:title falls back to the stage file name (title "${report.pageTitle}", heading "${report.hudHeading}")`,
    report.pageTitle === "shot.usda" && report.hudHeading === "shot.usda" && report.webSettings?.applied?.titleSource === "stage file name"],
];
console.log("---- checks ----");
for (const [name, ok] of checks) console.log(`${ok ? "ok  " : "FAIL"}  ${name}`);
if (checks.some(([, ok]) => !ok)) process.exitCode = 1;
