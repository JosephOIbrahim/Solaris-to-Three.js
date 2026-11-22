# Operator's Card: Web Export node

**One question:** what will this Solaris stage lose on its way to the web, and is it safe to write?

**The one rule:** the report comes first. The node writes the export folder only when nothing blocks.

## Install

Run these from the checkout you will keep, not from a worktree; the installed package records that folder.

1. `python scripts/install_package.py --dry-run` shows the target path and the JSON, and writes nothing.
2. `python scripts/install_package.py` writes `<prefs>/packages/webexport.json`.
3. `python scripts/install_package.py --verify` reads it back and exits 0 when it points at this repo.
4. `python scripts/doctor.py` runs the full Houdini 22 launch preflight check (Python, OpenUSD, Node.js 22+, HDA/shelf/panel/menu/icon assets, installed package, viewer dependencies, preview port).
5. Restart Houdini 22.

`<prefs>` is `--prefs-dir` if given, else `$HOUDINI_USER_PREF_DIR`, else `Documents/houdini22.0`, the real Documents folder whether OneDrive redirects it or not. On Windows with more than one Python installed, `py -3.12 scripts/install_package.py` names the interpreter.

The package sets `WEBEXPORT_ROOT`, puts `python/` on `PYTHONPATH`, and adds `houdini/` to `HOUDINI_PATH`, so `import webexport` works and Houdini 22 automatically discovers the HDA, the **Web Export** top-level main menu (`houdini/MainMenuCommon.xml`), the **Web Export** custom shelf (`houdini/toolbar/webexport.shelf`), and the **Web Export** Python Panel (`houdini/python_panels/webexport.pypanel`). The HDA is committed in the repo; you do not build it to use it.

## Main Menu, Custom Shelf and Python Panel (Houdini 22)

- **Web Export main menu (`Web Export`):** Appears directly in Houdini 22's top menu bar (before **Help**) with one-click access to **Add Web Export LOP**, **Open Report Panel**, **Check Stage**, **Export Web Formats**, **Preview in Browser**, and **Run Preflight Check...**.
- **Web Export shelf (`Web Export`):** Enable it from any Shelf Set's `+` menu. It adds five Solaris tools: **Add Web Export** (creates and wires `mythandlogic::web_export::1.0` below the selected or display LOP), **Report Panel**, **Check Stage**, **Export Web**, and **Preview Viewer**.
- **Web Export Python Panel (`Web Export`):** Open it from the main menu, from the shelf's **Report Panel** button, from any pane tab's `+ -> New Pane Tab Type -> Web Export`, or from the Python Panel toolbar dropdown. It renders side-by-side loss report cards and a feature comparison matrix across **USD Folder**, **glTF**, **X3D**, and **USDZ**, syncs bidirectionally with a selected Web Export LOP (or inspects any Solaris LOP read-only), and lets you double-click any USD prim path in the findings tree to select it in the Scene Graph.

## Add the node

01. In `/stage`, press Tab and choose **Web Export** under **File IO/Export**.
02. Wire the last stage of the network into its one input. The asset requires one input, so an unwired node shows an error.
03. Set **Output Folder**. The default is `$HIP/web_export`.
04. **Formats** has four live toggles: **USD Folder**, **glTF**, **X3D** and **USDZ**, all on by default. Export writes the folder and the three files beside it, and each format's report says what it cannot carry: subdivision as a cage, area and dome lights dropped, a duplicated copy of the geometry per look in X3D, and Quick Look's limited reading of MaterialX and lights in USDZ.

| Setting | Effect in the viewer |
|---|---|
| **Title** | The page title. Blank uses the stage file name. |
| **Camera** | A camera prim path. Blank uses the stage's first camera. |
| **HUD Variant Sets** | Spaces or commas separate set names. Blank gives every set buttons. |
| **Show Proxy** | Starts with proxy prims visible. |
| **Background Blur** | Blur of the environment background, default 0.35. |
| **Exposure Offset** | Exposure change in stops, default 0.0. |
| **Show HUD** | Shows the variant and camera HUD, on by default. |

The seven settings travel as `/WebSettings` attributes in the export, so the viewer reads them from the stage.

## The three buttons

| Button | Writes | What it does |
|---|---|---|
| **Check** | The report, nothing else | Reads the composed stage on input 0 and puts the loss report in **Report**. This button never writes a file. |
| **Export** | The folder, or nothing | Runs the same report, then writes the folder only when nothing blocks. The report gains a headline with the file count and folder. |
| **Preview** | Nothing | Starts `node server/serve.mjs` on `http://127.0.0.1:5180` when the port is free, then opens the browser. When the port is already serving, it opens that page. |

Set `WEBEXPORT_NO_BROWSER=1` in the environment to start the server without opening a browser, and `WEBEXPORT_PORT` to use a port other than 5180 (useful when another session already holds it).

**The badge on the node repeats the verdict.** The asset contains an in-chain Python Script LOP that reads the hidden verdict. **FIX FIRST** raises an error on the node, **GO, WITH NOTES** raises a warning, and **GO** says nothing. Because the badge sits in the cook chain, a FIX FIRST verdict also fails the asset's cook. That is deliberate: a stage that cannot go to the web should not pass silently to the next node.

## Read the report

The first word is the verdict.

