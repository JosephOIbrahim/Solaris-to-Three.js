# Handoff after v0.6.5

Start a fresh session in `E:\Myth_and_Logic\Software\Solaris-to-Three.js` with this note, `plan/web-export-blueprint.md` and `plan/wave8-tasks.json`. v0.6.5 is the current release; one LOP node writes four formats. glTF is shipped, so the old wave 3 mission is closed: this note is about what comes after it.

Reading path: section 01 for where things stand, 02 for what is next, then 03 and 04 before touching anything. About five minutes.

---

### 01. Where things stand (2026-11-12)

01. `package.json`, `README.md` and `CHANGELOG.md` say **v0.6.5**, released 2026-11-12 (pre-release). The three patch releases since v0.6.2 changed nothing in the export path: v0.6.3 refreshed this note, v0.6.4 added the HDA format assertion and repaired the changelog's heading order, v0.6.5 added the changelog structural checks. CI passed on each release commit, and the on-demand browser dispatch on `41417d5` passed 18 of 18. `tests/test_claims.py` fails when the README version, the current release's bullets, the format list, a relative link, the card's paths, or the changelog's shape drift from the code.

02. One LOP node, `mythandlogic::web_export::1.0`, installs from `py -3.12 scripts/install_package.py` and writes four formats from one Export: USD Folder, glTF (`.glb`), X3D (`.x3d`) and USDZ (`.usdz`). `api.FORMATS` is `("usd_folder", "gltf", "x3d", "usdz")`; splats is the planned fifth and is not written. With all four toggles on, the fixture gives GO and Export writes all four on hython 22.0.400, .492 and .466. `tests/hou/test_hda.py` now fails if any ticked format writes no file, so a disabled or broken writer cannot pass behind the others.

03. The core is `python/webexport/`: `inventory.py`, `report.py`, `settings.py`, `profiles/` (`usd_folder`, `gltf`, `x3d`, `usdz`), `writers/` (the same four), `validate.py`, `api.py`, and `hou_node.py` for the node shell. Everything except `hou_node.py` is pure `pxr`, so CI tests it without Houdini. The HDA holds no logic; its callbacks call `webexport.hou_node`.

04. What the writers carry:
   - **glTF** bakes a moving node's transform as LINEAR samplers, one sample per frame (121 on the animated fixture), and encodes `KHR_materials_variants`.
   - **X3D** bakes the same motion as a `TimeSensor`, interpolators and `ROUTE`s, 121 keys.
   - **USDZ** packs the USD folder, root layer and assets with `UsdUtils.CreateNewUsdzPackage`.
   - **Animation honesty** shipped in v0.6.2: X3D names a scale change it cannot represent instead of holding the start pose, and both glTF and X3D report animated points, shader inputs and visibility as `animated_attribute` at the current time sample only, because only transforms are baked. The probe is start/middle/end, so a motion that returns to its start pose at all three probes is reported static (blueprint section 6, item 9).

