# Operator's Card: USD ROP for the web

**One question:** how do I set up the USD ROP so the export folder loads in the browser as it is?

**The one rule:** everything the stage needs must sit inside one folder. The server only serves `stages/<name>/`, so a `../` path or an absolute path that works in Houdini will 404 on the web. The census now catches both.

## Set up the USD ROP

Settings on the USD ROP (LOP or ROP context), in the order they appear:

| Parameter | Set it to | Why |
|---|---|---|
| **Output File** | `$HIP/web/<name>/<name>.usda` | The root layer. Its folder *is* the export folder. `.usda` is slower to write but readable; `.usd` is fine too. |
| **Save Style** | **Flatten Implicit Layers** (the default) | Keeps variants, payloads and references, so variant switching still works on the web. **Flatten Stage** bakes the variant selection in and drops the others. |
| **Flatten File Layers** | On, if you sublayer files from outside the folder | Composes those disk layers into the output instead of pointing at them. |
| **Ensure Basic Metrics are Set on All Layers** | On | Writes `upAxis` and `metersPerUnit` into every file. Needle's source rotates Z-up stages to Y-up from this (read in its code, not tested in the browser). |

**Output processors** (the Output Processing section). Python plugins that rewrite file paths as the ROP saves. Add them from the **Output Processors** menu and order them top to bottom like this:

1. **Copy All Assets to Referencing Layer Directory** (`localizeassets`). Set its **Output Subdirectory** to `tex`. It copies every texture into the export folder.
2. **Use Relative Paths** (`simplerelativepaths`). Already there by default. It must stay *below* step 1, so the copied textures get the short relative path.
3. **Save Paths Relative to Output Path** (`savepathsrelativetooutput`). Any **relative** Save Path on a SOP Import or Configure Layer (for example `geo/hero.usd`) then lands inside the export folder. In the 22.0.466 source it anchors each save path to the Output File's folder, so an absolute save path such as `$HIP/usd/hero.usd` stays where it is: make those relative.

**The other route:** **Save All Files to a Specific Directory** (`savetodirectory`) re-roots every saved layer into one folder, absolute save paths included, and renames clashes. It flattens your folder layout, and it hasn't been tried here.

## The limit to know

**Copy All Assets skips USD files.** A Reference or Asset Reference LOP that points at an asset on disk outside the export folder, such as `$JOB/assets/hero/hero.usd`, stays where it is, and the ROP writes a `../` path to it. The census reports it as **files outside the export folder (404 on the web)**. Three ways out:

1. Copy the asset folder into the export folder, and reference it from there. Variants survive. This is the usual fix.
2. If the file comes in through a **Sublayer** LOP, turn on **Flatten File Layers**.
3. **Save Style > Flatten Stage**. Writes one self-contained layer, but bakes in the variant selection. Use it only when the page needs no variants.

## Then

```
hython tools/web_census.py <your hip folder>/web/<name>/<name>.usda
```

Fix anything under **FIX FIRST** (the card is [census.md](census.md)), copy the folder to `stages/<name>/`, and open `/?stage=/stages/<name>/<name>.usda`.

## Lights

- **Dome Light LOP:** both schemas work. The current node (`domelight::3.0`) writes `DomeLight_1`; older versions write `DomeLight`. The texture must be `.hdr` for now (T09).
- The dome's **Rotate** and **Pole Axis** carry over: the bridge orients the HDRI by USD's latlong convention, the dome's transform and `poleAxis`, and the browser test checks a rotated dome. Matching Karma side by side is still to be checked on a real scene (T03).
- Needle's delegate (`@needle-tools/usd` 1.1.2) creates rect, disk, sphere and cylinder lights as three.js point lights, read in its source. A rect light then lights in every direction. The arithmetic is in [lighting.md](lighting.md).

## From a script (untested)

The processor names above are module names under `$HFS/houdini/husdplugins/outputprocessors/`. The ROP turns one on when it has a spare toggle called `enableoutputprocessor_<name>`. Houdini's own menu callback adds that toggle and the processor's parameters, read from `loputils.py` in 22.0.466 and **not yet run**:

```python
import loputils
rop = hou.node("/stage/usd_rop1")
loputils.handleOutputProcessorAdd({"node": rop, "parm": rop.parm("outputprocessors"),
                                    "script_value": "localizeassets"})
rop.parm("localizeassets_subdirectory").set("tex")
# New processors are appended at the bottom. Move localizeassets above simplerelativepaths
# with its Move Up button (loputils.handleOutputProcessorMoveUp) before saving.
```

Check it in your build before relying on it: `hou.lop.outputProcessors()` lists the available names.

## Sources

- USD ROP parameters and the built-in processors: the installed help, `nodes/out/usd.txt` (22.0.429).
- Output processor API: `solaris/output.md#processors` (SideFX docs, 2026-10-04 snapshot).
- Processor module names, what `localizeassets`, `savepathsrelativetooutput` and `savetodirectory` do, and `handleOutputProcessorAdd`: read in the Houdini 22.0.466 install, `houdini/husdplugins/outputprocessors/` and `houdini/python3.13libs/loputils.py`. Read, not run.
- `DomeLight_1`: `nodes/lop/domelight.md` (SideFX docs, 2026-10-04 snapshot).

## Never

- Absolute or `$VAR` texture paths in the final export.
- Hand-edit `../` paths in the written files to "fix" them. Re-export instead, so the next export doesn't bring them back.
