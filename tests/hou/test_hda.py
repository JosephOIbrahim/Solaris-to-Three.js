"""Press the Web Export HDA's buttons under hython, on a real LOP network.

    "C:/Program Files/Side Effects Software/Houdini 22.0.<build>/bin/hython.exe" -B \
        tests/hou/test_hda.py [--hda houdini/otls/web_export.hda] [--expect "GO"] [--json out.json]

It installs the asset from the repo, builds the fixture chain (an empty feeder into the
asset, so the asset's own Check reads a composed stage), presses Check and Export, and
reports what the node said. The same script headless is the batch path, so a pass here
also proves `webexport` works under hython.

`--expect` fails the run when the verdict of the last report differs, so the build can
gate on it; without it the run only reports.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import hou

TYPE = "mythandlogic::web_export::1.0"


def net():
    stage = hou.node("/stage")
    if isinstance(stage, hou.LopNetwork):
        return stage
    return hou.node("/obj").createNode("lopnet", "webexport_test", exact_type_name=True)


def build_chain(parent, hda_path):
    hou.hda.installFile(hda_path, force_use_assets=True)
    definition = hou.hda.definitionsInFile(hda_path)[0]
    inst = parent.createNode(TYPE, "web_export", exact_type_name=True)
    # the fixture stage, so the export has layers, a payload and a texture to prove
    feeder = parent.createNode("sublayer", "shot", exact_type_name=True)
    feeder.parm("filepath1").set(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "fixtures", "stage", "shot.usda"))
    inst.setInput(0, feeder)
    return inst, definition


def read(node):
    report = node.parm("report").unexpandedString() if node.parm("report") else ""
    return {
        "report": report,
        "verdict_code": node.parm("check_verdict").eval() if node.parm("check_verdict") else None,
        "message": node.parm("check_message").evalAsString() if node.parm("check_message") else "",
        "warnings": list(node.warnings()),
        "errors": list(node.errors()),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--hda", default=os.path.join(repo, "houdini", "otls", "web_export.hda"))
    ap.add_argument("--expect", default=None, help='fail unless the report ends in this verdict, e.g. "GO"')
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    os.environ.setdefault("WEBEXPORT_NO_BROWSER", "1")
    out_dir = tempfile.mkdtemp(prefix="webexport_hda_")
    try:
        parent = net()
        inst, definition = build_chain(parent, a.hda)
        inst.parm("outdir").set(out_dir)

        result = {"houdini": hou.applicationVersionString(), "hda": a.hda, "out_dir": out_dir,
                  "definition": {"type": definition.nodeTypeName(),
                                 "library": definition.libraryFilePath(),
                                 "sections": sorted(definition.sections().keys())}}

        inst.parm("check").pressButton()
        result["check"] = read(inst)
        # the badge reports during a cook, so cook and read what the asset says on its own node
        try:
            inst.cook(force=True)
            result["cookAfterCheck"] = "ok"
        except Exception as exc:
            result["cookAfterCheck"] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
        result["check"]["errorsAfterCook"] = list(inst.errors())
        result["check"]["warningsAfterCook"] = list(inst.warnings())

        inst.parm("export").pressButton()
        result["export"] = read(inst)
        result["files"] = sorted(os.path.relpath(os.path.join(r, f), out_dir)
                                 for r, _d, fs in os.walk(out_dir) for f in fs)

        # the worst verdict across every format, read from the node's own parm rather than from text:
        # scanning text picks up the first format's verdict and hides a later format's FIX FIRST
        codes = {0: "GO", 1: "GO, WITH NOTES", 2: "FIX FIRST"}
        rank = {v: k for k, v in codes.items()}
        result["verdict"] = codes.get(result["export"]["verdict_code"]) if result["export"]["report"] else None
        result["verdicts_by_format"] = [line.strip() for line in (result["export"]["report"] or "").splitlines()
                                        if line.strip().startswith(("FIX FIRST", "GO, WITH NOTES", "GO"))]

        want = {"fmt_usd": ".usda", "fmt_gltf": ".glb", "fmt_x3d": ".x3d", "fmt_usdz": ".usdz"}
        missing_parms = sorted(parm for parm in want if not inst.parm(parm))
        on = {ext for parm, ext in want.items() if inst.parm(parm) and inst.parm(parm).eval()}
        have = {Path(f).suffix.lower() for f in result["files"]}
        missing = sorted(on - have)
        result["formats_ticked"] = sorted(on)
        result["formats_written"] = sorted(have & on)

        text = json.dumps(result, indent=1)
        if a.json:
            with open(a.json, "w", encoding="utf-8") as fh:
                fh.write(text)
        print(text)

        if result["check"]["report"] and result["check"]["report"].startswith("Check: nothing"):
            return 2
        if not result["export"]["report"] or not result["verdict"]:
            print("export produced an empty report or missing verdict", file=sys.stderr)
            return 3
        if missing_parms or len(on) != len(want):
            print(f"EXPECTED all {len(want)} format toggles on; missing_parms={missing_parms}, on={sorted(on)}",
                  file=sys.stderr)
            return 8
        worst_from_text = max(
            (next(v for v in ("FIX FIRST", "GO, WITH NOTES", "GO") if line.startswith(v))
             for line in result["verdicts_by_format"]),
            key=lambda v: rank[v],
            default=None,
        )
        if worst_from_text != result["verdict"]:
            print(f"VERDICT MISMATCH: check_verdict={result['verdict']!r}, report={worst_from_text!r}",
                  file=sys.stderr)
            return 9
        if a.expect and result["verdict"] != a.expect:
            print(f"EXPECTED {a.expect}, GOT {result['verdict']}", file=sys.stderr)
            return 4

        # The formats that are ticked must each land a file. Without this, a disabled or broken
        # writer passes as long as the remaining ones export, which is how a format can silently
        # stop shipping.
        if missing:
            print(f"MISSING output for ticked format(s): {missing}", file=sys.stderr)
            return 5
        if result["check"].get("errorsAfterCook"):
            print("the badge reported an error on a non-blocking export", file=sys.stderr)
            return 6
        if result["verdict"] == "FIX FIRST":
            print("the export reported FIX FIRST; nothing should have been written", file=sys.stderr)
            return 7
        return 0
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
