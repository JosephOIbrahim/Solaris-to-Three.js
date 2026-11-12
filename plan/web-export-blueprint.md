# Blueprint: Web Export for Houdini Solaris

Version 1, 2026-11-10. Copyright (c) 2026 Joseph O. Ibrahim. MIT.

**How to read this.** Sections 1 to 4 are the design and rarely change. Section 5 is the work, one mission at a time. Section 6 is the list of things nobody has verified yet. A Claude Code session reads sections 1 to 4 once, then executes exactly one mission from section 5.

---

## 1. The product in one paragraph

A **Web Export** LOP node sits at the end of a Solaris network in Houdini 22.0.400 and up. It writes the stage to one or more web formats, and before it writes anything it reports what will not survive the trip to each format. It lives in the `Solaris-to-Three.js` repo, works without Synapse, and can be called by Synapse.

## 2. First principles

Each principle is a rule the code must obey. If a mission seems to need breaking one, stop and ask Joe.

1. **An export is a translation, and the loss report is the product.** Writing files is easy. Knowing what broke is the value. No writer runs before its loss report exists.
2. **USD supplies facts, code makes decisions, Jev judges only what code cannot.** Same rule as the web census.
3. **The core is pure `pxr`.** Everything under `python/webexport/` except `hou_node.py` and `panel/` must import and pass tests with `pip install usd-core`, no Houdini. This is what lets CI test it.
4. **The node is a thin shell.** The HDA's Python calls `webexport.api`. No logic lives in the HDA.
5. **Structure out, never prose.** The core returns a `LossReport` data structure. Terminal, panel, node message and Synapse each render it their own way.
6. **Formats, not engines.** The menu lists file formats. Players and graphics APIs are viewer settings.
7. **One format is one plugin.** A format is a `Profile` (what it can hold) plus a `Writer` (how to write it). Adding X3D must not touch glTF.
8. **Settings travel in the stage.** Viewer settings are attributes on a `/WebSettings` Scope prim. The browser's USD bindings can read prim attributes and cannot read layer metadata.
9. **Fail open, fail loud.** A missing optional piece (Jev key, a player, a texture converter) degrades to a labelled warning, never a crash and never silence.

## 3. Architecture

```
Solaris stage (Usd.Stage)
   |
   v
inventory.py     facts: prims, materials, lights, variants, instancers, textures
   |
   v
profiles/*.py    per format: what each fact becomes (kept / changed / lost)
   |
   v
report.py        LossReport: verdict + items, as data
   |
   v
writers/*.py     writes the export folder for one format
   |
   v
validate.py      re-opens what was written; for the USD folder this is the web census
   |
   v
preview          repo's Node server + a player page per format
```

### Repo layout (new parts only)

```
python/webexport/
  __init__.py
  api.py              export(stage, formats, out_dir, settings) -> list[ExportResult]
  inventory.py        Inventory dataclass built from a Usd.Stage
  report.py           LossReport, LossItem, render_text(), to_json()
  settings.py         read/write /WebSettings
  profiles/           base.py, usd_folder.py, gltf.py, x3d.py, usdz.py
  writers/            base.py, usd_folder.py, gltf.py, x3d.py, usdz.py
  textures.py         find, copy, convert; flags .rat/.tx/UDIM
  validate.py
  hou_node.py         the ONLY module that imports hou
  panel/              optional Qt report panel (mission M9)
houdini/
  otls/               web_export.hda (built, committed)
  toolbar/            webexport.shelf
  scripts/python/     webexport_shelf.py
packages/webexport.json
scripts/build_hda.py          hython script; source of truth for the HDA
scripts/install_package.py    --dry-run, --verify (pattern from Synapse)
viewer/players/               gltf.html, x3d.html (USD stays at viewer/index.html)
tests/test_webexport_*.py
docs/web-export.md            operator's card
```

`tools/web_census.py` stays where it is. `validate.py` imports its `census()` and `decide()`.

## 4. Contracts

These four shapes are fixed. Missions fill them in; they do not redesign them.

### 4.1 Inventory (facts)

