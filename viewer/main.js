// Solaris stage -> three.js r187, two ways:
//   A. Hydra route: real OpenUSD (WASM) composes the stage, Needle's three.js
//      render delegate turns Hydra prims into three.js objects.
//   B. JS route: three.js's own USDLoader reads a flattened .usdz (MIT, no WASM).
// Plus a small "lighting bridge" that does what the delegate doesn't:
// dome light -> environment map, USD exposure + light size -> three.js intensity.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { HDRLoader } from "three/addons/loaders/HDRLoader.js";
import { USDLoader } from "three/addons/loaders/USDLoader.js";
import { getUsdModule } from "@needle-tools/usd";
import { createThreeHydra } from "@needle-tools/usd/three";

// ?stage=/stages/<folder>/<root>.usda loads your own USD ROP export from stages/<folder>/
const STAGE_URL = new URL(new URLSearchParams(location.search).get("stage") || "/fixtures/stage/shot.usda", location.href).href;
const FLAT_URL = new URL("/fixtures/stage-flat/hero_flat.usdz", location.href).href;

const report = { revision: THREE.REVISION, variant: window.__threeVariant, isolated: self.crossOriginIsolated, errors: [], timings: {} };
window.__report = report;
const statusEl = document.getElementById("status");
if (new URLSearchParams(location.search).has("clean")) for (const id of ["hud", "status"]) document.getElementById(id).style.display = "none";
document.querySelector("#hud h1").textContent = `Solaris stage · three.js r${THREE.REVISION}`;
const t0 = performance.now();
const mark = (k) => (report.timings[k] = Math.round(performance.now() - t0));
const say = (msg) => { statusEl.textContent = msg; };
addEventListener("error", (e) => report.errors.push(String(e.message || e)));
addEventListener("unhandledrejection", (e) => report.errors.push(String(e.reason?.message || e.reason)));

// ------------------------------------------------------------------ renderer
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.AgXToneMapping;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap; // r187 removed PCFSoftShadowMap
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
const hydraRoot = new THREE.Group(); hydraRoot.name = "route:hydra";
const jsRoot = new THREE.Group(); jsRoot.name = "route:usdloader"; jsRoot.visible = false;
scene.add(hydraRoot, jsRoot);

const camera = new THREE.PerspectiveCamera(40, innerWidth / innerHeight, 0.05, 500);
camera.position.set(4, 2.5, 6);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enabled = false;
controls.target.set(0, 0.9, 0);

if (!self.crossOriginIsolated) {
  say("Not cross-origin isolated: serve with COOP: same-origin + COEP: require-corp.");
  throw new Error("not crossOriginIsolated");
}

// ------------------------------------------------------------------ Hydra route
say("booting OpenUSD (WASM)…");
const USD = await getUsdModule();
mark("wasmReady");

say("composing stage…");
const hydra = await createThreeHydra({
  USD,
  scene: hydraRoot,
  url: STAGE_URL,                      // shot.usda pulls hero/asset.usda as a payload over HTTP
  includedPurposes: ["default", "render"],
  complexity: "high",                  // OpenSubdiv refinement level (low/medium/high/veryhigh)
  autoPlay: true,
});
await hydra.ready();
mark("firstDraw");
await hydra.materialsReady();
mark("materialsReady");

const meta = hydra.stageMetadata();
report.stage = meta;

// Read the composed stage through the real USD API (this is usdview-grade data).
async function readStage() {
  return hydra.editStage((stage) => {
    const lights = [], variantSets = [], purposeRoots = [];
    const prims = stage.Traverse();
    for (let i = 0; i < prims.size(); i++) {
      const prim = prims.get(i);
      const type = prim.GetTypeName();
      const path = prim.GetPath();
      if (/Light$/.test(type)) {
        const v = (n) => { const a = prim.GetAttribute(n); return a?.IsValid?.() && a.HasAuthoredValue() ? a.GetValueString() : null; };
        lights.push({ path, type,
          intensity: v("inputs:intensity"), exposure: v("inputs:exposure"), color: v("inputs:color"),
          width: v("inputs:width"), height: v("inputs:height"), radius: v("inputs:radius"),
          angle: v("inputs:angle"), normalize: v("inputs:normalize"), texture: v("inputs:texture:file") });
      }
      const purpose = prim.GetAttribute("purpose");
      if (purpose?.IsValid?.() && purpose.HasAuthoredValue()) {
        const p = purpose.GetValueString();
        if (p === "proxy" || p === "guide") purposeRoots.push({ path, purpose: p });
      }
      const names = prim.GetVariantSetNames();
      for (let j = 0; j < names.size(); j++) {
        const set = names.get(j);
        const opts = prim.GetVariantNames(set);
        const list = []; for (let k = 0; k < opts.size(); k++) list.push(opts.get(k));
        variantSets.push({ path, set, options: list, selected: prim.GetVariantSelection(set) });
      }
    }
    return { lights, variantSets, purposeRoots };
  });
}
const composed = await readStage();
report.lights = composed.lights;
report.variantSets = composed.variantSets;

