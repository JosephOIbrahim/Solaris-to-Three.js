# 0001: the glTF writer route

Date: 2026-11-11. Status: accepted. Mission: M7 (blueprint `plan/web-export-blueprint.md`).

---

## The question

Should the glTF writer be pure Python built on `pxr`, or should it drive an external converter?

The blueprint's default, before the spike, was pure Python "because it runs in CI". This record tests that default rather than assuming it, and answers blueprint section 6, item 3: can Houdini's own glTF output take a LOP stage directly?

---

## The options

01. **Pure Python from `pxr`.** Read the stage with OpenUSD, triangulate, pack buffers with `struct`, and write the `.glb` container directly. Runs anywhere `usd-core` runs.

02. **Houdini's glTF output.** Convert the LOP stage into SOP geometry, then use Houdini's `gltf::2.0` ROP.

03. **A third-party converter** (`gltf-transform`, `usd2gltf`, a Python glTF library). Nothing is installed on this machine: `pygltflib`, `trimesh`, `numpy` and `PIL` are all absent from the Python that has `usd-core`.

---

## The evidence

**Houdini's glTF node exists, but not for USD.** A read-only probe under hython 22.0.400 shows `gltf` and `gltf::2.0` in the ROP category, described as "GLTF". The LOP category offers no glTF writer at all; its USD-facing nodes are `sopimport` and `sceneimport`, which both convert *into* USD. The only way from USD to SOP geometry is `usdimport` or `unpackusd`, which flattens the stage: variants and purpose are lost before glTF is ever reached. That route also requires Houdini at export time, which breaks blueprint principle 3 (the core is testable in CI without Houdini).

**The pure-Python prototype works, and is fast.** `scripts/spike_gltf_writer.py` writes a `.glb` from `fixtures/stage/shot.usda` with no packages beyond `usd-core`:

| Measure | Result |
|---|---|
| Meshes written | 8 |
| Triangles | 8,642 |
| PointInstancer instances | 36, through `EXT_mesh_gpu_instancing` |
| Materials | 6, from `UsdPreviewSurface` and the MaterialX surface inputs |
| Camera | 1, with `yfov` from focal length and vertical aperture |
| Lights | 1 (SphereLight to `KHR_lights_punctual` point) |
| File size | 674 KB |
| Wall clock | 134 ms |
| Container checks | magic, version, chunk types, buffer length, every accessor fits its view, images resolve: all pass |

**Textures can be embedded.** On a purpose-built stage whose `UsdPreviewSurface.diffuseColor` is connected to a `UsdUVTexture`, the prototype copies the PNG into the BIN chunk unchanged and writes `images` and `textures` entries; the container validates at 1 KB.

**Four limits showed up, all of them reportable rather than fatal.**

01. **Subdivision cannot be tessellated.** `usd-core` has no OpenSubdiv, so the fixture's `catmullClark` mesh ships as its cage. The blueprint already allows this ("leave as cage and report").

02. **Rect and dome lights have no punctual equivalent.** The fixture's `RectLight /lights/key` was dropped, and the dome is not a light at all in glTF. Both must be `LossItem`s.

03. **`KHR_materials_variants` is not implemented yet.** The prototype reports the variant set (`/hero:look`) but does not encode it. The extension is feasible: it is a list of materials plus per-primitive mappings.

04. **Image formats are limited to PNG and JPEG**, which glTF allows without transcoding. The dome's `.hdr` can never be an image; it is reported, as the USD folder export already does.

---

## The decision

**Write glTF in pure Python from `pxr`.** The prototype proves the container, accessors, instancing, materials, camera and texture embedding, at 134 ms and 674 KB for the fixture. The external route needs Houdini at export time and destroys variants before it starts, so it cannot serve the node or CI.

The blueprint's pre-committed default stands, now with evidence rather than assumption.

---

## What this means for the mission

01. The writer owns triangulation and the container. No glTF library is added.

02. Buffers are packed with `struct`, because `numpy` is not a dependency of the core.

03. The profile must report: subdivision as a cage, rect and dome lights dropped, `.hdr` and non-PNG/JPEG images unusable, and material variants either expanded or lost.

04. **Still unproven, and the M7 check must prove it:** that three.js's `GLTFLoader` loads the file in the browser with the expected mesh count, that baked xform animation is written correctly, that `KHR_materials_variants` round-trips, and how the writer behaves on a stage far larger than the fixture.

---

## Blueprint section 6, item 3

Answered: **no.** Houdini's own glTF output cannot take a LOP stage directly. It is a geometry ROP, and the bridge from USD to geometry (`usdimport`, `unpackusd`) flattens the stage first, losing variants and purpose, and it needs Houdini present.