```python
@dataclass
class Inventory:
    root_layer: str
    up_axis: str; meters_per_unit: float
    time_range: tuple[float, float] | None
    meshes: list[MeshFact]            # path, points, has_uv, subdiv_scheme, purpose, material_path
    instancers: list[InstancerFact]   # path, prototype_paths, count
    materials: list[MaterialFact]     # path, kind: "preview" | "materialx" | "other", shader_id, textures
    lights: list[LightFact]           # path, type, intensity, exposure, color, texture
    cameras: list[CameraFact]
    variant_sets: list[VariantFact]   # prim_path, set_name, options, kind: "geometry" | "material" | "mixed"
    volumes: list[str]
    composition: CompositionFact      # references, payloads, sublayers, errors
    textures: list[TextureFact]       # path, ext, exists, is_udim, is_absolute
```

### 4.2 Profile (one per format)

```python
class Profile(Protocol):
    name: str                         # "usd_folder", "gltf", "x3d", "usdz"
    extensions: tuple[str, ...]
    def assess(self, inv: Inventory) -> list[LossItem]: ...
```

### 4.3 LossReport (structure)

```python
@dataclass
class LossItem:
    severity: Literal["blocks", "changes", "note"]   # x, !, -
    feature: str        # "variant_set", "rect_light", "materialx", ...
    count: int
    examples: list[str] # up to 3 prim paths
    becomes: str        # "baked to textures", "dropped", "Switch node", ...
    fix: str | None     # what the artist can do, if anything

@dataclass
class LossReport:
    format: str
    verdict: Literal["FIX FIRST", "GO, WITH NOTES", "GO"]
    items: list[LossItem]
    jev_note: str
```

Verdict rule, in code: any `blocks` gives FIX FIRST; else any `changes` gives GO, WITH NOTES; else GO.

### 4.4 Writer

```python
class Writer(Protocol):
    name: str
    def write(self, stage: Usd.Stage, inv: Inventory, out_dir: Path, settings: WebSettings) -> ExportResult: ...

@dataclass
class ExportResult:
    format: str; entry_file: Path; files: list[Path]; bytes: int
    report: LossReport; validated: bool; preview_url: str | None
```

### 4.5 /WebSettings

A `Scope` at `/WebSettings`, attributes namespaced `web:`. v1 set:

| Attribute | Type | Default |
|---|---|---|
| `web:title` | string | stage file name |
| `web:camera` | string (prim path) | first camera |
| `web:hudVariantSets` | string[] | all |
| `web:showProxy` | bool | false |
| `web:backgroundBlur` | float | 0.35 |
| `web:exposureOffset` | float | 0.0 |
| `web:showHud` | bool | true |

Writers for other formats map what they can and report the rest as `note`.

### 4.6 Format capability map (drives the profiles)

| USD fact | usd_folder | gltf | x3d | usdz |
|---|---|---|---|---|
| meshes, transforms | kept | kept | kept | kept |
| subdivision | kept | tessellated (changes) | tessellated (changes) | kept |
| PointInstancer | kept | `EXT_mesh_gpu_instancing` | DEF/USE (changes) | kept |
| references, payloads | kept | flattened (note) | flattened in v1 (note) | packed |
| variant set, geometry | kept | selected only (changes) | `Switch` (kept) | kept |
| variant set, material | kept | `KHR_materials_variants` | `Switch` duplicates (changes) | kept |
| UsdPreviewSurface | kept | PBR | `PhysicalMaterial` | kept |
| MaterialX | kept | fallback (changes) | fallback (changes) | Quick Look reads little (changes) |
| distant, sphere light | kept | `KHR_lights_punctual` | Directional, Point | kept |
| rect, disk light | bridged | dropped (changes) | dropped (changes) | kept |
| dome light | bridged, .hdr only | dropped in v1 (changes) | dropped in v1 (changes) | kept |
| camera | kept | kept | `Viewpoint` | kept |
| volumes | not drawn (changes) | dropped (changes) | dropped in v1 (changes) | not drawn (changes) |
| .rat/.tx/UDIM texture | blocks | blocks | blocks | blocks |

"MaterialX fallback" in v1 means: use the material's UsdPreviewSurface if one is bound for the `universal` render context, else a flat base colour read from the network's surface inputs. Baking MaterialX to texture maps is out of scope until a later version.