// ------------------------------------------------------------------ lighting bridge
const num = (s, d) => { const n = parseFloat(String(s ?? "").replace(/[()]/g, "")); return Number.isFinite(n) ? n : d; };
const vec3 = (s, d) => { const m = String(s ?? "").match(/-?[\d.eE+-]+/g); return m && m.length >= 3 ? m.slice(0, 3).map(Number) : d; };
const assetPath = (s) => String(s ?? "").replace(/^@|@$/g, "");

let envTexture = null;
async function applyDome() {
  const dome = composed.lights.find((l) => l.type === "DomeLight");
  if (!dome) return;
  const L = num(dome.intensity, 1) * 2 ** num(dome.exposure, 0);
  if (!envTexture && dome.texture) {
    // Simplification: resolve relative to the root layer. A real export may author it
    // in a sublayer; then resolve against that layer's URL instead.
    const url = new URL(assetPath(dome.texture), STAGE_URL).href;
    const hdr = await new HDRLoader().loadAsync(url);
    hdr.mapping = THREE.EquirectangularReflectionMapping;
    const pmrem = new THREE.PMREMGenerator(renderer);
    envTexture = pmrem.fromEquirectangular(hdr).texture;
    pmrem.dispose(); hdr.dispose();
    report.env = { url, size: [hdr.image.width, hdr.image.height] };
  }
  scene.environment = envTexture;
  scene.background = envTexture;
  scene.backgroundBlurriness = 0.35;
  scene.environmentIntensity = L;
  scene.backgroundIntensity = L;
}

// USD light radiance (intensity * 2^exposure) times emitting area ~ luminous intensity (cd),
// which is what three.js punctual lights take. normalize=1 means "already per-light".
function bridgedIntensity(l) {
  const L = num(l.intensity, 1) * 2 ** num(l.exposure, 0);
  const normalized = /^(1|true)$/i.test(String(l.normalize ?? ""));
  switch (l.type) {
    case "RectLight": return normalized ? L : L * num(l.width, 1) * num(l.height, 1);
    case "DiskLight": return normalized ? L : L * Math.PI * num(l.radius, 0.5) ** 2;
    case "SphereLight": return normalized ? L : L * Math.PI * num(l.radius, 0.5) ** 2;
    case "DistantLight": { const a = THREE.MathUtils.degToRad(num(l.angle, 0.53)) / 2; return L * Math.PI * a * a; }
    default: return L;
  }
}

function applyBridge() {
  const byPath = new Map(composed.lights.map((l) => [l.path, l]));
  const bridged = [];
  hydraRoot.traverse((o) => {
    if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; }
    if (!o.isLight) return;
    const l = byPath.get(o.userData.usdPath);
    if (!l) return;
    if (l.type === "DomeLight") { o.visible = false; return; } // replaced by the environment map
    const c = vec3(l.color, [1, 1, 1]);
    o.color.setRGB(c[0], c[1], c[2]);
    o.intensity = bridgedIntensity(l);
    if (o.isPointLight || o.isSpotLight) o.decay = 2;
    if (l.path.endsWith("/key")) { o.castShadow = true; o.shadow.mapSize.set(1024, 1024); o.shadow.bias = -0.0005; o.shadow.radius = 4; }
    bridged.push({ path: l.path, three: o.type, intensity: +o.intensity.toFixed(2) });
  });
  report.bridgedLights = bridged;
}
await applyDome();
applyBridge();
mark("bridged");

