"""Build houdini/otls/web_export.hda, the Web Export LOP asset.

Run it with the oldest supported build, so newer builds can load what it writes:

    "C:/Program Files/Side Effects Software/Houdini 22.0.400/bin/hython.exe" -B \
        scripts/build_hda.py [--out <path>] [--keep-open]

The script is the source of truth for the asset: parameters, sections, callbacks and
the in-chain badge all live here, so a change to the node is a change to this file and a
rebuild, never a hand edit in Houdini. The HDA itself holds no logic: its buttons call
`webexport.hou_node` (blueprint principles 3 and 4).

What the 2026-11-11 probes on 22.0.400 settled, and this script relies on:
- `createDigitalAsset` leaves a subnet asset with no parameters and no `Tools.shelf`.
- Tools.shelf must be SideFX's shape, with no `<toolshelf>` wrapper, or the TAB entry
  silently disappears.
- A badge listed in MessageNodes reports only when it sits in the cook chain.
"""
from __future__ import annotations

import argparse
import os
import sys

import hou

TYPE = "mythandlogic::web_export::1.0"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELF = os.path.join(REPO, "tests", "hou", "web_export_tools.shelf")
DEFAULT_OUT = os.path.join(REPO, "houdini", "otls", "web_export.hda")
BADGE = "verdict_badge"
# the script inside the asset is two lines and calls the module, so no logic lives in the HDA
BADGE_CODE = """import hou
from webexport import hou_node
hou_node.badge(hou.pwd())
"""

WEB_SETTINGS = (
    # name, label, kind, default
    ("web_title", "Title", "string", ""),
    ("web_camera", "Camera", "cam", ""),
    ("web_hudvs", "HUD Variant Sets", "string", ""),
    ("web_showproxy", "Show Proxy", "toggle", False),
    ("web_bgblur", "Background Blur", "float", 0.35),
    ("web_exposure", "Exposure Offset", "float", 0.0),
    ("web_showhud", "Show HUD", "toggle", True),
)


def lop_network():
    stage = hou.node("/stage")
    if isinstance(stage, hou.LopNetwork):
        return stage
    return hou.node("/obj").createNode("lopnet", "webexport_build", exact_type_name=True)


def build_contents(sub):
    """indirect input -> badge (in the cook chain) -> output0."""
    out = sub.node("output0") or sub.createNode("output", "output0", exact_type_name=True)
    out.parm("outputidx").set(0)
    # The badge is a Python Script LOP in the cook chain: it shows the verdict as a node
    # message, and fails the cook on FIX FIRST. Joe's decision of 2026-11-11
    # (fix-first-badge). The script is two lines and calls webexport.hou_node, so no logic
    # lives in the HDA.
    badge = sub.createNode("pythonscript", BADGE, exact_type_name=True)
    badge.parm("python").set(BADGE_CODE)
    badge.setInput(0, sub.indirectInputs()[0])
    out.setInput(0, badge)
    return badge


