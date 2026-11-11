"""Probe the Houdini API questions that the M5 HDA build depends on.

Run it with hython, one build at a time:

    "C:/Program Files/Side Effects Software/Houdini 22.0.400/bin/hython.exe" -B \
        tests/hou/probe_hda_api.py --json probe_400.json

It builds throwaway LOP subnets and HDAs in the system temp folder and prints one JSON
object of answers. It writes nothing in the repo and nothing in Houdini's prefs. The
question numbers are those in `.token-saver/m5-api-brief.md` ("Open questions for the
first hython run").

Every probe is wrapped, so one failure records its exception and the rest still run.
Set PROBE_TRACE=1 for tracebacks.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import hou

ANSWERS = {}
TMP = tempfile.mkdtemp(prefix="webexport_probe_")
TYPE = "mythandlogic::web_export::1.0"


def probe(key, fn):
    try:
        ANSWERS[key] = fn()
    except Exception as e:
        ANSWERS[key] = {"error": f"{type(e).__name__}: {e}"}
        if os.environ.get("PROBE_TRACE"):
            ANSWERS[key]["trace"] = traceback.format_exc()


def net():
    """A LOP network to build in: /stage if it is one, else a new lopnet under /obj."""
    stage = hou.node("/stage")
    if isinstance(stage, hou.LopNetwork):
        return stage
    return hou.node("/obj").createNode("lopnet", "webexport_probe", exact_type_name=True)


def subnet(parent, name):
    return parent.createNode("subnet", name, exact_type_name=True)


def chain(sub, fixture):
    """indirect input -> sublayer -> error badge -> output0, the M5 test chain."""
    sl = sub.createNode("sublayer", "shot", exact_type_name=True)
    sl.parm("filepath1").set(fixture)
    badge = sub.createNode("error", "verdict_badge", exact_type_name=True)
    sl.setInput(0, sub.indirectInputs()[0])
    badge.setInput(0, sl)
    out = sub.node("output0") or sub.createNode("output", "output0", exact_type_name=True)
    out.setInput(0, badge)
    if out.parm("outputidx"):
        out.parm("outputidx").set(0)
    return sl, badge


def build(node, path, **kw):
    args = dict(name=TYPE, hda_file_name=path, description="Web Export",
                min_num_inputs=1, max_num_inputs=1, create_backup=False)
    args.update(kw)
    return node.createDigitalAsset(**args)


def main():
    root = hou.getenv("WEBEXPORT_ROOT") or os.getcwd()
    fixture = os.path.join(root, "fixtures", "stage", "shot.usda")

    # ---- environment (1, 2, 5, 6) --------------------------------------------------
    probe("q1_env", lambda: {
        "version": hou.applicationVersionString(),
        "HOUDINI_USER_PREF_DIR": hou.getenv("HOUDINI_USER_PREF_DIR"),
        "homeHoudiniDirectory": hou.homeHoudiniDirectory(),
        "HOME": os.environ.get("HOME"),
        "isUI": hou.isUIAvailable(),
    })
    probe("q2_import", lambda: {"WEBEXPORT_ROOT": hou.getenv("WEBEXPORT_ROOT"),
                                "__file__": __import__("webexport").__file__})
    probe("q5_hda", lambda: {"loaded": [f for f in hou.hda.loadedFiles() if "web_export" in f],
                             "otlscan": hou.houdiniPath("HOUDINI_OTLSCAN_PATH")})
    probe("q6_license", lambda: {"category": str(hou.licenseCategory()),
                                 "ext": hou.hda.defaultFileExtension()})

    # ---- the LOP network (7, 8, 23) ------------------------------------------------
    probe("q7_stage", lambda: {"isLopNetwork": isinstance(hou.node("/stage"), hou.LopNetwork),
                               "hasStage": hasattr(hou.node("/stage"), "stage")})
    hou.hipFile.clear(suppress_save_prompt=True)
    probe("q7_after_clear", lambda: {"isLopNetwork": isinstance(hou.node("/stage"), hou.LopNetwork)})

    parent = net()
    sub = subnet(parent, "web_export")
    probe("q8_subnet", lambda: {
        "type": sub.type().name(),
        "children": [(c.name(), c.type().name()) for c in sub.children()],
        "indirectInputs": [n.name() for n in sub.indirectInputs()],
        "subnetOutputs": [n.name() for n in sub.subnetOutputs()],
    })
    sl, badge = chain(sub, fixture)
    probe("q23_sublayer", lambda: {"type": sl.type().name(),
                                   "fileParms": sorted(p.name() for p in sl.parms() if "file" in p.name())})
    probe("q23_error", lambda: {
        "type": badge.type().name(),
        "parms": sorted(p.name() for p in badge.parms()),
        "severityMenu": list(badge.parm("severity1").parmTemplate().menuItems()) if badge.parm("severity1") else None,
    })

    # ---- composing the stage (33, 34, 35) ------------------------------------------
    def q33():
        from pxr import UsdGeom
        st = sl.stage()
        rl = st.GetRootLayer()
        return {"root": rl.identifier, "anonymous": rl.anonymous,
                "defaultPrim": str(st.GetDefaultPrim()), "upAxis": str(UsdGeom.GetStageUpAxis(st)),
                "metersPerUnit": UsdGeom.GetStageMetersPerUnit(st),
                "start": st.GetStartTimeCode(), "end": st.GetEndTimeCode(),
                "sublayers": list(rl.subLayerPaths),
                "layerStack": [l.identifier for l in st.GetLayerStack()],
                "prims": [str(p.GetPath()) for p in st.Traverse()][:12]}
    probe("q33_stage", q33)

    def q34():
        from pxr import Sdf, Usd, UsdUtils
        calls = []

        def resolve(layer, path):
            calls.append(path)
            return UsdUtils.FlattenLayerStackResolveAssetPath(layer, path)
        flat = UsdUtils.FlattenLayerStack(sl.stage(), resolve, "probe_flat.usda")
        st = Usd.Stage.Open(flat)
        return {"resolvedCalls": len(calls), "sample": calls[:3], "root": flat.identifier,
                "hasHoudiniLayerInfo": any(str(p.GetPath()) == "/HoudiniLayerInfo" for p in st.Traverse()),
                "sublayers": list(flat.subLayerPaths)}
    probe("q34_flatten", q34)

    # ---- building the HDA (9, 10, 12, 13, 14, 19 to 21, 24, 25, 27, 11) ------------
    path = os.path.join(TMP, "web_export.hda")
    created = build(sub, path)   # converts the subnet; the old node object is now deleted
    sub = created                # closures below read this re-bound name
    probe("q9_create", lambda: {"type": str(sub.type().name()), "exists": os.path.exists(path)})
    d = sub.type().definition()
    probe("q9_definition", lambda: {"sections": sorted(d.sections().keys()),
                                    "parms": [t.name() for t in d.parmTemplateGroup().entries()]})
    probe("q9_tools_present", lambda: "Tools.shelf" in d.sections())

    def q13_14():
        d.enableCreateBackups(False)
        ptg = hou.ParmTemplateGroup()
        ptg.append(hou.StringParmTemplate("outdir", "Output Folder", 1, default_value=("$HIP/web_export",),
                                          string_type=hou.stringParmType.FileReference,
                                          file_type=hou.fileType.Directory,
                                          tags={"filechooser_mode": "write"}))
        ptg.append(hou.StringParmTemplate("report", "Report", 1, default_value=("",),
                                          tags={"editor": "1", "editorlines": "8-40"}))
        ptg.append(hou.ToggleParmTemplate("report_lock", "Lock", is_hidden=True))
        ptg.append(hou.ButtonParmTemplate(
            "check", "Check",
            script_callback="kwargs['node'].parm('report').set('ran:' + str(kwargs.get('webexport_no_browser')))",
            script_callback_language=hou.scriptLanguage.Python))
        d.setParmTemplateGroup(ptg, rename_conflicting_parms=False, create_backup=False)
        got = d.parmTemplateGroup()
        ds = got.asDialogScript()
        return {"isDirectory": got.find("outdir").fileType() == hou.fileType.Directory,
                "dialogTypeDirectory": ("type    directory" in ds) or ("type\tdirectory" in ds),
                "editorTag": got.find("report").tags().get("editor"),
                "callback": got.find("check").scriptCallback()[:40],
                "callbackLang": str(got.find("check").scriptCallbackLanguage()),
                "editorInDialog": "editorlines" in ds}
    probe("q13_14_parms", q13_14)

    def q10_tools():
        # the shelf lives in tests/hou/web_export_tools.shelf, so no escaping lives in code
        shelf = Path(__file__).with_name("web_export_tools.shelf").read_text(encoding="utf-8")
        d.addSection("Tools.shelf", shelf)
        before = list(d.tools().keys())
        d.save(d.libraryFilePath(), template_node=sub, create_backup=False)
        hou.hda.reloadFile(d.libraryFilePath())
        again = hou.hda.definitionsInFile(d.libraryFilePath())[0]
        tools = again.tools()
        return {"beforeSave": before, "afterReload": list(tools.keys()),
                "labels": [x.label() for x in tools.values()],
                "locations": [list(x.toolMenuLocations()) for x in tools.values()]}
    probe("q10_tools", q10_tools)

    def q12():
        d.addSection("MessageNodes", "verdict_badge")
        d.save(d.libraryFilePath(), template_node=sub, create_backup=False)
        here = os.path.dirname(d.libraryFilePath())
        return {"backups": [f for f in os.listdir(here) if "backup" in f.lower()],
                "createBackups": d.isCreateBackupsEnabled(),
                "sections": sorted(d.sections().keys())}
    probe("q12_backups", q12)

    def q19_21():
        inst = parent.createNode(TYPE, "we_inst", exact_type_name=True)
        # an empty-stage feeder: a second sublayer of the same fixture would be a duplicate
        feeder = parent.createNode("createxform", "feeder", exact_type_name=True)
        feeder.parm("primpath").set("/feeder") if feeder.parm("primpath") else None
        inst.setInput(0, feeder)
        out = {"parms": [p.name() for p in inst.parms()][:10]}
        inst.parm("check").pressButton()
        out["plain"] = inst.parm("report").unexpandedString()
        inst.parm("check").pressButton(arguments={"webexport_no_browser": 1})
        out["withArgs"] = inst.parm("report").unexpandedString()
        return out
    probe("q19_21_press", q19_21)

    def cook_with_errors(node):
        """cook, and keep the badge text instead of letting the exception hide it."""
        try:
            node.cook(force=True)
            return {"cook": "ok", "warnings": list(node.warnings()), "errors": list(node.errors())}
        except Exception as e:
            return {"cook": f"{type(e).__name__}: {e}", "warnings": list(node.warnings()),
                    "errors": list(node.errors())}

    def q24_badge():
        inst = parent.node("we_inst")
        inst.allowEditingOfContents()          # an HDA locks its contents when it is created
        inner = inst.node("verdict_badge")
        quiet = cook_with_errors(inst)
        inner.parm("enable1").set(1)
        inner.parm("severity1").set(1)         # warn
        inner.parm("errormsg1").set("badge warning text")
        quiet["warningSeverity"] = cook_with_errors(inst)
        return quiet
    probe("q24_badge", q24_badge)

    def q25_offchain():
        # a MessageNodes-listed node off the output chain: does it cook, and does its text show?
        inst = parent.node("we_inst")
        inst.allowEditingOfContents()
        side = inst.createNode("error", "side_badge", exact_type_name=True)
        side.parm("enable1").set(1)
        side.parm("severity1").set(1)
        side.parm("errormsg1").set("off-chain badge text")
        d.addSection("MessageNodes", "verdict_badge side_badge")
        inst.matchCurrentDefinition()          # relock, saving the edited contents back
        return cook_with_errors(inst)
    probe("q25_offchain", q25_offchain)

    def q27():
        inst = parent.node("we_inst")
        inst.allowEditingOfContents()
        inner = inst.node("verdict_badge")
        inner.parm("severity1").set(2)         # error
        inner.parm("errormsg1").set("blocked on purpose")
        r = cook_with_errors(inst)
        r["innerErrors"] = list(inner.errors())
        r["innerWarnings"] = list(inner.warnings())
        try:
            st = inst.stage()
            r["stage"] = "None" if st is None else "a stage"
        except Exception as e:
            r["raised"] = f"{type(e).__name__}: {e}"
        return r
    probe("q27_error_stage", q27)

    def q11():
        ok_uninstall = hou.hda.uninstallFile(path)
        try:
            os.remove(path)
            removed = not os.path.exists(path)
        except OSError as e:
            removed = f"{type(e).__name__}: {e}"
        return {"uninstall": ok_uninstall, "removed": removed}
    probe("q11_uninstall_remove", q11)


if __name__ == "__main__":
    main()
    text = json.dumps(ANSWERS, indent=1, default=str)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