// ------------------------------------------------------------------ USD camera
let usdCam = null;
hydraRoot.traverse((o) => { if (!usdCam && o.isCamera && o.userData.usdTypeName === "Camera") usdCam = o; });
report.usdCamera = usdCam ? { path: usdCam.userData.usdPath, fov: +usdCam.fov.toFixed(2), aspect: +usdCam.aspect.toFixed(3) } : null;

let useUsdCam = !!usdCam;
function syncCamera() {
  if (!useUsdCam || !usdCam) return;
  usdCam.updateMatrixWorld(true);
  usdCam.matrixWorld.decompose(camera.position, camera.quaternion, new THREE.Vector3());
  // Keep the authored frame fully visible on any screen shape (phone portrait included).
  const a = innerWidth / innerHeight, tanV = Math.tan(THREE.MathUtils.degToRad(usdCam.fov) / 2);
  camera.fov = a >= usdCam.aspect ? usdCam.fov : THREE.MathUtils.radToDeg(2 * Math.atan(tanV * usdCam.aspect / a));
  camera.near = usdCam.near; camera.far = usdCam.far;
  camera.updateProjectionMatrix();
}
function setCamMode(usd) {
  useUsdCam = usd && !!usdCam;
  controls.enabled = !useUsdCam;
  if (!useUsdCam) {
    // Start orbiting from where the USD camera stood.
    const box = new THREE.Box3().setFromObject(hydraRoot.visible ? hydraRoot : jsRoot);
    if (!box.isEmpty()) controls.target.copy(box.getCenter(new THREE.Vector3())).setY(0.9);
    controls.update();
  }
  press("cam-usd", useUsdCam); press("cam-orbit", !useUsdCam);
}

// ------------------------------------------------------------------ JS route (lazy)
let jsLoaded = false;
async function loadJsRoute() {
  if (jsLoaded) return;
  jsLoaded = true;
  const t = performance.now();
  try {
    const group = await new USDLoader().loadAsync(FLAT_URL);
    jsRoot.add(group);
    let meshes = 0, lights = 0, cams = 0;
    group.traverse((o) => { if (o.isMesh) { meshes++; o.castShadow = o.receiveShadow = true; } if (o.isLight) lights++; if (o.isCamera) cams++; });
    report.jsRoute = { ms: Math.round(performance.now() - t), meshes, lights, cameras: cams };
  } catch (e) {
    report.jsRoute = { error: String(e.message || e) };
  }
}

// ------------------------------------------------------------------ UI
const press = (id, on) => document.getElementById(id)?.setAttribute("aria-pressed", String(on));
function setRoute(which) {
  hydraRoot.visible = which === "hydra";
  jsRoot.visible = which === "js";
  press("route-hydra", which === "hydra"); press("route-js", which === "js");
  for (const b of document.querySelectorAll("#variants button, #proxy, #play")) b.disabled = which === "js";
}
document.getElementById("route-hydra").onclick = () => setRoute("hydra");
document.getElementById("route-js").onclick = async () => { await loadJsRoute(); setRoute("js"); };
document.getElementById("cam-usd").onclick = () => setCamMode(true);
document.getElementById("cam-orbit").onclick = () => setCamMode(false);

let showProxy = false;
// Needle's delegate (usd 1.1.2) never hides a proxy mesh once it has been drawn, and it re-applies its
// own visibility on every sync, so setting .visible doesn't stick. Purpose is enforced with three.js
// layers instead, which the delegate never touches: proxy and guide meshes sit on their own layers,
// and the camera sees the proxy layer only while "show proxy" is on. It runs every frame, so meshes
// the delegate creates or replaces later are covered too.
const LAYER_PROXY = 1, LAYER_GUIDE = 2;
function purposeOf(o) {
  const tag = o.userData.usdRenderTag;
  if (tag === "proxy" || tag === "guide") return tag;
  const path = o.userData.usdPath || "";
  return composed.purposeRoots.find((r) => path === r.path || path.startsWith(r.path + "/"))?.purpose;
}
function applyPurposes() {
  hydraRoot.traverse((o) => {
    if (!o.isMesh) return;
    const p = purposeOf(o);
    o.layers.set(p === "proxy" ? LAYER_PROXY : p === "guide" ? LAYER_GUIDE : 0);
  });
  camera.layers.set(0);
  if (showProxy) camera.layers.enable(LAYER_PROXY);
}
async function toggleProxy() {
  showProxy = !showProxy;
  await hydra.setIncludedPurposes(showProxy ? ["default", "render", "proxy"] : ["default", "render"]);
  applyBridge();
  press("proxy", showProxy);
}
document.getElementById("proxy").onclick = toggleProxy;
let playing = true;
document.getElementById("play").onclick = (e) => {
  playing = !playing; hydra.setPlaying(playing);
  e.target.textContent = playing ? "pause" : "play"; press("play", playing);
};