05. Players: three on one server — the USD viewer (`viewer/index.html`, OpenUSD in WebAssembly and Hydra), the glTF player (`viewer/players/gltf.html`, three's `GLTFLoader`), and the X3D player (`viewer/players/x3d.html`, X_ITE 16.7.1, MIT).

06. Tests: `npm test` (syntax checks plus the Python suite) passes, and `py -3.12 -m unittest discover -s tests` reports **174 Python tests**. The browser suite is **18 checks** (`tests/browser/smoke.mjs`); it passed on GitHub's `ubuntu-latest` runner and runs on `workflow_dispatch`, not every push. To run it locally, start the server first and install Playwright with Chromium.

07. The prose drift found earlier is fixed, and `tests/test_claims.py` guards the classes of it that matter: the README's version, release bullets, the format list, relative links, the card's paths, and now the changelog's shape. The last two checks are structural, because two rolls damaged the file rather than drifting its words: it must open with `# Changelog`, and the `## [x.y.z]` headings must descend. Both were verified by running them against a deliberately broken file. The README no longer states a test count, deliberately: it changed four times in a day.

08. Machine: use `py -3.12` for tests and scripts. It has `usd-core` 26.5 and no numpy, PIL, pygltflib or trimesh, so the writers pack binary themselves and copy image bytes as they are. Hython runs pin `HOUDINI_USER_PREF_DIR=C:/Users/User/OneDrive/Documents/houdini22.0`. Plain `python` is 3.14.2 today, its `python314.dll` is present, and `npm test` ran the full suite through it; the pinned interpreter is still 3.12 because that is what CI tests. The E1 repair task remains on the list.

---

### 02. What is next

01. Wave 7 is closed: **F1** (report animation only when something moves) merged at `431cb21`, **F2** (the claims test) at `5026d0c`, **F3** (this note) at `d496c3e`. `plan/wave8-tasks.json` is the current plan, and Jev puts no agent on it: every remaining item needs a GUI session, Joe's accounts, or a new blueprint revision. **J1** (social preview upload) and **J2** (GUI Preview check) are Joe's. **E1** closed itself: plain `python` is 3.14.2 again.

02. After wave 7, no feature mission is chosen. The candidates in the blueprint's own order: **T03** on a real Karma export, the **Qt report panel** (M9), the **Synapse tool** (lives in the Synapse repo), then **splats**, the **VAT companion** and **MaterialX baking**. A release stays Joe's: `tools/release.py` only runs from a clean `main` on the workstation, and it refuses unless CI passed on that exact commit.

---

### 03. Decisions on record

These still bind. The machine-readable entries are in `plan/decisions.jsonl`; the design record is blueprint section 8.

| Decision | What it settles | Source |
|---|---|---|
| In-memory export | Flatten the layer stack, stage it to a temporary file, strip `/HoudiniLayerInfo`, hand that file to the USD-folder writer. Do not weaken the M5 check to get past an anonymous root layer. | `w3-in-memory-export` (ADOPT); blueprint section 6, item 7 |
| The report is data | A `LossReport` is a list of `LossItem`s. A verdict that contradicts its items is refused in `post_init`, and `from_json` recomputes it. | `validation-reporting`, `verdict-consistency` |
| Stage binding | A writer records the root layer identifier its report was built from and raises on a different stage. | `report-stage-binding` |
| Contract additions | `from_items`, `to_json_str`, `jev_on` and `check` are recorded in blueprint section 4 as accepted additions, not removed. | `contract-drift` |
| glTF route | Pure Python from `pxr`, no Houdini at export time. Houdini's own glTF ROP cannot take a LOP stage. | `gltf-writer-route`; `docs/decisions/0001-gltf-writer.md` |
| FIX FIRST badge | An in-chain Python Script LOP calls `webexport.hou_node.badge`: it raises `hou.NodeError` on FIX FIRST and `hou.NodeWarning` on GO, WITH NOTES. | `fix-first-badge` |
| Formats | USD folder, glTF, X3D, USDZ, then splats. | Joe; blueprint section 8 |
| Node shell | The HDA calls `webexport.api`; no logic lives in the HDA. | Blueprint principle 4 |
| Builds | 22.0.400, 22.0.492 and 22.0.466; 22.0.459 is not installed. | Joe, 2026-11-11 |
| Social preview | `docs/assets/social-preview.png` is uploaded by Joe in repository Settings; GitHub has no supported API for it. | `social-preview` |
| Licence line | `scripts/install_package.py` keeps Synapse's quoted licence line and adds this repo's "Joseph O. Ibrahim" form beside it. | `licence-line` |

Still unsettled:

- **Design-system reuse** for the Qt panel: blueprint section 8 still says "Joe to confirm" for copying Synapse's `panel/designsystem/` at commit `36148779`.
- **A release**, and anything beyond the table above, needs Joe's go-ahead.

---

### 04. Pins for this lane

01. Use `py -3.12` for unit tests and scripts. The plain `python` is 3.14.2 (section 01.08), but 3.12 is the interpreter CI and the pins are tested on.

02. No `@needle-tools` import in the glTF or X3D player. The glTF player uses three's MIT `GLTFLoader`; the X3D player uses MIT X_ITE 16.7.1 from jsDelivr and keeps its copyright notice.

03. Port 5180 belongs to Joe's viewer. A test server uses its own port (`WEBEXPORT_PORT`).

04. Keep the pure-`pxr` rule: everything under `python/webexport/` except `hou_node.py` (and the planned panel) must import and pass tests with `pip install usd-core`, no Houdini.

05. No Houdini in a cloud worktree. HDA builds, hython tests, the Qt panel and the GUI Preview check are desk work. Hython runs on the workstation are approved for the Web Export build and tests; other hython work needs asking.

---

### 05. What remains

01. **T03**: census one real Karma export. The fixture is still the only end-to-end proof.

02. **Qt report panel** (M9): planned. It renders each format's `LossReport` side by side and needs a Houdini GUI session to build and check.

03. **Synapse tool**: planned, and it lives in `C:\Users\User\SYNAPSE`. Never edit that repo from this work; reading it is fine.

04. **Splats**, the **VAT companion** and **MaterialX baking**: later, each its own blueprint revision.

05. **Roadmap tasks** in `plan/tasks.json`: T03 is open, and `docs/gotchas.md` names the known gaps — T08 (shadows only for `/key` lights), T09 (the dome bridge reads `.hdr` only), T10 (`no-store`, so the WASM re-downloads on every load). The other tasks and their dependencies are in the same file.

06. **Open verification** in blueprint section 6: Quick Look has not opened a `.usdz` from this exporter (item 8); MaterialX base colour without baking (item 4); three's WebGPU with Needle's MaterialX (item 5); the animation probe's start/middle/end blind spot is deliberate (item 9). Item 10 records what the browser suite proves.

---

### 06. Open items at close

01. **Quick Look**: untested. There is no macOS in CI, so nobody has opened one of these `.usdz` files in Apple's viewer.

02. **J1, social preview**: Joe uploads `docs/assets/social-preview.png` in Settings.

03. **J2, Preview in the GUI**: the headless path is proven on 22.0.400 — the server starts detached and outlives hython. The GUI click is untested: whether it flashes a console, stalls the UI, or leaves the server running.

04. **E1, python 3.14**: works on this machine today and ran the suite, but it is not the pinned interpreter. The repair task is still on the wave 7 board.

05. **T03, a real Karma export**: still the missing proof that the census and the viewers agree with production work rather than the fixtures.

06. **Prose drift** listed in section 01.07: the test count in README, the USD-folder-only wording in `docs/web-export.md`, and the glTF writer header. The wave 7 F2 task exists because green tests did not catch this kind of drift.

---

The node, the four formats and the three players are shipped at v0.6.2. Nothing in the export path blocks the next session; the choice of feature is Joe's.
