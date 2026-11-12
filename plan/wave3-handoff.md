# Wave 3 handoff: glTF (M7)

Start M7 from a fresh session in `E:\Myth_and_Logic\Software\Solaris-to-Three.js` with this note, the blueprint and `docs/decisions/0001-gltf-writer.md`. Wave 2 shipped the Web Export node and closed at v0.2.0; wave 3 adds glTF.

Reading path: section 01 for where things stand, 02 for the spike's result, then the steps in 05 one at a time. About five minutes before you start.

---

### 01. Where things stand (2026-11-11)

01. `main` is released at **v0.2.0**. The Web Export HDA (`mythandlogic::web_export::1.0`) installs from `python scripts/install_package.py`, and Check, Export and Preview work on Houdini 22.0.400, .492 and .466.

02. The core is `python/webexport/`: `inventory.py`, `report.py`, `settings.py`, `profiles/`, `writers/usd_folder.py`, `validate.py`, `api.py`, and `hou_node.py` for the node shell. Everything except `hou_node.py` is pure `pxr`.

03. Checks that pass today: 130 Python tests (`py -3.12 -m unittest discover -s tests`), `npm test`, the headless browser test (16 of 16), and `tests/hou/test_hda.py` on three Houdini builds.

04. The machine's plain `python` is broken (its 3.14 lost `python314.dll`), so use `py -3.12`. Hython needs `HOUDINI_USER_PREF_DIR=C:/Users/User/OneDrive/Documents/houdini22.0`.

---

### 02. The spike's answer

`docs/decisions/0001-gltf-writer.md` records it: **write glTF in pure Python from `pxr`**, because the external routes either need Houdini at export time or lose what the browser needs. The spike's limits and the one thing that is out of reach (OpenSubdiv tessellation) are in the same file.

Read it before you design the writer. It also answers blueprint section 6, item 3: Houdini's own glTF output cannot take a LOP stage directly.

---

### 03. Decisions and approvals on record

| Item | Value | Whose call |
|---|---|---|
| Format order | USD folder, glTF, X3D, USDZ, then splats | Joe |
| glTF writer route | Pure Python unless the spike says otherwise | Joe, 2026-11-11, and the spike agrees |
| glTF and X3D start | After 0.2.0 ships, M7 then M8 | Joe, 2026-11-11 |
| Node shell | The HDA holds no logic; it calls `webexport.hou_node` | Joe (blueprint principle 4) |
| Badge | In-chain Python Script LOP raising `hou.NodeError` on FIX FIRST | Joe, 2026-11-11 |

Anything beyond these, especially a release, needs Joe's go-ahead. Hython runs on this workstation are approved for the Web Export build and tests; other hython work needs asking.

---

### 04. Pins for this lane

01. Use `py -3.12` for unit tests and scripts. It has `usd-core` 26.5 and no numpy, PIL, pygltflib or trimesh, so the writer must do its own binary packing and copy image bytes as they are.

02. No `@needle-tools` import anywhere in this lane. `viewer/players/gltf.html` uses three's `GLTFLoader`, which is MIT.

03. Port 5180 belongs to Joe's viewer; a test server uses its own port.

04. Keep the pure-`pxr` rule: the glTF profile and writer must pass CI without Houdini.

---

### 05. The steps

01. **Profile first** (`profiles/gltf.py`), per blueprint principle 1: turn the inventory into `LossItem`s using the glTF column of section 4.6. Nothing is written before the report exists.

02. **Writer** (`writers/gltf.py`): one `.glb` with a JSON chunk and a BIN chunk. Scope from M7: meshes, transforms, PBR from `UsdPreviewSurface`, textures, cameras, `KHR_lights_punctual`, `EXT_mesh_gpu_instancing`, `KHR_materials_variants`, and baked xform animation. Subdivision is reported, not silently wrong: export the cage and say so.

03. **Player** (`viewer/players/gltf.html`): three's `GLTFLoader`, no Needle packages. Load the exported `.glb` and report meshes, materials and errors the same way the main viewer does.

04. **Tests**, the M7 check: a unit test exporting the fixture to `.glb` with structural validation (JSON chunk parses, accessor counts match, every image resolves), plus a browser smoke check of the player with zero errors and the expected mesh count.

05. **Release**: 0.3.0, with the operator's card updated and the changelog rolled, once everything above is green.

---

### 06. Open items carried in

01. **Blueprint section 6, item 3 is now answered** by the spike: no direct LOP-to-glTF path in Houdini. Item 2 (X_ITE's licence) is answered: MIT, so an X3D player page may load it.

02. **The GUI Preview check** (blueprint question 43) is still Joe's: whether a Preview click in the GUI flashes a console or stalls the UI.

03. **The social preview image** is still Joe's to upload in Settings.

04. **Subdivision** is the one place pure Python cannot reach: `usd-core` has no OpenSubdiv, so a catmullClark mesh ships as its cage unless the export is re-tessellated in Houdini. The profile must say so on every export that has one.

---

The node and the USD folder are shipped at 0.2.0; wave 3 takes the same loss report and adds a second format behind it.
