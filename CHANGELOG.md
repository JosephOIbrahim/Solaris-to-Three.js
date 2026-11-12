# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `docs/review-2026-11-12.md`, an independent review of the tree at v0.6.10 by three lanes (export core, browser side, docs and tooling), ranked and deduplicated: four blockers, nineteen majors, twelve minors, the areas that held up, and six refinements.
- `plan/harness-blueprint.md` and `plan/harness-spec.md`: the dev-workflow harness, derived from the review. One rule above all: no gate counts unless it has been seen to fail. Shape is a lean code agent with one orchestrator; primitives are the gate registry, mutation ledger, claims ledger, golden tasks, replay runner and lessons ledger.
- B1 is fixed: the glTF writer expands vertices per corner and welds only when position, normal and UV agree, so POSITION, NORMAL and TEXCOORD_0 are parallel arrays indexed by the same index buffer. A UV seam is now representable. `validate_glb` checks attribute parity, that indices stay inside the vertex array and that triangle indices come in threes, and the test suite includes the mutation that proves it fails. `fixtures/stage-flat/hero.glb` is regenerated: the floor's attributes are 4/4/4 and each pedestal's are 24/24.

## [0.6.10] - 2026-11-12

This release records the green browser dispatch on `e7ade53`.

### Changed

- The on-demand browser dispatch on `e7ade53`, the v0.6.9 release commit, passed 18 of 18 with both jobs green; this release records it.

## [0.6.9] - 2026-11-12

This release records the green browser dispatch on `aa60357`.

### Changed

- `plan/wave3-handoff.md` is updated for v0.6.8: six patches since v0.6.2 and what each changed, the 175-test baseline, and the browser dispatch on `d6b3b7a`. Both dispatches are recorded green, `d6b3b7a` and `aa60357`.

## [0.6.8] - 2026-11-12

This release covers the v0.6.7 handoff update.

### Changed

- `plan/wave3-handoff.md` is updated for v0.6.7: the five patches since v0.6.2 and what each changed, the 175-test baseline, and the three changelog shape checks. The on-demand browser dispatch on `d6b3b7a` passed 18 of 18 and is recorded.

## [0.6.7] - 2026-11-12

This release covers the changelog blank-line check.

### Changed

- `tests/test_claims.py` gained a third structural check: every `## [` and `### ` heading must be followed by a blank line. Verified against three malformed changelogs and one well-formed one, which passes.

## [0.6.6] - 2026-11-12

This release covers the v0.6.5 handoff update.

### Changed

- `plan/wave3-handoff.md` is updated for v0.6.5: the three patch releases and what each changed, the 174-test baseline, the changelog structural checks, the HDA format assertion, and wave 7 closed with wave 8 as the plan.

## [0.6.5] - 2026-11-12

This release covers the changelog structural checks.

### Changed

- `tests/test_claims.py` now checks the changelog's structure as well as its words: the file must open with its `# Changelog` title, and every `## [x.y.z]` heading must descend. Both checks were verified against a deliberately broken file, and both fail on it.


## [0.6.4] - 2026-11-12

This release covers the HDA format assertion.

### Changed

- `tests/hou/test_hda.py` now fails when a ticked format writes no file, when the badge errors on a non-blocking export, or when a FIX FIRST export writes anything. Before, a broken or disabled writer passed as long as the remaining formats exported. Proven on 22.0.400, .492 and .466: four ticked, four written.

## [0.6.3] - 2026-11-12

This release covers the v0.6.2 handoff refresh.

### Changed

- The hand-off note (`plan/wave3-handoff.md`) is refreshed for v0.6.2: the release facts, the 172-test baseline, animation honesty as shipped, and the prose-drift entry replaced by the note that the claims test guards it. The note also records that this release adds nothing but the refresh itself.

## [0.6.2] - 2026-11-12

This release covers the claims test, animation precision and the drift fixes.

### Added

