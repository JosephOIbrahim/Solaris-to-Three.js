// Solaris stage -> three.js r187, two ways:
//   A. Hydra route: real OpenUSD (WASM) composes the stage, Needle's three.js
//      render delegate turns Hydra prims into three.js objects (dynamically imported).
//   B. JS route: three.js's own USDLoader reads a flattened .usdz (MIT, no WASM).
// Plus a small "lighting bridge" that does what the delegate doesn't:
// dome light -> environment map, USD exposure + light size -> three.js intensity.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { HDRLoader } from "three/addons/loaders/HDRLoader.js";
import { EXRLoader } from "three/addons/loaders/EXRLoader.js";
import { USDLoader } from "three/addons/loaders/USDLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const params = new URLSearchParams(location.search);
const rawStage = params.get("stage") || "/fixtures/stage/shot.usda";
const FLAT_URL = new URL("/fixtures/stage-flat/hero_flat.usdz", location.href).href;
const wantJsRoute = params.get("route") === "js";

const report = { revision: THREE.REVISION, variant: window.__threeVariant, isolated: self.crossOriginIsolated, errors: [], timings: {} };
window.__report = report;
const statusEl = document.getElementById("status");
if (params.has("clean")) for (const id of ["hud", "status"]) document.getElementById(id).style.display = "none";
document.querySelector("#hud h1").textContent = `Solaris stage · three.js r${THREE.REVISION}`;
const t0 = performance.now();
const mark = (k) => (report.timings[k] = Math.round(performance.now() - t0));
const say = (msg) => { statusEl.textContent = msg; };
addEventListener("error", (e) => report.errors.push(String(e.message || e)));
addEventListener("unhandledrejection", (e) => report.errors.push(String(e.reason?.message || e.reason)));

// ?stage=/stages/<folder>/<root>.usda loads your own USD ROP export from stages/<folder>/
function validateStageUrl(raw) {
  const u = new URL(raw, location.href);
  if (u.origin !== location.origin || !/^\/(fixtures|stages)\//.test(u.pathname)) {
    throw new Error(`blocked stage URL (must be same-origin under /fixtures/ or /stages/): ${raw}`);
  }
  return u.href;
}
let STAGE_URL;
try {
  STAGE_URL = validateStageUrl(rawStage);
} catch (e) {
  const msg = String(e.message || e);
  report.errors.push(msg);
  say(msg);
  throw e;
}

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

const useHydra = self.crossOriginIsolated && !wantJsRoute;
let hydra = null;
let composed = { lights: [], variantSets: [], purposeRoots: [] };
let webSettings = { present: false, title: null, camera: null, hudVariantSets: null,
  showProxy: false, backgroundBlur: 0.35, exposureOffset: 0, showHud: true };
let applied = {};

