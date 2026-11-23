# Beta Testing Guide: Solaris-to-Three.js (Houdini 22)

Thank you for beta-testing **Solaris-to-Three.js**! This guide takes about five minutes from install to your first exported Solaris stage.

## 1. Install and Preflight (2 minutes)

Download `solaris-to-three-v1.0.1-houdini22.zip` from the latest GitHub Release (or clone `main`), extract it to a permanent folder, and run:

```bash
python scripts/install_package.py
python scripts/doctor.py
```

If you want to preview the exported **USD Folder** locally in Chrome or Edge (in addition to exporting from Houdini 22), also install the Node.js viewer packages once:

```bash
npm ci
```

> **Licensing note:** The Houdini 22 integration, pure-`pxr` exporter, HTTP server, glTF player, X3D player, and `USDLoader` (`?route=js`) are **MIT**. The optional Needle Hydra WASM route (`@needle-tools/usd`, installed via `npm ci` and excluded from release zips) is **PolyForm Noncommercial** for local evaluation only.

Restart **Houdini 22** once `doctor.py` prints `READY FOR HOUDINI 22`.

## 2. Launch Inside Houdini 22

Open any Solaris `/stage` scene (or load `fixtures/stage/shot.usda` with a Sublayer LOP), then use any of the four entry points:

- **Top Menu Bar:** `Web Export -> Add Web Export LOP` or `Web Export -> Open Report Panel`
- **Custom Shelf:** `+ -> Shelves -> Web Export`
- **Python Panel:** `+ -> New Pane Tab Type -> Web Export`
- **TAB Menu in `/stage`:** `File IO/Export -> Web Export`

## 3. What We Most Want Tested in Beta

1. **Real Karma / Solaris production stages (T03):**
   - Does **Check** accurately predict what survives and what changes before writing?
   - Are there MaterialX nodes, light rigs, instancers, or texture paths (`../`, UDIM, `.rat`, `.tx`, `.exr`) where the report disagreed with what rendered in the browser?
2. **Houdini 22 GUI Ergonomics:**
   - Opening the **Web Export** Python Panel (docked and floating), switching LOP nodes via the navigation bar, and clicking **Check / Export / Preview** in a live GUI session (note whether Preview flashes a console or stalls the UI).
   - Verifying all four formats (**USD Folder**, **glTF**, **X3D**, **USDZ**) side-by-side in the panel matrix and double-clicking prim paths to jump in the Scene Graph.
3. **Apple Quick Look (`.usdz`):**
   - Opening an exported `.usdz` on macOS or iOS Quick Look and comparing framing, materials, and lighting against the loss report.
4. **Standalone Players:**
   - Opening exported `.glb` in `/players/gltf.html?model=/stages/<name>/<file>.glb` (camera framing, portrait resize, orbit, lighting, variants).
   - Opening exported `.x3d` in `/players/x3d.html?model=/stages/<name>/<file>.x3d`.
   - Opening `/?route=js` (MIT `USDLoader` fallback).

## 4. How to File a Great Beta Bug Report

Two one-click diagnostics make almost any issue immediately reproducible:

1. In the **Web Export** Python Panel, click **Copy JSON** (bottom right) and paste the report JSON into your issue.
2. Run `python scripts/doctor.py --json` and include the output along with your Houdini 22 build number (e.g. `22.0.504`) and OS.
3. If the issue is visual in the browser, open DevTools Console and paste `JSON.stringify(window.__report || window.__gltf || window.__x3d, null, 2)`.
