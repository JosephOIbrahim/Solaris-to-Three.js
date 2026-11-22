"""
web_census.py: can this Solaris export go to the web?

Reads the root USD layer your USD ROP wrote and answers one question: will it load
and look right in the browser viewer (OpenUSD in WASM + three.js)?

    hython web_census.py C:/path/to/export/shot.usda           (Houdini's Python has pxr)
    python web_census.py path/to/shot.usda --json census.json  (any Python with usd-core)

Three kinds of work, kept apart on purpose:
  facts     USD tells us (shader IDs, texture paths, lights, layers, UVs), gathered by
            python/webexport/inventory.py, the one place the census and the Web Export read the stage.
  decisions code makes (the verdict and every rule below).
  judgment  Jev (TypeSafe) gives, for one narrow question only: is a shader node
            code doesn't recognise portable MaterialX, or renderer-only?
Set TYPESAFE_API_KEY to use Jev. Without a key the census still runs and those
nodes are marked "needs your eyes". Nothing is sent anywhere else.
"""
import argparse, json, re, sys
from pathlib import Path

import jev  # tools/jev.py: the shared TypeSafe door

# The facts live in python/webexport/. Nothing installs that package for a plain `python` or for
# hython, so the census puts the repo's python/ folder on the path itself.
_PYTHON = str(Path(__file__).resolve().parents[1] / "python")
if _PYTHON not in sys.path:
    sys.path.insert(0, _PYTHON)

from pxr import Usd, UsdGeom  # noqa: E402
from webexport.inventory import _norm, asset_location, gather, inside, is_absolute  # noqa: E402,F401

# ---- rules: what the viewer is known to handle (tested 2026-10-30) ------------------
TESTED_SHADERS = {"UsdPreviewSurface", "ND_standard_surface_surfaceshader", "ND_open_pbr_surface_surfaceshader"}
PREVIEW_FAMILY = re.compile(r"^(UsdPreviewSurface|UsdUVTexture|UsdTransform2d|UsdPrimvarReader_\w+|ND_UsdPreviewSurface\w*)$")
WEB_TEXTURES = {".png", ".jpg", ".jpeg", ".tga", ".exr"}       # what Needle's delegate can load
DOME_TEXTURES = {".hdr"}                                        # what the viewer's lighting bridge reads
BRIDGED_LIGHTS = {"RectLight", "SphereLight", "DiskLight", "DistantLight", "DomeLight", "DomeLight_1"}
APPROX_LIGHTS = {"CylinderLight"}                               # becomes a point light
WEB_LAYERS = {".usd", ".usda", ".usdc", ".usdz", ".gltf", ".glb", ".drc"}
UNTESTED_TYPES = {"BasisCurves", "NurbsCurves", "HermiteCurves", "Points", "NurbsPatch", "Volume", "TetMesh"}

JEV_FLOOR = 0.5   # below this confidence Jev's answer is treated as "can't tell" (TypeSafe's suggested starting point)


def census(path):
    """The facts, from webexport.inventory.gather(), under the census's own names."""
    stage = Usd.Stage.Open(str(path))
    if not stage:
        sys.exit(f"Could not open {path}")
    f = gather(stage, track_types=UNTESTED_TYPES)
    return {"stage": stage, "shaders": f.shaders, "textures": f.asset_rows, "lights": f.lights_by_type,
            "untested": f.tracked, "no_uv": f.no_uv, "unbound": f.unbound, "purposes": f.purposes,
            "prims": f.prims, "layers": f.layers, "outside": f.outside, "errors": f.errors}


def ask_jev(nodes):
    """One request, one Choice per unfamiliar node. Returns ({id: (choice, confidence)}, note)."""
    if not nodes:
        return {}, ""
    ids = sorted(nodes)
    state = {"context": "Shader nodes from a USD scene exported by Houdini Solaris. The scene will render in a web "
                        "browser through OpenUSD Hydra, the open-source MaterialX runtime and three.js.",
             "nodes": {f"n{i}": {"id": s, "inputs": nodes[s]["inputs"]} for i, s in enumerate(ids)}}
    criteria = {
        "portable_materialx": "A node from the open MaterialX standard libraries (stdlib, pbrlib, nprlib, Standard Surface "
                              "or OpenPBR) that any MaterialX renderer can compile.",
        "usd_preview": "Part of the UsdPreviewSurface family: UsdPreviewSurface, UsdUVTexture, UsdPrimvarReader, UsdTransform2d.",
        "renderer_specific": "Defined only for one renderer or application (for example Karma, Arnold, RenderMan, Redshift, "
                             "V-Ray, Octane, or a studio library), so a generic MaterialX runtime will not compile it.",
        "cannot_tell": "The identifier and its inputs do not show which library the node comes from.",
    }
    questions = {f"n{i}": {"type": "choice", "criteria": criteria,
                           "instructions": f"Which library does the shader node `nodes.n{i}` come from?"}
                 for i in range(len(ids))}
    answers, note = jev.ask(questions, state)
    if not answers:
        return {}, note
    out = {}
    for i, s in enumerate(ids):
        ans = jev.answer_for(answers, f"n{i}") if hasattr(jev, "answer_for") else (
            answers.get(f"n{i}") if isinstance(answers.get(f"n{i}"), dict) else None
        )
        if ans and ans.get("choice") is not None and ans.get("confidence") is not None:
            out[s] = (ans["choice"], float(ans["confidence"]))
    return out, note