### 4.7 Accepted additions (Joe, 2026-11-11)

Wave 1 implemented these shapes with more than section 4 spells out. Joe recorded them as accepted, so they are part of the contract and not drift:

- `LossReport.from_items()`, which computes the verdict by the rule, and `LossReport.from_json`, which Joe has since agreed also recomputes it.
- `LossReport.to_json()` returning a dict, and `to_json_str()` returning text.
- Defaults on `LossItem` (`examples` `[]`, `becomes` `""`, `fix` `None`), on `LossReport` (`items`, `jev_note`), on `ExportResult.preview_url`, and default factories on every `Inventory` list.
- `api.check()` for the report alone, and `api.export(..., jev_on=...)`.
- `MeshFact.points` is a count (an int), not a list of points.
- `LossItem.examples` holds up to three prim paths or asset paths, whichever the finding names.

## 5. Missions

**Rules for every mission.**

- One mission per Claude Code session. Read sections 1 to 4, then only that mission.
- Work in `E:\Solaris-to-Three.js` on `main`. Start with `git pull --rebase`. Follow `docs/workflow.md`.
- A mission is done only when its **Check** command passes and `npm test` passes. Then add a line under `## [Unreleased]` in `CHANGELOG.md`, commit, pull, push.
- Do not start the next mission. Report: what was built, what the check printed, anything in section 6 that got verified or stayed open.
- If a check cannot pass after two honest attempts, stop and write what blocked it. Do not weaken the check.
- Size tag says which model is enough: **S** (small model), **L** (large model). Set the model once per session.

### v0.2.0: the node exists and exports what already works

**M0. Skeleton and contracts. (S)**
Create `python/webexport/` with `report.py`, `settings.py`, empty `profiles/base.py` and `writers/base.py` exactly as section 4. Add `tests/test_webexport_report.py` covering the verdict rule, `to_json()` round-trip and `render_text()` marks (x, !, -).
Check: `python -m unittest tests.test_webexport_report -v`

**M1. Inventory. (L)**
`inventory.py` builds an `Inventory` from a stage. Reuse census logic by moving shared fact-gathering out of `tools/web_census.py` into `inventory.py` and importing it back, so there is one source of facts. The census's own tests must still pass unchanged.
Check: `python -m unittest tests.test_webexport_inventory tests.test_census -v`. The fixture stage must report 1 instancer with 36 instances, 1 variant set `look` with 3 options, 2 purposes, 1 camera, 3 lights.

**M2. USD folder profile and writer. (L)**
`profiles/usd_folder.py` turns the census findings into `LossItem`s. `writers/usd_folder.py` writes a self-contained folder: root layer, payloads, textures copied in, all asset paths relative, `/WebSettings` authored. `validate.py` runs the census on the output. `api.export()` wires it together.
Check: `python -m unittest tests.test_webexport_usd_folder -v`. Exporting `fixtures/stage/shot.usda` to a temp dir gives verdict GO, the output opens with `Usd.Stage.Open`, has zero absolute paths, and contains `/WebSettings`. Exporting `fixtures/stage-dirty/dirty.usda` gives FIX FIRST and writes nothing.

**M3. Viewer reads /WebSettings. (S)**
`viewer/main.js` reads the seven attributes after load and applies them. Add a browser smoke check that sets `web:title` and `web:showHud=false` on a temp export and sees both take effect.
Check: `npm run test:browser` (needs the server running), all checks ok.