- The loss report now says exactly what animation was baked: X3D names a scale change it cannot represent instead of silently holding the start pose, and both glTF and X3D report animated points, shader inputs and visibility as `animated_attribute` at the current time sample only.
- The blueprint records what is still unverified: Quick Look, the start/middle/end animation probe's blind spot, and what the browser suite proves on CI.

- `tests/test_claims.py` fails when the README's version, the current release's notes, the format list, a relative link or the web-export card's paths drift from the code. It caught a real one on arrival: the glTF-animation bullet had been credited to 0.2.0 rather than 0.5.0.
- The glTF and X3D reports call out animation only when the stage actually moves, naming what moved, instead of firing on a declared time range; a time range with no motion now reports nothing.
- Prose drift fixed: the web-export card's Formats step now says four live toggles, and the glTF writer's header no longer claims animation is unwritten.
## [0.6.1] - 2026-11-12

This release keeps the README's version line in step with the package.

### Fixed

- The README's status line lagged the release (0.5.0 while the package said 0.6.0); it now says 0.6.1 and is checked against `package.json` before a release.

## [0.6.0] - 2026-11-12

This release covers the USDZ toggle and the assess-all-first fix.

### Added

- The node writes the fourth format: the **USDZ** toggle is live and on by default, and Export writes `hero.usda`, `hero.glb`, `hero.x3d` and `hero.usdz` plus their assets into one folder, on hython 22.0.400, .492 and .466.

### Fixed

- USDZ no longer reports `unsaved_layer` when it runs after another format. `api.export` now builds every format's loss report before any writer runs, so each report describes the stage as it arrived. The HDA test reads the node's verdict parm rather than the first verdict in the text, which had hidden a later format's FIX FIRST.

## [0.5.0] - 2026-11-12

This release covers glTF and X3D animation and USDZ. All four formats now write from one node.

### Added

- glTF bakes xform animation: a moving node's local-to-world is sampled at every timecode of the stage's range and written as glTF `animations` with one LINEAR sampler per channel (translation, rotation as quaternions, scale), times in seconds and the required accessor min/max. Geometry now sits in prim space with the transform on its node, so a node can move. `fixtures/stage-animated/` holds a cube that turns a full circle over 121 frames, built by `tools/make_animated_stage.py`, and the glTF test checks the channels and the 121-sample accessors. The profile reports animation as a note rather than a loss.
- X3D bakes xform animation: a moving node's local-to-world is sampled across the stage's range and written as a `TimeSensor`, `OrientationInterpolator`/`PositionInterpolator` nodes and `ROUTE` statements, with the node's static pose taken from the stage start. The profile reports animation as a note, and `validate_x3d()` checks the routes and the key counts.
- USDZ export (`python/webexport/profiles/usdz.py`, `writers/usdz.py`): `UsdUtils.CreateNewUsdzPackage` packs the USD folder export into one archive, with a profile for what Quick Look reads and does not. `inventory.gather` now reads a packaged layer's inner extension, so the census no longer calls a correct `.usdz` fix-first.

## [0.4.0] - 2026-11-12

This release covers two new formats. glTF and X3D join the USD folder, and the Web Export node writes all three.

### Added

