# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

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
- **Docs.** Architecture, versions, lighting, gotchas, and operator's cards for the census and router.

### Fixed during the prototype

- MaterialX failed on three r187 and crashed on r186; forced `@needle-tools/materialx` 1.8.0.
- Look variants had no visible effect because local opinions beat variant opinions; look values are now authored only inside variants.
- Materials went black after a second variant switch; drawing now pauses until materials are ready.
- A MaterialX mesh without UVs rendered black; the fixture gained UVs and the census flags the case.
- Script paths broke on Windows; they use `fileURLToPath` now.

[0.1.0]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.1.0
