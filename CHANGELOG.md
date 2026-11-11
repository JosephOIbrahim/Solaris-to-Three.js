# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- The Web Export core skeleton (`python/webexport`): `LossReport` and `LossItem` with the verdict rule, `render_text` and JSON round-trip, the `Profile` and `Writer` contracts, and `/WebSettings` read and write, all pure `pxr`.
- `python/webexport/inventory.py` lists what is on a stage (meshes, instancers, materials, lights, cameras, variant sets, volumes, composition and textures) for the Web Export. The web census now reads the stage through it, so the census and the export see the same facts, and the census's verdicts are unchanged.
- The USD folder export. `webexport.api.export()` writes a stage into one self-contained folder: the root layer, every sublayer, reference and payload (unselected variants included) and every texture, copied in, with every path made relative and kept inside the folder, and `/WebSettings` authored into the root layer. Files from outside the source folder land under `external/`. Before anything is written, a loss report built from the web census says what will not survive. Any fix-first item writes nothing. `api.check()` gives the report alone.
- The viewer reads `/WebSettings` from the stage and applies `web:title`, `web:camera`, `web:hudVariantSets`, `web:showProxy`, `web:backgroundBlur`, `web:exposureOffset` and `web:showHud`; what it applied is recorded in `window.__report.webSettings`, and a browser check covers title and HUD.
- A Houdini package file (`packages/webexport.json`) and `scripts/install_package.py`, which deploys a resolved copy into the Houdini 22 user prefs packages folder, with `--dry-run`, `--verify` and `--prefs-dir`.
- A visual identity in `docs/assets/`: a logo mark (a vermilion sun on the horizon above three hairline scanlines) as light and dark SVG, wordmark lockups and a 1280 x 640 social preview rendered from HTML by `docs/assets/src/render.mjs`, five 11 px heading swatches, and a Mermaid theme that reads on GitHub's light and dark backgrounds.
- README rewritten for calm reading, with a current status and the explainer video; both Mermaid diagrams take the house theme and now show the census catching files outside the export folder and the dome oriented by its transform and `poleAxis`.
- `docs/media/explainer.mp4`, a silent 1 minute 47 second explainer in the house style, with its poster. `docs/media/explainer/` holds the page it is drawn from and `render.mjs`, which re-renders it and writes nothing if a font, an image, the reading time or the encoded file is off.

### Fixed

- The dome's HDRI now faces the way USD says. The viewer applies USD's latlong convention (image centre at +Z), the dome's transform and `DomeLight_1`'s `poleAxis` to `scene.environmentRotation` and `scene.backgroundRotation`. Before, the dome's rotation was ignored and every HDRI sat 90 degrees off USD's convention. The fixture dome is now turned 75 degrees, and the browser test finds its sun where USD puts it.

## [0.1.3] - 2026-11-11

### Added

- `docs/export.md`, the operator's card for setting up the USD ROP for the web: save style, metrics and the order of the built-in output processors.
- The census reports layers and textures outside the export folder (`../` paths) as fix-first. They load in Houdini and 404 in the browser. A new fixture, `fixtures/stage-escape/`, covers it.

### Fixed

- The viewer recognises `DomeLight_1`, the schema Houdini 22's Dome Light LOP writes. Before, the environment silently went missing while the census said GO. The fixture stage now uses `DomeLight_1`, and the browser test checks it.

## [0.1.2] - 2026-11-02

### Fixed

- Turning off "show proxy" now hides proxy prims again. Needle's delegate shows them when the proxy purpose is included but never hides them when it is removed, so the viewer now enforces purpose with three.js layers, which the delegate never touches. The browser test now checks that the proxy shows and then hides. Found in the first run on the RTX 4090 (T02).
- A push to `main` no longer cancels an on-demand browser test run in CI.

## [0.1.1] - 2026-11-02

### Added

- `tools/release.py` creates the GitHub release for the version in `package.json`, and refuses unless the tree is clean, local `main` matches GitHub, the changelog has notes for the version and CI passed on that commit.
- `docs/workflow.md`, the operator's card for changing, pushing and releasing from the workstation.

### Fixed

- The Jev client closes HTTP error responses before retrying or giving up. Python 3.14 warned about the unclosed connection, and a new test fails if one is left open.

## [0.1.0] - 2026-11-02

The first release: the working prototype, organised into a repository.

### Added

- **Viewer.** A Solaris stage composed by OpenUSD 26.05 in WebAssembly and drawn by Needle's three.js Hydra delegate on three.js r187. It includes live variant switching, a purpose toggle for proxies, the USD camera with phone framing, an orbit camera and playback of time samples.
- **Lighting bridge.** The dome `.hdr` becomes a prefiltered environment map, and rect, disk, sphere and distant lights are converted from USD radiance to three.js candela.
- **Comparison route.** three.js's own `USDLoader` reads a flattened `.usdz`, and `?three=185` runs the page on r185.
- **Server.** A static server that sends COOP and COEP on every response, mounts the viewer, vendor modules, fixtures and your exports, and rejects path traversal.
- **Web census** (`tools/web_census.py`). It gives a one-word verdict on whether an export can go to the web, with fix-first, will-look-different and worth-knowing lists. Jev judges unfamiliar shader nodes.
- **Build router** (`tools/build_router.py`). It routes each roadmap task to a cloud agent, the desk or a decision, with Jev's help and a confidence floor.
- **Fixtures.** A Solaris-shaped test stage, its flattened `.usdz`, and a dirty stage with eight planted problems.
- **Tests.** Python unit tests for the census and router against a local Jev stand-in, syntax checks, and a headless Chromium smoke test with pass/fail checks.
- **CI.** Unit tests and syntax checks on every push and pull request; the browser test on demand.
- **Licence.** MIT for this repo's own code. Needle's packages keep their PolyForm Noncommercial licence.
- **Docs.** Architecture, versions, lighting, gotchas, and operator's cards for the census and router.

### Fixed during the prototype

- MaterialX failed on three r187 and crashed on r186; forced `@needle-tools/materialx` 1.8.0.
- Look variants had no visible effect because local opinions beat variant opinions; look values are now authored only inside variants.
- Materials went black after a second variant switch; drawing now pauses until materials are ready.
- A MaterialX mesh without UVs rendered black; the fixture gained UVs and the census flags the case.
- Script paths broke on Windows; they use `fileURLToPath` now.

[Unreleased]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.1.3
[0.1.2]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.1.2
[0.1.1]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.1.1
[0.1.0]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.1.0
