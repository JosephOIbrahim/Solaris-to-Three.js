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
    feeder = parent.createNode("createxform", "feeder", exact_type_name=True)
    if feeder.parm("primpath"):
        feeder.parm("primpath").set("/feeder")
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
    parent = net()
    inst, definition = build_chain(parent, a.hda)
    inst.parm("outdir").set(out_dir)

    result = {"houdini": hou.applicationVersionString(), "hda": a.hda, "out_dir": out_dir,
              "definition": {"type": definition.nodeTypeName(),
                             "library": definition.libraryFilePath(),
                             "sections": sorted(definition.sections().keys())}}

    inst.parm("check").pressButton()
    result["check"] = read(inst)

    inst.parm("export").pressButton()
    result["export"] = read(inst)
    result["files"] = sorted(os.path.relpath(os.path.join(r, f), out_dir)
                             for r, _d, fs in os.walk(out_dir) for f in fs)

    verdict = None
    for line in (result["export"]["report"] or "").splitlines():
        for candidate in ("FIX FIRST", "GO, WITH NOTES", "GO"):
            if line.strip().startswith(candidate):
                verdict = candidate
                break
        if verdict:
            break
    result["verdict"] = verdict

    text = json.dumps(result, indent=1)
    if a.json:
        open(a.json, "w", encoding="utf-8").write(text)
    print(text)

    if result["check"]["report"] and result["check"]["report"].startswith("Check: nothing"):
        return 2
    if result["files"] and not result["verdict"]:
        return 3
    if a.expect and result["verdict"] != a.expect:
        print(f"EXPECTED {a.expect}, GOT {result['verdict']}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