// Read the composed stage through the real USD API (this is usdview-grade data).
async function readStage() {
  return hydra.editStage((stage) => {
    const lights = [], variantSets = [], purposeRoots = [];
    const prims = stage.Traverse();
    for (let i = 0; i < prims.size(); i++) {
      const prim = prims.get(i);
      const type = prim.GetTypeName();
      const path = prim.GetPath();
      if (/Light(_\d+)?$/.test(type)) { // DomeLight_1: the schema Houdini 22's Dome Light LOP writes
        const v = (n) => { const a = prim.GetAttribute(n); return a?.IsValid?.() && a.HasAuthoredValue() ? a.GetValueString() : null; };
        lights.push({ path, type,
          intensity: v("inputs:intensity"), exposure: v("inputs:exposure"), color: v("inputs:color"),
          width: v("inputs:width"), height: v("inputs:height"), radius: v("inputs:radius"),
          length: v("inputs:length"), angle: v("inputs:angle"), normalize: v("inputs:normalize"),
          treatAsPoint: v("inputs:treatAsPoint"), texture: v("inputs:texture:file"),
          poleAxis: v("poleAxis") });
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

// /WebSettings (blueprint 4.5): viewer settings that travel in the stage. A missing prim or attribute
// means the default; null means "no authored value", so the viewer's own default rule applies.
async function readWebSettings() {
  return hydra.editStage((stage) => {
    const prim = stage.GetPrimAtPath("/WebSettings");
    const out = { present: !!(prim && prim.IsValid?.()), title: null, camera: null, hudVariantSets: null,
      showProxy: false, backgroundBlur: 0.35, exposureOffset: 0, showHud: true };
    if (!out.present) return out;
    const raw = (n) => { const a = prim.GetAttribute(n); return a?.IsValid?.() && a.HasAuthoredValue() ? a.GetValueString() : null; };
    const str = (n) => { const v = raw(n); return v == null ? null : String(v).replace(/^@|@$/g, "").replace(/^"|"$/g, ""); };
    const bool = (n, d) => { const v = raw(n); return v == null ? d : /^(1|true)$/i.test(String(v).trim()); };
    const flt = (n, d) => { const v = raw(n); const x = parseFloat(String(v ?? "").replace(/[()]/g, "")); return Number.isFinite(x) ? x : d; };
    const arr = (n) => { const v = raw(n); return v == null ? null : String(v).replace(/^\s*\[|\]\s*$/g, "").split(",").map((x) => x.trim().replace(/^["']|["']$/g, "")).filter(Boolean); };
    out.title = str("web:title");
    out.camera = str("web:camera");
    out.hudVariantSets = arr("web:hudVariantSets");
    out.showProxy = bool("web:showProxy", false);
    out.backgroundBlur = flt("web:backgroundBlur", 0.35);
    out.exposureOffset = flt("web:exposureOffset", 0);
    out.showHud = bool("web:showHud", true);
    return out;
  });
}

const stageFileName = decodeURIComponent(new URL(STAGE_URL).pathname.split("/").pop() || "");
if (useHydra) {
  // ------------------------------------------------------------------ Hydra route (dynamic import decouples MIT JS route)
  say("booting OpenUSD (WASM)…");
  const [{ getUsdModule }, { createThreeHydra }] = await Promise.all([
    import("@needle-tools/usd"),
    import("@needle-tools/usd/three"),
  ]);
  const USD = await getUsdModule();
  mark("wasmReady");

  say("composing stage…");
  hydra = await createThreeHydra({
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
  composed = await readStage();
  webSettings = await readWebSettings();
  report.webSettings = { read: webSettings, applied: {} };
  applied = report.webSettings.applied;
  // web:title, else the stage file name (blueprint 4.5). Same rule as python/webexport/settings.py:
  // fall back only when web:title isn't authored; an authored title wins, even an empty one.
  const title = webSettings.title ?? stageFileName;
  document.title = title;
  document.querySelector("#hud h1").textContent = title;
  applied.title = title;
  applied.titleSource = webSettings.title != null ? "web:title" : "stage file name";
  if (!webSettings.showHud) { document.getElementById("hud").style.display = "none"; applied.showHud = false; }
  renderer.toneMappingExposure = 2 ** webSettings.exposureOffset;
  applied.exposureOffset = webSettings.exposureOffset;
  applied.backgroundBlur = webSettings.backgroundBlur;
  report.lights = composed.lights;
  report.variantSets = composed.variantSets;
} else {
  report.route = "js";
  report.hydraDisabled = !self.crossOriginIsolated ? "not crossOriginIsolated" : "route=js";
  document.title = stageFileName;
  document.querySelector("#hud h1").textContent = stageFileName;
}

// ------------------------------------------------------------------ lighting bridge
const num = (s, d) => { const n = parseFloat(String(s ?? "").replace(/[()]/g, "")); return Number.isFinite(n) ? n : d; };
const vec3 = (s, d) => { const m = String(s ?? "").match(/-?[\d.eE+-]+/g); return m && m.length >= 3 ? m.slice(0, 3).map(Number) : d; };
const assetPath = (s) => String(s ?? "").replace(/^@|@$/g, "");
// Houdini 22's Dome Light LOP (domelight::3.0) writes DomeLight_1; older versions write DomeLight.
const isDome = (type) => /^DomeLight(_\d+)?$/.test(type);

let envTexture = null, envRT = null, envKey = null, envTexUrl = null;
async function applyDome() {
  try {
    const dome = composed.lights.find((l) => isDome(l.type));
    if (!dome) {
      if (envRT) { envRT.dispose(); envRT = null; envTexture = null; envTexUrl = null; }
      envKey = null; domeInfo = null; domeObject = null;
      scene.environment = null; scene.background = null;
      return;
    }
    const key = `${dome.texture ?? ""}|${dome.intensity ?? ""}|${dome.exposure ?? ""}|${dome.poleAxis ?? ""}`;
    if (key === envKey && domeInfo?.path === dome.path) {
      orientDome();
      return;
    }
    const L = num(dome.intensity, 1) * 2 ** num(dome.exposure, 0);
    const texPath = assetPath(dome.texture);
    const parsedUrl = texPath ? new URL(texPath, STAGE_URL) : null;
    if (parsedUrl && parsedUrl.origin !== location.origin) {
      throw new Error(`blocked cross-origin dome texture: ${parsedUrl.href}`);
    }
    const url = parsedUrl?.href ?? null;
    if (url !== envTexUrl) {
      if (envRT) { envRT.dispose(); envRT = null; envTexture = null; envTexUrl = null; }
      if (url) {
        const loader = /\.exr$/i.test(parsedUrl.pathname) ? new EXRLoader() : new HDRLoader();
        const hdr = await loader.loadAsync(url);
        hdr.mapping = THREE.EquirectangularReflectionMapping;
        const pmrem = new THREE.PMREMGenerator(renderer);
        envRT = pmrem.fromEquirectangular(hdr);
        envTexture = envRT.texture;
        envTexUrl = url;
        const size = [hdr.image?.width ?? 0, hdr.image?.height ?? 0];
        pmrem.dispose(); hdr.dispose();
        delete report.envError;
        report.env = { url, type: dome.type, size };
      }
    }
    scene.environment = envTexture;
    scene.background = envTexture;
    scene.backgroundBlurriness = webSettings.backgroundBlur;
    scene.environmentIntensity = L;
    scene.backgroundIntensity = L;
    domeInfo = dome;
    domeObject = null;
    envKey = key;
    orientDome();
  } catch (e) {
    report.envError = String(e.message || e);
  }
}

// Dome orientation: texture-to-world rotation = domeWorld * pole * USD_TO_THREE^-1.
const USD_TO_THREE_INV = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -Math.PI / 2);
const POLE_Z = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2);
const STAGE_Z_TO_Y = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
const token = (s) => String(s ?? "").replace(/["']/g, "").trim();
let domeInfo = null, domeObject = null;
const _q = new THREE.Quaternion(), _p = new THREE.Vector3(), _s = new THREE.Vector3();
function stageUpIsZ() {
  const up = report.stage?.upAxis;
  return (typeof up === "number" ? String.fromCharCode(up) : String(up ?? "Y")).toUpperCase().startsWith("Z");
}
function orientDome() {
  if (!domeInfo || !envTexture) return;
  if (!domeObject || !domeObject.parent) {
    domeObject = null;
    hydraRoot.traverse((o) => { if (!domeObject && o.isLight && o.userData.usdPath === domeInfo.path) domeObject = o; });
  }
  if (domeObject) { domeObject.updateWorldMatrix(true, false); domeObject.matrixWorld.decompose(_p, _q, _s); }
  else if (stageUpIsZ()) _q.copy(STAGE_Z_TO_Y);
  else _q.identity();
  const pole = token(domeInfo.poleAxis) || "scene";
  const poleZ = domeInfo.type !== "DomeLight" && (pole === "Z" || (pole === "scene" && stageUpIsZ()));
  if (poleZ) _q.multiply(POLE_Z);
  _q.multiply(USD_TO_THREE_INV);
  scene.environmentRotation.setFromQuaternion(_q);
  scene.backgroundRotation.copy(scene.environmentRotation);
  if (report.env) {
    const r = scene.environmentRotation;
    report.env.rotation = [r.x, r.y, r.z].map((a) => +THREE.MathUtils.radToDeg(a).toFixed(2));
    report.env.poleAxis = pole; report.env.poleZ = poleZ; report.env.fromXform = !!domeObject;
  }
}

// Test hook: average linear radiance of the background seen from the origin along a world direction,
// through a narrow camera, with blur off. The browser test uses it to find the sun in the dome.
function probeEnv(dir) {
  const rt = new THREE.WebGLRenderTarget(8, 8, { type: THREE.FloatType });
  const cam = new THREE.PerspectiveCamera(3, 1, 0.1, 10);
  cam.lookAt(new THREE.Vector3(...dir));
  const blur = scene.backgroundBlurriness, vis = [hydraRoot.visible, jsRoot.visible];
  scene.backgroundBlurriness = 0; hydraRoot.visible = jsRoot.visible = false;
  try {
    renderer.setRenderTarget(rt); renderer.render(scene, cam); renderer.setRenderTarget(null);
    const px = new Float32Array(8 * 8 * 4);
    renderer.readRenderTargetPixels(rt, 0, 0, 8, 8, px);
    let sum = 0; for (let i = 0; i < px.length; i += 4) sum += Math.max(px[i], px[i + 1], px[i + 2]);
    return sum / 64;
  } finally {
    scene.backgroundBlurriness = blur; [hydraRoot.visible, jsRoot.visible] = vis;
    rt.dispose();
  }
}

// USD light radiance (intensity * 2^exposure) -> three.js physical units (cd for punctual, lux for distant).
function bridgedIntensity(l, o) {
  const L = num(l.intensity, 1) * 2 ** num(l.exposure, 0);
  const normalized = /^(1|true)$/i.test(String(l.normalize ?? ""));
  const treatAsPoint = /^(1|true)$/i.test(String(l.treatAsPoint ?? ""));
  switch (l.type) {
    case "RectLight": {
      const area = num(l.width, 1) * num(l.height, 1);
      if (o?.isRectAreaLight) return normalized ? L / Math.max(area, 1e-6) : L;
      return normalized ? L : L * area;
    }
    case "DiskLight": return normalized ? L : L * Math.PI * num(l.radius, 0.5) ** 2;
    case "SphereLight": {
      if (treatAsPoint) return L;
      const r = num(l.radius, 0.5);
      return normalized ? L / 4 : L * Math.PI * r * r;
    }
    case "CylinderLight": return normalized ? L : L * 2 * num(l.radius, 0.5) * num(l.length, 1);
    case "DistantLight": {
      const a = THREE.MathUtils.degToRad(num(l.angle, 0.53)) / 2;
      return normalized ? L : L * Math.PI * Math.sin(Math.min(a, Math.PI / 2)) ** 2;
    }
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
    if (isDome(l.type)) { o.visible = false; return; } // replaced by the environment map
    const c = vec3(l.color, [1, 1, 1]);
    o.color.setRGB(c[0], c[1], c[2]);
    o.intensity = bridgedIntensity(l, o);
    if (o.isPointLight || o.isSpotLight) o.decay = 2;
    if (l.path.endsWith("/key")) { o.castShadow = true; o.shadow.mapSize.set(1024, 1024); o.shadow.bias = -0.0005; o.shadow.radius = 4; }
    bridged.push({ path: l.path, three: o.type, intensity: +o.intensity.toFixed(2) });
  });
  report.bridgedLights = bridged;
}

// ------------------------------------------------------------------ USD camera
const press = (id, on) => document.getElementById(id)?.setAttribute("aria-pressed", String(on));
let usdCam = null, cameraNote = "";
function resolveCamera() {
  usdCam = null; cameraNote = "";
  hydraRoot.traverse((o) => { if (!usdCam && o.isPerspectiveCamera && o.userData.usdTypeName === "Camera") usdCam = o; });
  if (webSettings.camera) {
    let want = null;
    hydraRoot.traverse((o) => { if (!want && o.isPerspectiveCamera && o.userData.usdTypeName === "Camera" && o.userData.usdPath === webSettings.camera) want = o; });
    if (want) { usdCam = want; applied.camera = webSettings.camera; }
    else { cameraNote = `\nweb:camera ${webSettings.camera} not found: using the default camera.`; applied.camera = null; }
  }
  report.usdCamera = usdCam ? { path: usdCam.userData.usdPath, fov: +usdCam.fov.toFixed(2), aspect: +usdCam.aspect.toFixed(3) } : null;
}

let useUsdCam = false;
function syncCamera() {
  if (!useUsdCam || !usdCam || !usdCam.isPerspectiveCamera) return;
  usdCam.updateMatrixWorld(true);
  usdCam.matrixWorld.decompose(camera.position, camera.quaternion, _s);
  // Keep the authored frame fully visible on any screen shape (phone portrait included).
  const a = innerWidth / innerHeight, tanV = Math.tan(THREE.MathUtils.degToRad(usdCam.fov) / 2);
  const authoredAspect = usdCam.aspect > 0 ? usdCam.aspect : a;
  camera.aspect = a;
  camera.fov = a >= authoredAspect ? usdCam.fov : THREE.MathUtils.radToDeg(2 * Math.atan(tanV * authoredAspect / a));
  camera.near = usdCam.near; camera.far = usdCam.far;
  camera.updateProjectionMatrix();
}
function setCamMode(usd) {
  useUsdCam = usd && !!usdCam;
  controls.enabled = !useUsdCam;
  if (!useUsdCam) {
    // Start orbiting around the active route's bounds centre.
    const box = new THREE.Box3().setFromObject(hydraRoot.visible ? hydraRoot : jsRoot);
    if (!box.isEmpty()) box.getCenter(controls.target);
    controls.update();
  }
  press("cam-usd", useUsdCam); press("cam-orbit", !useUsdCam);
}

// ------------------------------------------------------------------ JS route (MIT, lazy)
let jsLoaded = false;
async function loadJsRoute() {
  if (jsLoaded) return;
  jsLoaded = true;
  const t = performance.now();
  const targetUrl = /\.usdz$/i.test(new URL(STAGE_URL).pathname) ? STAGE_URL : FLAT_URL;
  try {
    const group = await new USDLoader().loadAsync(targetUrl);
    jsRoot.add(group);
    let meshes = 0, lights = 0, cams = 0;
    group.traverse((o) => { if (o.isMesh) { meshes++; o.castShadow = o.receiveShadow = true; } if (o.isLight) lights++; if (o.isCamera) cams++; });
    if (!scene.environment) {
      const pmrem = new THREE.PMREMGenerator(renderer);
      const rt = pmrem.fromScene(new RoomEnvironment(), 0.04);
      scene.environment = rt.texture;
      pmrem.dispose();
    }
    if (lights === 0 && !useHydra) {
      jsRoot.add(new THREE.HemisphereLight(0xffffff, 0x222222, 1.2));
      const key = new THREE.DirectionalLight(0xffffff, 2.0); key.position.set(3, 5, 4); jsRoot.add(key);
    }
    report.jsRoute = { ms: Math.round(performance.now() - t), meshes, lights, cameras: cams };
  } catch (e) {
    jsLoaded = false;
    report.jsRoute = { error: String(e.message || e) };
  }
}

// ------------------------------------------------------------------ UI
function setRoute(which) {
  if (which === "hydra" && !hydra) which = "js";
  hydraRoot.visible = which === "hydra";
  jsRoot.visible = which === "js";
  press("route-hydra", which === "hydra"); press("route-js", which === "js");
  for (const b of document.querySelectorAll("#variants button, #proxy, #play")) b.disabled = which === "js";
  if (!hydra) document.getElementById("route-hydra").disabled = true;
}
document.getElementById("route-hydra").onclick = () => setRoute("hydra");
document.getElementById("route-js").onclick = async () => { await loadJsRoute(); setRoute("js"); };
document.getElementById("cam-usd").onclick = () => setCamMode(true);
document.getElementById("cam-orbit").onclick = () => setCamMode(false);

let showProxy = false;
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
  if (!hydra) return;
  showProxy = !showProxy;
  await hydra.setIncludedPurposes(showProxy ? ["default", "render", "proxy"] : ["default", "render"]);
  applyBridge();
  applyPurposes();
  press("proxy", showProxy);
}
document.getElementById("proxy").onclick = toggleProxy;
let playing = true;
document.getElementById("play").onclick = (e) => {
  if (!hydra) return;
  playing = !playing; hydra.setPlaying(playing);
  e.target.textContent = playing ? "pause" : "play"; press("play", playing);
};

const variantRow = document.getElementById("variants");
let holdFrames = 0; // ref-counted freeze so concurrent variant switches never unfreeze mid-sync
let variantChain = Promise.resolve();
async function selectVariant(vs, opt) {
  if (!hydra || !vs) return;
  const run = async () => {
    const t = performance.now();
    holdFrames += 1;
    try {
      await hydra.editStage(async (stage) => { await stage.GetPrimAtPath(vs.path).SetVariantSelection(vs.set, opt); });
      await hydra.materialsReady();
      composed = await readStage();
      report.lights = composed.lights;
      report.variantSets = composed.variantSets;
      await applyDome();
      applyBridge();
      applyPurposes();
      if (!usdCam || !usdCam.parent) resolveCamera();
    } finally {
      holdFrames = Math.max(0, holdFrames - 1);
    }
    vs.selected = opt;
    for (const b of variantRow.querySelectorAll("button")) {
      if (b.dataset.set === vs.set && b.dataset.path === vs.path) {
        b.setAttribute("aria-pressed", String(b.textContent === opt));
      }
    }
    report.lastVariantSwitch = { set: vs.set, option: opt, ms: Math.round(performance.now() - t) };
  };
  variantChain = variantChain.then(run, run);
  return variantChain;
}

// ------------------------------------------------------------------ inventory for the HUD/test
function inventory() {
  const inv = { meshes: 0, instanced: 0, materials: {}, lights: 0, cameras: 0 };
  hydraRoot.traverse((o) => {
    if (o.isInstancedMesh) inv.instanced += o.count;
    else if (o.isMesh && o.visible && o.layers.test(camera.layers)) inv.meshes++;
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

function fallbacks() {
  const out = new Set();
  hydraRoot.traverse((o) => { if (o.isMesh && o.visible && (Array.isArray(o.material) ? o.material[0] : o.material)?.name === "DefaultMaterial") out.add(o.userData.usdPath); });
  return [...out];
}
window.__inventory = inventory;
window.__app = { selectVariant: (opt) => selectVariant(composed.variantSets[0], opt), setRoute, loadJsRoute, setCamMode,
  setProxy: toggleProxy, hydra, probeEnv };

if (useHydra) {
  await applyDome();
  applyBridge();
  mark("bridged");
  resolveCamera();

  const hudSets = webSettings.hudVariantSets?.length ? new Set(webSettings.hudVariantSets) : null;
  applied.hudVariantSets = hudSets ? [...hudSets] : null;
  for (const vs of composed.variantSets) {
    if (hudSets && !hudSets.has(vs.set)) continue;
    for (const opt of vs.options) {
      const b = document.createElement("button");
      b.textContent = opt;
      b.dataset.set = vs.set;
      b.dataset.path = vs.path;
      b.setAttribute("aria-pressed", String(opt === vs.selected));
      b.onclick = () => selectVariant(vs, opt);
      variantRow.append(b);
    }
  }
  setCamMode(true);
  if (webSettings.showProxy) { await toggleProxy(); applied.showProxy = true; }
  applyPurposes();
  report.inventory = inventory();
  report.fallbackMaterials = fallbacks();
  const fb = report.fallbackMaterials.length ? `\n${report.fallbackMaterials.length} prims fell back to a default material (MaterialX failed or the network is unsupported: see the console).` : "";
  const envNote = report.envError ? `\ndome environment failed to load (${report.envError}).` : "";
  say(`three r${THREE.REVISION} · isolated ${self.crossOriginIsolated} · WASM ${report.timings.wasmReady} ms · first draw ${report.timings.firstDraw} ms · materials ${report.timings.materialsReady} ms${fb}${envNote}${cameraNote}`);
} else {
  await loadJsRoute();
  setRoute("js");
  setCamMode(false);
  say(`three r${THREE.REVISION} · MIT USDLoader route (${report.hydraDisabled}) · ${report.jsRoute?.meshes ?? 0} meshes`);
}

// ------------------------------------------------------------------ loop
let last = performance.now();
renderer.setAnimationLoop((now) => {
  const dt = Math.min(0.1, Math.max(0, (now - last) / 1000)); last = now;
  if (hydra && hydraRoot.visible) hydra.update(dt);
  if (holdFrames > 0) return;
  syncCamera();
  orientDome(); // follows an animated dome; cheap when nothing moves
  if (controls.enabled) controls.update();
  if (hydra && hydraRoot.visible) applyPurposes();
  renderer.render(scene, camera);
  if (hydra) report.frame = hydra.getTime();
  report.frameMs = Math.round(0.9 * (report.frameMs ?? dt * 1000) + 0.1 * dt * 1000);
});
addEventListener("resize", () => {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  if (useUsdCam && usdCam) syncCamera();
  else camera.updateProjectionMatrix();
});
report.ready = true;