| Verdict | Means | Do |
|---|---|---|
| **FIX FIRST** | The folder would not load, or would look wrong, in the browser | Fix every `x` line, then press Check again |
| **GO, WITH NOTES** | It loads, but it will differ from Karma | Read the `!` lines, then press Export |
| **GO** | Nothing found that stops it | Press Export |

**Marks:** `x` stops it, `!` will look different, `-` is worth knowing. The report groups the findings under FIX FIRST, WILL LOOK DIFFERENT and WORTH KNOWING.

```
LOSS REPORT   usd_folder
------------------------------------------------------------------------
GO             Nothing found that stops it. Open it in the viewer.

------------------------------------------------------------------------
```

Each finding names the feature, the count and up to three examples, then what it becomes and the fix where there is one. The profile starts from the web census findings and uses the same words, so [census.md](census.md) is the reference for the common `x` fixes.

**The verdict is code's, not a model's.** USD supplies the facts, the census rules decide the verdict, and Jev only judges whether an unrecognised shader node is portable MaterialX. Without a key, or if Jev fails, those nodes are marked for your eyes and the report still runs.

What usually blocks:

- **`.rat` and `.tx` textures.** Convert them to `.png`, `.jpg` or `.exr`. 0.2.0 copies files and does not convert them.
- **UDIM sets.** Bake the tiles to one map. The browser has no UDIM support.
- **A file the writer cannot find.** A `$VAR` path, a package path, a URI, or a path relative to the in-memory LOP root with no folder to resolve against. Put the file on disk and point at it, or make the path resolvable.
- **MaterialX meshes with no UVs, and composition errors.** The census lists both, with the fix.

## Batch

The HDA test is also the batch path. It installs the asset, builds a LOP chain, presses Check and Export into a temporary folder, and prints JSON. It needs hython from Houdini 22 and the package installed.

```
hython -B tests/hou/test_hda.py --expect "GO" --json out.json
```

| Flag | What it adds |
|---|---|
| `--hda <path>` | Uses another HDA file |
| `--expect "GO"` | Fails the run unless the last report ends in that verdict |
| `--json out.json` | Saves the full result, including the report text and file list |

The script sets `WEBEXPORT_NO_BROWSER=1`, so no browser can open. Exit codes: 0 pass, 2 nothing connected, 3 files written without a verdict, 4 the verdict was not the expected one, 5 a ticked format wrote no file, 6 the badge raised an error on a non-blocking export, 7 a FIX FIRST export wrote files, 8 a format toggle was missing or off, 9 the verdict parameter disagreed with the report. Use it to gate a batch check.

## Traps

- **The LOP stage lives in memory.** Its root layer is anonymous, so the node flattens the layer stack, stages it to a temporary file, strips `/HoudiniLayerInfo`, and hands that file to the USD-folder writer. What it exports is what the node cooked, not the `.hip` on disk. A path relative to the anonymous root has no folder to resolve against; the report names it as missing.
- **`../` paths, `.rat`/`.tx` and UDIM textures can still block.** The writer copies a resolvable file from outside the export folder into `external/` and reports it as a note, but a `../` path that resolves to nothing is a block, and no texture is converted or baked.
- **Preview needs Node.** It uses `node.exe` beside Houdini when one exists, else `node` on `PATH`. Without either, the button reports the missing runtime instead of crashing.
- **Preview opens your export when it lives under `stages/`.** The server serves `/`, `fixtures/`, `stages/` and `vendor/`. Exports under `stages/<name>/` deep-link straight to `?stage=/stages/...`; exports outside `stages/` get a note saying there is no preview link and open the viewer root.
- **The HDA is built, not hand-edited.** Rebuild it with `hython -B scripts/build_hda.py` under Houdini 22.0.400, the oldest supported build.

## Proven

On hython 22.0.400, 22.0.466 and 22.0.504, with `fixtures/stage/shot.usda`, Check says **GO** / **GO, WITH NOTES** and Export writes all four formats (`hero.usda` + `external/`, `hero.glb`, `hero.x3d`, `hero.usdz`).

## Where it lives

| Path | Holds |
|---|---|
| `houdini/otls/web_export.hda` | The asset, `mythandlogic::web_export::1.0` |
| `houdini/MainMenuCommon.xml` | The Houdini 22 top-level **Web Export** menu |
| `houdini/toolbar/webexport.shelf` | The Houdini 22 **Web Export** custom shelf (5 Solaris tools) |
| `houdini/scripts/python/webexport_shelf.py` | Thin Python callbacks for the main menu and custom shelf |
| `houdini/python_panels/webexport.pypanel` | The Houdini 22 **Web Export** Python Panel interface definition |
| `houdini/config/Icons/webexport.svg` | Custom house-mark SVG icon for the HDA, shelf, and Python Panel |
| `python/webexport/panel/` | PySide6 side-by-side loss report panel (`widget.py`) and pure-Python view-model (`model.py`) |
| `python/webexport/hou_node.py` | The three button callbacks and the badge |
| `python/webexport/` | The pure `pxr` core: report, inventory, profiles and writers |
| `scripts/build_hda.py` | The source of truth for the node; rebuilds the HDA |
| `scripts/install_package.py` | Installs the package into the Houdini prefs |
| `scripts/doctor.py` | Preflight diagnostics for Houdini 22 launch readiness |
| `scripts/package_release.py` | Deterministic studio release archive + `SHA256SUMS.txt` builder |
| `tests/hou/test_hda.py` | The batch path and the multi-build Houdini 22 proof |