const variantRow = document.getElementById("variants");
for (const vs of composed.variantSets) {
  for (const opt of vs.options) {
    const b = document.createElement("button");
    b.textContent = opt;
    b.dataset.set = vs.set;
    b.setAttribute("aria-pressed", String(opt === vs.selected));
    b.onclick = () => selectVariant(vs, opt);
    variantRow.append(b);
  }
}
let holdFrames = false; // freeze the canvas while Hydra re-syncs, so the draw isn't starved
async function selectVariant(vs, opt) {
  const t = performance.now();
  holdFrames = true;
  try {
  await hydra.editStage(async (stage) => { await stage.GetPrimAtPath(vs.path).SetVariantSelection(vs.set, opt); });
  await hydra.materialsReady();
  } finally { holdFrames = false; }
  applyBridge();
  vs.selected = opt;
  for (const b of variantRow.querySelectorAll(`button[data-set="${vs.set}"]`)) b.setAttribute("aria-pressed", String(b.textContent === opt));
  report.lastVariantSwitch = { set: vs.set, option: opt, ms: Math.round(performance.now() - t) };
}
window.__app = { selectVariant: (opt) => selectVariant(composed.variantSets[0], opt), setRoute, loadJsRoute, setCamMode,
  setProxy: toggleProxy, hydra };

// ------------------------------------------------------------------ inventory for the HUD/test
function inventory() {
  const inv = { meshes: 0, instanced: 0, materials: {}, lights: 0, cameras: 0 };
  hydraRoot.traverse((o) => {
    if (o.isInstancedMesh) inv.instanced += o.count;
    else if (o.isMesh && o.visible && o.layers.test(camera.layers)) inv.meshes++; // what the camera can draw
    if (o.isMesh) {
      const m = Array.isArray(o.material) ? o.material[0] : o.material;
      const key = `${o.userData.usdPath?.split("/").pop()}:${m?.type}${m?.name ? `(${m.name})` : ""}`;
      inv.materials[key] = true;
    }
    if (o.isLight && o.visible) inv.lights++;
    if (o.isCamera) inv.cameras++;
  });
  inv.materials = Object.keys(inv.materials).sort();
  return inv;
}
report.inventory = inventory();

// Diagnostic for real exports: which bound materials didn't make it to a real shader.
function fallbacks() {
  const out = new Set();
  hydraRoot.traverse((o) => { if (o.isMesh && o.visible && (Array.isArray(o.material) ? o.material[0] : o.material)?.name === "DefaultMaterial") out.add(o.userData.usdPath); });
  return [...out];
}
report.fallbackMaterials = fallbacks();
window.__inventory = inventory;

setCamMode(true);
const fb = report.fallbackMaterials.length ? `\n${report.fallbackMaterials.length} prims fell back to a default material (MaterialX failed or the network is unsupported: see the console).` : "";
say(`three r${THREE.REVISION} · isolated ${self.crossOriginIsolated} · WASM ${report.timings.wasmReady} ms · first draw ${report.timings.firstDraw} ms · materials ${report.timings.materialsReady} ms${fb}`);

// ------------------------------------------------------------------ loop
let last = performance.now();
renderer.setAnimationLoop((now) => {
  const dt = (now - last) / 1000; last = now;
  hydra.update(dt);
  if (holdFrames) return;
  syncCamera();
  if (controls.enabled) controls.update();
  applyPurposes();
  renderer.render(scene, camera);
  report.frame = hydra.getTime();
  report.frameMs = Math.round(0.9 * (report.frameMs ?? dt * 1000) + 0.1 * dt * 1000);
});
addEventListener("resize", () => {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
});
report.ready = true;