def parm_group():
    """The whole interface, built once in memory and set on the definition in one call."""
    ptg = hou.ParmTemplateGroup()

    formats = hou.FolderParmTemplate("formats", "Formats", folder_type=hou.folderType.Simple)
    formats.addParmTemplate(hou.ToggleParmTemplate("fmt_usd", "USD Folder", default_value=True,
                                                  help="One self-contained folder: layers, textures, /WebSettings."))
    formats.addParmTemplate(hou.ToggleParmTemplate(
        "fmt_gltf", "glTF", default_value=True,
        help="One .glb: meshes, PBR, textures, camera, punctual lights, GPU instancing."))
    for name, label in (("fmt_x3d", "X3D"), ("fmt_usdz", "USDZ")):
        t = hou.ToggleParmTemplate(name, label, default_value=False, is_hidden=False,
                                   help="Not written yet. Reported as unavailable, never silently skipped.")
        t.setConditional(hou.parmCondType.DisableWhen, "{ 0 }")  # always disabled, visibly
        formats.addParmTemplate(t)
    ptg.append(formats)

    ptg.append(hou.StringParmTemplate("outdir", "Output Folder", 1,
                                      default_value=("$HIP/web_export",),
                                      string_type=hou.stringParmType.FileReference,
                                      file_type=hou.fileType.Directory,
                                      tags={"filechooser_mode": "write"}))

    ws = hou.FolderParmTemplate("websettings", "Web Settings", folder_type=hou.folderType.Simple)
    for name, label, kind, default in WEB_SETTINGS:
        if kind == "toggle":
            ws.addParmTemplate(hou.ToggleParmTemplate(name, label, default_value=default))
        elif kind == "float":
            ws.addParmTemplate(hou.FloatParmTemplate(name, label, 1, default_value=(default,),
                                                     min=-5.0, max=5.0))
        elif kind == "cam":
            ws.addParmTemplate(hou.StringParmTemplate(
                name, label, 1, default_value=(default,),
                tags={"sidefx::usdpathtype": "prim",
                      "script_action": "import loputils\nloputils.selectPrimsInParm(kwargs, False)",
                      "script_action_icon": "BUTTONS_reselect"}))
        else:
            ws.addParmTemplate(hou.StringParmTemplate(name, label, 1, default_value=(default,)))
    ws.addParmTemplate(hou.ToggleParmTemplate(
        "web_hudvs_limit", "Limit HUD Variant Sets", default_value=False,
        help="Off: every variant set gets buttons. On: only the ones listed above."))
    ptg.append(ws)

    report = hou.FolderParmTemplate("report_folder", "Report", folder_type=hou.folderType.Simple)
    report.addParmTemplate(hou.StringParmTemplate("report", "Report", 1, default_value=("",),
                                                  tags={"editor": "1", "editorlines": "10-40"}))
    for name in ("check_verdict", "check_enable"):
        report.addParmTemplate(hou.IntParmTemplate(name, name, 1, default_value=(0,), is_hidden=True))
    report.addParmTemplate(hou.StringParmTemplate("check_message", "check_message", 1,
                                                  default_value=("",), is_hidden=True))
    for name, label in (("check", "Check"), ("export", "Export"), ("preview", "Preview")):
        report.addParmTemplate(hou.ButtonParmTemplate(
            name, label, join_with_next=(name != "preview"),
            script_callback=f"__import__('webexport.hou_node').hou_node.on_{name}(kwargs)",
            script_callback_language=hou.scriptLanguage.Python))
    ptg.append(report)
    return ptg


def build(out_path, keep_open=False):
    net = lop_network()
    for stale in ("web_export", "web_export_build"):
        old = net.node(stale)
        if old:
            old.destroy()
    sub = net.createNode("subnet", "web_export", exact_type_name=True)
    build_contents(sub)

    if os.path.exists(out_path):
        hou.hda.uninstallFile(out_path)
        os.remove(out_path)
    node = sub.createDigitalAsset(name=TYPE, hda_file_name=out_path, description="Web Export",
                                  min_num_inputs=1, max_num_inputs=1, create_backup=False)
    definition = node.type().definition()
    if not definition:
        raise RuntimeError("createDigitalAsset produced no definition")
    definition.enableCreateBackups(False)

    definition.setParmTemplateGroup(parm_group(), rename_conflicting_parms=False, create_backup=False)
    with open(SHELF, encoding="utf-8") as f:
        definition.addSection("Tools.shelf", f.read())
    definition.addSection("MessageNodes", BADGE)
    definition.addSection("Help", HELP)
    definition.setUserInfo(f"Built by scripts/build_hda.py from {os.path.basename(REPO)}; "
                           f"houdini {hou.applicationVersionString()}")
    definition.save(definition.libraryFilePath(), template_node=node, create_backup=False)

    readback = {
        "type": str(node.type().name()),
        "path": out_path,
        "sections": sorted(definition.sections().keys()),
        "parms": [t.name() for t in definition.parmTemplateGroup().entries()],
        "tools": list(definition.tools().keys()),
        "inputs": (definition.minNumInputs(), definition.maxNumInputs()),
        "backups": [f for f in os.listdir(os.path.dirname(out_path)) if "backup" in f.lower()],
    }
    if not keep_open:
        node.destroy()
    return readback


HELP = """= Web Export =

Write a Solaris stage to a self-contained folder for the web, and report what will not
survive the trip before anything is written.

* **Check** runs the loss report and writes nothing.
* **Export** writes the folder when nothing blocks.
* **Preview** starts the repo's viewer server on port 5180 and opens it.

The report is the product: a FIX FIRST verdict means the folder would not load or would
look wrong in the browser. The census card is in the repo at docs/census.md.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build houdini/otls/web_export.hda")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--keep-open", action="store_true", help="leave the built node in the session")
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    info = build(a.out, a.keep_open)
    import json
    print(json.dumps(info, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