- glTF export (`python/webexport/profiles/gltf.py`, `writers/gltf.py`): `api.export(stage, ["gltf"])` writes one `.glb` with meshes (quads triangulated, world transforms baked), UsdPreviewSurface to PBR, PNG and JPEG base colour textures embedded in the file, a camera, `KHR_lights_punctual` for sphere and distant lights, and `EXT_mesh_gpu_instancing` for PointInstancers. The report says what glTF cannot carry: subdivision as its cage, geometry variants reduced to the selected option, area and dome lights dropped, and any image that is not PNG or JPEG as a block. `validate_glb()` re-opens the file and checks the container, every accessor and every image.
- The glTF player (`viewer/players/gltf.html`): three's `GLTFLoader` on the exporter's own `fixtures/stage-flat/hero.glb`, with no Needle packages, framed from the model's bounds and lit by `KHR_lights_punctual` when the file carries lights. `tools/make_gltf_fixture.py` rebuilds the fixture, and the browser test now loads it and checks 8 meshes, 36 instances and zero errors.
- The Web Export node writes glTF too: the **glTF** format toggle is live and on by default, and `hou_node` asks the core for whichever formats are ticked. On hython 22.0.400, .492 and .466 the fixture gives GO and Export writes `hero.usda`, `hero.glb` and their assets (`external/`) into one folder.
- glTF material looks survive: the writer writes `KHR_materials_variants`, evaluates the bound material under each look of a material variant set and maps the primitive to one material per look. The fixture's `look` set now travels as chrome, ember and gold, with their own base colours, metalness and roughness, and the profile reports it as a note instead of a loss. Materials also resolve through the `mtlx` render context now, so MaterialX surfaces export their real values instead of the default grey.
- X3D export (`python/webexport/profiles/x3d.py`, `writers/x3d.py`): `api.export(stage, ["x3d"])` writes X3D 4.0 XML with `Transform`/`Shape`/`IndexedFaceSet`, `PhysicalMaterial`, a `Switch` per material variant set (one group per look), `DEF`/`USE` for PointInstancers, a `Viewpoint` and `PointLight`/`DirectionalLight`. The file validates against the official X3D 4.0 schema, and the test checks that with lxml when the schema is reachable. The profile reports what X3D cannot carry: subdivision as its cage, material variants duplicated per look, area and dome lights dropped, and images that are not PNG or JPEG blocking the export.
- The X3D player (`viewer/players/x3d.html`): X_ITE 16.7.1, MIT, pinned and loaded from jsDelivr, on the exporter's own `fixtures/stage-flat/hero.x3d`. X_ITE exposes no node counts, so the page parses the file it renders and reports shapes, instances and switches. `tools/make_x3d_fixture.py` rebuilds the fixture, `serve.mjs` serves `.x3d` as `model/x3d+xml`, and the browser test loads it with zero errors. All 18 browser checks pass.
- The Web Export node writes X3D too: the **X3D** format toggle is live and on by default. With all three formats on, the fixture gives GO on hython 22.0.400, .492 and .466, and Export writes `hero.usda`, `hero.glb`, `hero.x3d` and their assets into one folder.

## [0.2.0] - 2026-11-11

### Added

