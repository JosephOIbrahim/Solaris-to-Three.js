# Wave 2 probes on Houdini 22.0.400 (2026-11-11)

Run with `hython -B tests/hou/probe_hda_api.py` under 22.0.400 with
`HOUDINI_USER_PREF_DIR=C:/Users/User/OneDrive/Documents/houdini22.0`. Raw JSON:
`.token-saver/probe_400.json` (git-ignored). Question numbers are the M5 API brief's.

| Probe | Answer | Detail |
|---|---|---|
| 1 environment | 22.0.400 | prefs `C:/Users/User/OneDrive/Documents/houdini22.0`, HOME `C:/Users/User`, isUI `False` |
| 2 import | works | WEBEXPORT_ROOT `E:/Myth_and_Logic/Software/Solaris-to-Three.js`, module `E:\Myth_and_Logic\Software\Solaris-to-Three.js\python\webexport\__init__.py` |
| 5 HDA scan path | wired | otlscan `['C:/Users/User/OneDrive/Documents/houdini22.0/packages/JLTools/HDAs', 'C:/Program Files/Side Effects Software/sidefx_packages/SideFXLabs22.0/platform_specific/windows/otls', 'C:/Program Files/Side Effects Software/sidefx_packages/SideFXLabs22.0/otls', 'C:/PROGRA~1/SIDEEF~1/HOUDIN~1.400/packages/texturepaint/otls', 'C:/PROGRA~1/SIDEEF~1/HOUDIN~1.400/packages/shotbuilder/otls', 'C:/PROGRA~1/SIDEEF~1/HOUDIN~1.400/packages/sculpt/otls', 'C:/PROGRA~1/SIDEEF~1/HOUDIN~1.400/packages/kinefx/otls', 'C:/PROGRA~1/SIDEEF~1/HOUDIN~1.400/packages/apex/otls', 'E:/Myth_and_Logic/Software/CVCOPS/houdini/otls', 'C:/Users/User/Conductor/houdini/ciohoudini/otls', 'C:/Users/User/OneDrive/Documents/houdini22.0/packages/MOPS_Plus/otls', 'C:/PROGRA~1/SIDEEF~1/HOUDIN~1.400/houdini/otls']` |
| 6 licence | licenseCategoryType.Indie | default HDA extension `.hdalc` |
| 7 /stage | LopNetwork True | after hipFile.clear: `True`; a LOP network has no `stage()` method |
| 8 subnet contents | one Output LOP | children `[['output0', 'output']]`, indirect inputs `['1', '2', '3', '4']` |
| 23 node parms | confirmed | sublayer type `sublayer` with `['filepath1', 'handlemissingfiles', 'num_files', 'sublayerfile_group1']`; error type `error` parms `['enable1', 'errormsg1', 'numerror', 'severity1']`, severity menu `['message', 'warn', 'error']` |
| 33 LOP stage | anonymous root | root `anon:0000000042FBA580:LOP:rootlayer`, up `Y`, m/unit `1.0`, time `1.0` to `120.0`, defaultPrim `Usd.Prim(</hero>)` |
| 34 FlattenLayerStack | works, needs a clean-up | 2 asset paths resolved (['./hero/asset.usda', './env/sky.hdr']); `/HoudiniLayerInfo` survives the flatten: `True`; sublayers after: `[]` |
| 9 createDigitalAsset | creates the type | type `mythandlogic::web_export::1.0`; before any edits the sections are `['Contents.gz', 'CreateScript', 'DialogScript', 'InternalFileOptions']` and the parms `[]`: a subnet HDA starts empty |
| 13 Directory parm | True | dialog script says `type directory`: `True`; editor tag `1`, editorlines in dialog `True` |
| 14 tags and callback | both kept | callback `kwargs['node'].parm('report').set('ran:'...`, language `scriptLanguage.Python` |
| 10 Tools.shelf | works | tools `['mythandlogic::lop_web_export::1.0']`, labels `['Web Export']`, locations `[['File IO/Export']]`. SideFX's shape, with no `<toolshelf>` wrapper, is what parses |
| 12 backups | none: `[]` | createBackups `False`, sections after save `['Contents.gz', 'CreateScript', 'DialogScript', 'InternalFileOptions', 'MessageNodes', 'Tools.shelf']` |
| 19 pressButton | runs the Python callback headless | plain `ran:None`, with arguments `ran:1` |
| 24 badge warning | reaches the asset | quiet `[]`, warn severity `['badge warning text']` |
| 25 off-chain badge | never cooks | a MessageNodes-listed node beside the chain gave `[]`: keep the badge in the chain |
| 27 error severity | unresolved | severity `error` produced no asset error and no inner error (`[]`, `[]`), while `stage()` returned `a stage` |
| 11 uninstall and remove | True | uninstallFile then os.remove works on Windows, so a rebuild can replace the file |

## What this settles for the build

01. Build a LOP subnet, keep its generated `output0`, and put the badge Error LOP between the indirect input and `output0`: an off-chain badge never cooks.
02. `createDigitalAsset` leaves the asset with no parameters and no `Tools.shelf`; add both, then `setParmTemplateGroup`, then save.
03. Use SideFX's shelf XML exactly, with the `$HDA_*` placeholders and no `<toolshelf>` wrapper, or the TAB entry silently vanishes.
04. `pressButton` runs Python callbacks headless and merges `arguments` into `kwargs`, so `test_hda.py` can press Check and Export.
05. The LOP stage's root is anonymous and carries `/HoudiniLayerInfo`; the W3 export must flatten, strip that prim, and stage to a file before the writer runs.
06. Warnings reach the asset through `MessageNodes`. Error severity did not raise anything in this probe, so the FIX FIRST badge needs a second design, checked while building the HDA.
