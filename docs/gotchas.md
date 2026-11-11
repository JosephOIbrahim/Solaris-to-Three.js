# Gotchas

These are the traps that cost real time while building the prototype. Each one has a cause and a fix, so you only pay for it once.

## Variant values do nothing

**Local opinions beat variant opinions.** USD composes in LIVRPS order: Local, Inherits, Variants, References, Payloads, Specializes. If a material value is authored directly on the prim *and* inside a variant, the local value always wins, and switching the variant changes nothing you can see. Author look values only inside the variants. `tools/make_stage.py` does this with its `look_in_variants` flag.

## Materials go black after a second variant switch

The Hydra draw was starving: the page kept rendering while the delegate re-synced, so materials were drawn half-built. The viewer now sets `holdFrames` during `selectVariant`, which keeps the clock running but skips drawing until `materialsReady()` resolves.

## A MaterialX mesh renders black

**MaterialX needs UVs.** A subdivision mesh with no `st` primvar rendered black under a MaterialX material, and adding `st` fixed it. The likely cause, not yet confirmed in Needle's source, is that the generated shader expects texture coordinates whether or not it samples a texture. Give every MaterialX mesh `primvars:st`. The census flags this as fix-first.

## The page says "Not cross-origin isolated"

The host isn't sending the two headers. Use `npm start`, or configure your host to send `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp`. Opening `index.html` from disk can never work.

## The dome light does nothing

**Houdini 22's Dome Light LOP writes `DomeLight_1`, not `DomeLight`.** The viewer only recognised the old schema, so the environment silently went missing while the census said GO. The viewer now accepts both, and the fixture uses `DomeLight_1` so the browser test covers it.

## The census said GO and the browser shows 404s

**A `../` path leaves the export folder.** Houdini's Use Relative Paths processor writes one for anything outside the USD ROP's output folder. The file exists on the workstation, so it loads in Houdini, but only the export folder is copied to `stages/<name>/`. The census now reports these as fix-first. The ROP settings that avoid them are in [export.md](export.md).

## Textures don't load

Needle's delegate loads `.png`, `.jpg`, `.tga` and `.exr`. Karma's `.rat` and `.tx` files will not load, and there is no UDIM support. Convert or bake before export. The census lists every one.

## An absolute path works in Houdini and fails in the browser

The browser can only fetch files under the server's root. `D:/textures/wood.png` and `$HIP/tex/wood.png` are invisible to it. Set the USD ROP to write relative paths and to copy textures into the export folder.

## MaterialX falls back to grey on a new three.js

This is the version trap in [versions.md](versions.md). Check the `overrides` block first.

## The two routes disagree about proxies

The Hydra route respects `purpose` and hides proxy and guide prims by default. three.js's own `USDLoader` ignores `purpose` and shows everything. The census mentions this when a stage has proxies.

## Proxies stay visible after turning them off

Needle's delegate (`@needle-tools/usd` 1.1.2) shows proxy prims when `proxy` is added to the included purposes, but leaves them on screen when it is removed: a mesh's visibility depends only on the prim's own visibility and its materials, and the delegate re-applies it on every sync, so setting `.visible` from outside doesn't stick. The viewer puts proxy and guide meshes on their own three.js layers, which the delegate never touches, and lets the camera see the proxy layer only while "show proxy" is on. If a later delegate release fixes this, the workaround is harmless; remove it then.

## Paths in scripts break on Windows

`new URL(...).pathname` gives `/C:/...` on Windows, which Node can't open. The scripts use `fileURLToPath` instead. Keep it that way in anything new.

## Software rendering is slow

Headless Chromium with SwiftShader can take minutes on the first draw. The browser test uses long timeouts and small viewports for that reason. Timings from it say nothing about a real GPU.

## Known gaps, still open

These came from an independent review and are tracked as tasks in `plan/tasks.json`.

- No real Karma export has been checked yet (T03).
- Only `/key` lights cast shadows (T08).
- The dome bridge reads `.hdr` only (T09).
- The server sends `no-store`, so the WebAssembly runtime re-downloads on every load (T10).