- The Web Export core skeleton (`python/webexport`): `LossReport` and `LossItem` with the verdict rule, `render_text` and JSON round-trip, the `Profile` and `Writer` contracts, and `/WebSettings` read and write, all pure `pxr`.
- `python/webexport/inventory.py` lists what is on a stage (meshes, instancers, materials, lights, cameras, variant sets, volumes, composition and textures) for the Web Export. The web census now reads the stage through it, so the census and the export see the same facts, and the census's verdicts are unchanged.
- The USD folder export. `webexport.api.export()` writes a stage into one self-contained folder: the root layer, every sublayer, reference and payload (unselected variants included) and every texture, copied in, with every path made relative and kept inside the folder, and `/WebSettings` authored into the root layer. Files from outside the source folder land under `external/`. Before anything is written, a loss report built from the web census says what will not survive. Any fix-first item writes nothing. `api.check()` gives the report alone.
- The viewer reads `/WebSettings` from the stage and applies `web:title`, `web:camera`, `web:hudVariantSets`, `web:showProxy`, `web:backgroundBlur`, `web:exposureOffset` and `web:showHud`; what it applied is recorded in `window.__report.webSettings`, and a browser check covers title and HUD.
- A Houdini package file (`packages/webexport.json`) and `scripts/install_package.py`, which deploys a resolved copy into the Houdini 22 user prefs packages folder, with `--dry-run`, `--verify` and `--prefs-dir`.
- A visual identity in `docs/assets/`: a logo mark (a vermilion sun on the horizon above three hairline scanlines) as light and dark SVG, wordmark lockups and a 1280 x 640 social preview rendered from HTML by `docs/assets/src/render.mjs`, five 11 px heading swatches, and a Mermaid theme that reads on GitHub's light and dark backgrounds.
- README rewritten for calm reading, with a current status and the explainer video; both Mermaid diagrams take the house theme and now show the census catching files outside the export folder and the dome oriented by its transform and `poleAxis`.
- `docs/media/explainer.mp4`, a silent 1 minute 47 second explainer in the house style, with its poster. `docs/media/explainer/` holds the page it is drawn from and `render.mjs`, which re-renders it and writes nothing if a font, an image, the reading time or the encoded file is off.
- `tools/decide.py` settles one design decision with Jev and code: Jev judges the options, whether the choice needs Joe, and any evidence checks in one request, and code adopts only above TypeSafe's documented thresholds, otherwise it asks Joe. `plan/decisions.jsonl` keeps the raw probabilities. The first decision, how to export an in-memory LOP stage, is recorded. `plan/web-export-tasks.json` holds the wave 2 tasks for the build router.
- The Web Export HDA (`houdini/otls/web_export.hda`), built by `scripts/build_hda.py` with hython 22.0.400: a LOP subnet asset `mythandlogic::web_export::1.0` with one input, the Formats, Output Folder, Web Settings and Report fields, and **Check**, **Export** and **Preview** buttons. `python/webexport/hou_node.py` is the only module that imports `hou`.
- Check and Export work on a live LOP stage. `webexport.api` flattens an anonymous root layer, strips `/HoudiniLayerInfo` and stages the result to a file before the USD-folder writer runs. Proven on hython 22.0.400, .492 and .466: the fixture stage gives GO, and the export writes `hero.usda` with its payload and dome texture copied into one folder.
- A FIX FIRST verdict now fails the cook on the asset itself. The in-chain badge is a Python Script LOP that calls `webexport.hou_node.badge`, which raises `hou.NodeError` for FIX FIRST and a warning for GO, WITH NOTES.
- **Preview** starts the repository's viewer server from the node, on port 5180 or `WEBEXPORT_PORT`, and opens it. `WEBEXPORT_NO_BROWSER=1` keeps a batch run quiet. Verified on 22.0.400: the server answers and outlives the hython process.
- `docs/web-export.md`, the operator's card for the node: install, the three buttons, reading the report, the batch path and the traps.
- Every validation problem now travels as a report item, so `validated=False` always carries its reason. A report refuses a verdict that contradicts its items and recomputes it when read back, and a report is bound to the stage it was built from.

### Fixed

- When `/WebSettings` has no `web:title`, the viewer now titles the page and the HUD with the stage file name, as blueprint section 4.5 says, and records where the title came from. A browser check covers it.
- `docs/architecture.md` and `docs/gotchas.md` no longer say the prototype hasn't met a real GPU or Windows; it ran on the RTX 4090 in 0.1.2 (T02). Live Jev calls are now verified too.
- The Web Export blueprint records Joe's decisions of 2026-11-11 (it lives in this repo, `min_num_inputs=1`, the M5 check on 22.0.400, 22.0.492 and 22.0.466, a silent video) and two section 6 results: live Jev calls work, and a LOP stage's in-memory root needs the W3 export path before M5's Check can pass.
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

[Unreleased]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/compare/v0.6.10...HEAD
[0.6.10]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.10
[0.6.9]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.9
[0.6.8]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.8
[0.6.7]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.7
[0.6.6]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.6
[0.6.5]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.5
[0.6.4]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.4
[0.6.3]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.3
[0.6.2]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.2
[0.6.1]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.1
[0.6.0]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.6.0
[0.5.0]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.5.0
[0.4.0]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.4.0
[0.2.0]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.2.0
[0.1.3]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.1.3
[0.1.2]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.1.2
[0.1.1]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.1.1
[0.1.0]: https://github.com/JosephOIbrahim/Solaris-to-Three.js/releases/tag/v0.1.0
