# Architecture

This page explains how a Solaris stage ends up as pixels in a browser tab, from first principles. Read it once before changing the viewer or the server.

## The one idea

**three.js becomes another Hydra render delegate.** A Solaris LOP network produces a USD stage. Karma and the Solaris viewport never read your nodes; they read that stage through Hydra, USD's rendering layer. Hydra splits scene description from drawing: it hands prims to a *render delegate*, and the delegate turns them into pixels.

Needle compiled OpenUSD 26.05 to WebAssembly and wrote a render delegate that outputs three.js objects. So the browser runs the same composition engine Houdini does, and three.js sits where Karma or Storm would. Payloads, variants, purpose and instancing are resolved by real USD, not by a converter that guesses.

## The pipeline

```mermaid
flowchart TB
  subgraph desk["Houdini, at your desk"]
    lop["Solaris LOP network"] --> rop["USD ROP: one folder, relative paths"]
  end
  rop --> folder[("export folder: root layer, payloads, textures")]
  folder --> census["tools/web_census.py: FIX FIRST, GO WITH NOTES, or GO"]
  census -->|GO| stages["stages/name/"]
  stages --> server
  subgraph server["server/serve.mjs, port 5180"]
    headers["COOP same-origin and COEP require-corp on every response"]
  end
  server --> wasm
  subgraph tab["Browser tab, cross-origin isolated"]
    wasm["OpenUSD 26.05 in WebAssembly: composes the stage"] --> hydra["Hydra"]
    hydra --> delegate["Needle three.js render delegate"]
    delegate --> mtlx["MaterialX 1.8.0: shader generation"]
    delegate --> scene["three.js r187 scene"]
    mtlx --> scene
    bridge["Lighting bridge: dome to environment, USD lights to candela"] --> scene
    scene --> webgl["WebGL"]
  end
```

## The layers, one at a time

**The export.** The USD ROP writes the root layer, every payload and every texture into one folder with relative paths. The browser can only fetch what sits under the server's root, so an absolute `D:/textures/wood.png` is unreachable by design. `tools/web_census.py` checks this before you open the page; see [census.md](census.md).

**The server.** `server/serve.mjs` is a static file server with one job beyond serving files: it sends `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp` on every response. Those two headers make the page *cross-origin isolated*, and only an isolated page may use `SharedArrayBuffer`. The OpenUSD build is threaded, and its threads share memory through `SharedArrayBuffer`, so without the headers the runtime refuses to start. Any host you deploy to must send the same two headers. The server also maps four roots: `/` to `viewer/`, `/vendor/` to `node_modules/`, `/fixtures/` to the test stages and `/stages/` to your exports. It rejects paths that escape those roots and sends `Cache-Control: no-store` so a re-export is never hidden by a stale cache.

**The import map.** `viewer/index.html` builds an import map instead of using a bundler. The page imports `three`, `@needle-tools/usd` and `@needle-tools/materialx` by name, and the map points those names at `/vendor/`. That keeps the source you edit identical to the code the browser runs, and it lets `?three=185` swap the whole three.js version by changing one map entry.

**The runtime.** `getUsdModule()` boots the WebAssembly OpenUSD. `createThreeHydra()` opens the root layer by URL, and USD fetches payloads and sublayers over HTTP as composition asks for them. The delegate creates meshes, instanced meshes for `PointInstancer`, lights and cameras under a `THREE.Group`, and it keeps them in sync when the stage changes. `editStage()` gives you the real `Usd.Stage` API inside a callback, which is how the viewer reads lights and variant sets and how it switches variants.

**Materials.** UsdPreviewSurface maps to three.js physical materials directly. MaterialX networks (`standard_surface`, OpenPBR) go through Needle's MaterialX package, which generates GLSL that three.js compiles. MaterialX is the pinned part of the stack; [versions.md](versions.md) explains why the repo overrides it to 1.8.0.

**The lighting bridge.** The delegate creates three.js lights, but USD and three.js measure light differently, and the delegate leaves dome lights alone. `viewer/main.js` closes that gap: the dome's `.hdr` becomes a prefiltered environment map, and each area light's intensity is converted to the luminous intensity three.js expects. The arithmetic is in [lighting.md](lighting.md).

**The camera.** If the stage has a USD camera, the viewer looks through it and widens the vertical field of view on narrow screens so the authored frame stays fully visible, phone portrait included. The orbit button hands control to `OrbitControls`.

## Two routes, on purpose

The viewer carries a second route for comparison. The **Hydra route** is the main one described above. The **JS route** loads a flattened `.usdz` with three.js's own `USDLoader`, which is MIT-licensed and needs no WebAssembly, but it ignores `purpose`, builds only UsdPreviewSurface materials and does no composition of its own. Keeping both on one page shows exactly what the heavy runtime buys you.

## What is verified, and what is not

Verified in headless Chromium with software rendering, on the synthetic fixture stage: payloads over HTTP, live variant switching, purpose toggling, `PointInstancer`, OpenSubdiv refinement, the USD camera with phone framing, MaterialX `standard_surface` and OpenPBR, UsdPreviewSurface and time samples. `npm run test:browser` repeats those checks.

Not yet verified: a real GPU, Windows, a real Karma export and live calls to Jev. Those are tasks T02 and T03 in [`plan/tasks.json`](../plan/tasks.json).