def decide(c, jev_on=True):
    """Code owns the verdict. Each issue: (level, label, count, example)."""
    red, amber, info = [], [], []
    def add(bucket, label, items):
        if items:
            bucket.append((label, len(items), items[0]))

    add(red, "composition errors", c["errors"])
    bad_layers = [i for e, ids in c["layers"].items() if e not in WEB_LAYERS for i in ids]
    add(red, "layer formats the browser can't read", bad_layers)
    tex = c["textures"]
    add(red, "absolute or $VAR asset paths", [t["path"] for t in tex if is_absolute(t["path"]) or "$" in t["path"]])
    add(red, "files outside the export folder (404 on the web)", c["outside"])
    add(red, "UDIM textures (no UDIM support)", [t["path"] for t in tex if "<udim>" in t["path"].lower()])
    add(red, "texture formats the browser can't load", [t["path"] for t in tex if not t["dome"] and "<" not in t["path"]
                                                          and t["ext"] not in WEB_TEXTURES])
    add(red, "missing files", [t["path"] for t in tex if not t["resolved"] and "<" not in t["path"] and not is_absolute(t["path"])])
    add(red, "MaterialX meshes without UVs (render black)", c["no_uv"])
    add(amber, "dome texture not .hdr (the bridge reads .hdr)", [t["path"] for t in tex if t["dome"] and t["ext"] not in DOME_TEXTURES])
    add(amber, "meshes with no material (default grey)", c["unbound"])
    add(amber, "lights approximated as point lights", [p for k, v in c["lights"].items() if k in APPROX_LIGHTS for p in v])
    add(amber, "light types the viewer ignores", [f"{k} {p}" for k, v in c["lights"].items()
                                                   if k not in BRIDGED_LIGHTS | APPROX_LIGHTS for p in v])
    add(amber, "prim types untested in the viewer", [f"{k} {p}" for k, v in c["untested"].items() for p in v])

    unfamiliar = {s: e for s, e in c["shaders"].items() if s not in TESTED_SHADERS and not PREVIEW_FAMILY.match(s)}
    calls, note = ask_jev(unfamiliar) if jev_on else ({}, "Jev off (--no-jev)")
    portable, renderer, unsure = [], [], []
    for s in sorted(unfamiliar):
        choice, conf = calls.get(s, (None, 0.0))
        if choice and conf >= JEV_FLOOR and choice in ("portable_materialx", "usd_preview"):
            portable.append(s)
        elif choice and conf >= JEV_FLOOR and choice == "renderer_specific":
            renderer.append(f"{s} ({conf:.2f})")
        else:
            unsure.append(s if not choice else f"{s} ({choice} {conf:.2f})")
    add(amber, "renderer-only nodes (fall back to grey)", renderer)
    add(amber, "shader nodes that need your eyes", unsure)
    if portable:
        info.append(("untested MaterialX nodes, Jev says portable", len(portable), portable[0]))
    if c["purposes"]["proxy"] or c["purposes"]["guide"]:
        info.append(("proxy/guide prims: Hydra hides, USDLoader shows",
                     c["purposes"]["proxy"] + c["purposes"]["guide"], ""))
    verdict = "FIX FIRST" if red else "GO, WITH NOTES" if amber else "GO"
    return verdict, red, amber, info, note


def report(path, c, verdict, red, amber, info, note):
    W = 72
    s = c["stage"]
    lines = [f"WEB CENSUS   {Path(path).name}", "-" * W]
    lead = {"FIX FIRST": f"{len(red)} problem(s) stop this scene loading or looking right.",
            "GO, WITH NOTES": "It will load. Some things will look different from Karma.",
            "GO": "Nothing found that stops it. Open it in the viewer."}[verdict]
    lines += [f"{verdict:<15}{lead}", ""]
    for title, mark, items in (("FIX FIRST", "x", red), ("WILL LOOK DIFFERENT", "!", amber), ("WORTH KNOWING", "-", info)):
        if items:
            lines.append(title)
            for label, n, ex in items:
                lines.append(f"  {mark}  {label:<52}{n:>4}")
                if ex:
                    lines.append(f"     {str(ex)[:W - 5]}")
            lines.append("")
    lt = "  ".join(f"{k} {len(v)}" for k, v in sorted(c["lights"].items())) or "none"
    ly = "  ".join(f"{e or '(none)'} {len(v)}" for e, v in sorted(c["layers"].items()))
    lines += ["FACTS",
              f"  prims {c['prims']}   shader IDs {len(c['shaders'])}   textures {len(c['textures'])}"
              f"   up {UsdGeom.GetStageUpAxis(s)}   m/unit {UsdGeom.GetStageMetersPerUnit(s):g}",
              f"  lights  {lt}",
              f"  layers  {ly}",
              f"  {note}" if note else "", "-" * W]
    return "\n".join(l for l in lines if l is not None)


def main():
    ap = argparse.ArgumentParser(description="Can this Solaris export go to the web?")
    ap.add_argument("usd", help="root USD layer written by your USD ROP")
    ap.add_argument("--json", help="also write the full census as JSON here")
    ap.add_argument("--no-jev", action="store_true", help="don't ask Jev about unfamiliar shader nodes")
    a = ap.parse_args()
    c = census(Path(a.usd))
    verdict, red, amber, info, note = decide(c, jev_on=not a.no_jev)
    print(report(a.usd, c, verdict, red, amber, info, note))
    if a.json:
        out = {k: v for k, v in c.items() if k != "stage"}
        out.update(verdict=verdict, note=note, fix_first=red, will_look_different=amber, worth_knowing=info)
        Path(a.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
    sys.exit(1 if verdict == "FIX FIRST" else 0)


if __name__ == "__main__":
    main()