**M4. Package and installer. (S)**
`packages/webexport.json` (pattern from Synapse's `packages/synapse.json`: `hpath`, `load_package_once`, paths relative to `$HOUDINI_PACKAGE_PATH`; sets `WEBEXPORT_ROOT` and prepends `python/` to `PYTHONPATH`; nothing else). `scripts/install_package.py` with `--dry-run` and `--verify`, writing into the Houdini user prefs `packages/` folder.
Check: `python scripts/install_package.py --dry-run` prints the target path and JSON; `--verify` after a real install exits 0; `hython -c "import webexport; print(webexport.__file__)"` under 22.0.400 prints a path inside the repo.

**M5. The HDA. (L)**
`scripts/build_hda.py`, run with hython **22.0.400**, creates `houdini/otls/web_export.hda`: a LOP subnet HDA `mythandlogic::web_export::1.0`, one input, tab menu "Web Export". Parameters: Output Folder, Formats (toggle per format; only USD Folder enabled in v0.2), the seven Web Settings, buttons **Check**, **Export**, **Preview**. Callbacks call `webexport.hou_node` and nothing else. Check shows `render_text()` in a read-only multi-line string parm and sets a node warning or error by verdict. Preview starts `node server/serve.mjs` if port 5180 is free and opens the default browser.
Check: `hython tests/hou/test_hda.py` under 22.0.400, 22.0.492 and 22.0.466 (22.0.492 replaces 22.0.459, which is not installed; Joe, 2026-11-11): installs the HDA, builds a LOP net that loads the fixture stage, presses Check (verdict GO), presses Export (folder exists, census GO). The same script run headless is the batch path, so this also proves hython export.

**M6. Card and release. (S)**
`docs/web-export.md` operator's card (install, the three buttons, reading the report, batch use). README tools table and repo map updated. Bump to 0.2.0, release with `python tools/release.py`.
Check: `python tools/release.py --dry-run` passes.

### v0.3.0: glTF

**M7. glTF profile, writer, player. (L)**
Start with a one-hour spike recorded in `docs/decisions/0001-gltf-writer.md`: pure-Python writer from `pxr` versus an external converter. Default if the spike is inconclusive: pure Python, because it runs in CI. Scope: meshes (tessellate subdiv via `UsdGeom` or leave as cage and report), transforms, PBR from UsdPreviewSurface, textures, cameras, punctual lights, `EXT_mesh_gpu_instancing`, `KHR_materials_variants` for material-only variant sets, baked animation for xforms. `viewer/players/gltf.html` uses three's `GLTFLoader` (MIT), no Needle packages.
Check: unit test exports the fixture to `.glb`; the file passes a structural validation (JSON chunk parses, accessor counts match, every image resolves); the browser smoke test loads it in the glTF player with 0 errors and the expected mesh count.

### v0.4.0: X3D

**M8. X3D profile, writer, player. (L)**
Writer emits X3D 4.0 XML with the standard library (`xml.etree`), profile `Immersive` plus the components it uses. Mapping per section 4.6: `Transform`/`Shape`/`IndexedFaceSet`, `PhysicalMaterial`, `Switch` for geometry variant sets, DEF/USE for instancer prototypes, `Viewpoint`, `DirectionalLight`/`PointLight`. Validate against the X3D 4.0 XML schema in the test. Player page uses X_ITE **only after** its licence is confirmed (section 6); otherwise the mission ships the writer and schema test and leaves the player as an open item.
Check: unit test exports the fixture to `.x3d`, schema-valid, `Switch` has 3 children, 36 `USE` references.

### Later, each its own blueprint revision

- **USDZ** (v0.5): `UsdUtils.CreateNewUsdzPackage` from the USD-folder writer's output, plus a Quick Look profile.
- **Report panel** (M9): Qt panel using a copy of Synapse's `panel/designsystem/`, rendering `LossReport` side by side per format.
- **Synapse tool**: an optional tool in Synapse that calls `webexport.api.export` when the package is present. Lives in the Synapse repo, not here.
- **Splats**, **VAT companion**, **MaterialX baking**.

## 6. Unverified, on purpose

Nobody has confirmed these. The mission that touches one must verify it first and record the result here.

1. Whether a LOP HDA button callback can safely start a long-lived Node process on Windows (M5). **Verified 2026-11-11, headless path:** from hython 22.0.400, pressing Preview starts `server/serve.mjs` detached on the chosen port (5 `WEBEXPORT_PORT`), it answers on that port, and it outlives the hython process. Still open: the GUI path (question 43), whether a click flashes a console or stalls the UI.
2. X_ITE's licence (M8). X3DOM is MIT/GPL dual but does not support X3D 4.0.
3. Whether Houdini's own glTF output can take a LOP stage directly (M7 spike).
4. How MaterialX networks authored by Karma expose a usable base colour without baking (M7, M8).
5. Whether three's WebGPU renderer can run Needle's MaterialX materials. Not needed before v0.5.
6. Live Jev calls. All Jev paths are tested against a local stand-in only. **Verified 2026-11-11:** the build router and `tools/decide.py` both returned typed answers from `jev-latest` in live requests.
7. A LOP node's stage has an anonymous, in-memory root layer, and the USD-folder writer blocks it as an unsaved layer, so M5's Check cannot pass on a live LOP stage until W3 lands. **Decided 2026-11-11** (`plan/decisions/w3-in-memory-export.json`, Jev ADOPT at the threshold, Joe may veto): merge the layer stack with `UsdUtils.FlattenLayerStack`, stage it to a file, and hand that file to the existing USD-folder writer. Do not weaken the M5 check to get past it.

## 7. Guardrails

- **Never** vendor, copy or commit Needle's packages (`@needle-tools/*`). They are PolyForm Noncommercial and are installed from npm only. The glTF and X3D players must not import them.
- **Never** edit the Synapse repo (`C:\Users\User\SYNAPSE`) from this work. Reading it is fine.
- **Never** `git push --force`, commit anything under `stages/`, or put `TYPESAFE_API_KEY` in a file.
- Copyright lines read "Joseph O. Ibrahim".
- Build the HDA with the oldest supported Houdini (22.0.400) so newer builds can load it.
- Code copied from Synapse carries a header naming the source commit (`36148779`) and its MIT licence.

## 8. Decisions on record

| Decision | Choice | Whose call | Status |
|---|---|---|---|
| Where it lives | Standalone in `Solaris-to-Three.js`; Synapse may call it | Claude proposed, Joe confirmed 2026-11-11 | settled |
| Node form | LOP HDA at the end of the network | Joe ("add the WEB EXPORT node at the end of a network") | settled |
| Formats | USD folder, glTF, X3D, USDZ, then splats | Joe (X3D required) | settled |
| Workflow | Straight to main from the workstation | Joe | settled |
| Design system reuse | Copy from Synapse at a pinned commit, not a shared package | Claude default: it leaves Synapse untouched during the demo push | **Joe to confirm** |
| glTF writer route | Pure Python unless the M7 spike says otherwise | Claude default | open until M7 |
| HDA inputs | `min_num_inputs=1`, so an unwired Web Export node shows an error | Joe, 2026-11-11 | settled |
| M5 check builds | 22.0.400, 22.0.492 and 22.0.466 (22.0.459 is not installed) | Joe, 2026-11-11 | settled |
| Explainer video | Silent, no text-to-speech service | Joe, 2026-11-11 | settled |
| In-memory LOP export (W3) | Flatten the layer stack, stage it to a file, reuse the USD-folder writer | Jev ADOPT at the threshold, 2026-11-11 | settled unless Joe vetoes |
| Verdict consistency | `LossReport` refuses a verdict that contradicts its items, and `from_json` recomputes the verdict | Joe, 2026-11-11 | settled |
| Report binding | The writer records the root layer identifier its report was built from and raises when given a different stage | Joe, 2026-11-11 | settled |
| Contract additions | The wave 1 defaults and helpers are recorded as accepted additions in section 4.7 rather than removed | Joe, 2026-11-11 | settled |
| Licence line | `scripts/install_package.py` keeps Synapse's quote and adds this repo's "Joseph O. Ibrahim" form beside it | Joe, 2026-11-11 | settled |
| glTF and X3D start | After 0.2.0 ships, M7 then M8 (this answers the W10 decision) | Joe, 2026-11-11 | settled |
| Social preview | Joe uploads `docs/assets/social-preview.png` in the repository Settings; GitHub has no supported API for it | Joe, 2026-11-11 | Joe's action |
| FIX FIRST badge | An in-chain Python Script LOP calls `webexport.hou_node.badge`, which raises `hou.NodeError` on FIX FIRST and `hou.NodeWarning` on GO, WITH NOTES. An Error LOP at error severity was probed and reports nothing | Joe, 2026-11-11 | settled |
