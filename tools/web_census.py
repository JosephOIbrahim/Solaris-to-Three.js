"""
web_census.py: can this Solaris export go to the web?

Reads the root USD layer your USD ROP wrote and answers one question: will it load
and look right in the browser viewer (OpenUSD in WASM + three.js)?

    hython web_census.py C:/path/to/export/shot.usda           (Houdini's Python has pxr)
    python web_census.py path/to/shot.usda --json census.json  (any Python with usd-core)

Three kinds of work, kept apart on purpose:
  facts     USD tells us (shader IDs, texture paths, lights, layers, UVs).
  decisions code makes (the verdict and every rule below).
  judgment  Jev (TypeSafe) gives, for one narrow question only: is a shader node
            code doesn't recognise portable MaterialX, or renderer-only?
Set TYPESAFE_API_KEY to use Jev. Without a key the census still runs and those
nodes are marked "needs your eyes". Nothing is sent anywhere else.
"""
import argparse, json, os, re, sys
from pathlib import Path

import jev  # tools/jev.py: the shared TypeSafe door

from pxr import Sdf, Usd, UsdGeom, UsdLux, UsdShade

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


def is_absolute(p):
    return os.path.isabs(p) or bool(re.match(r"^[A-Za-z]:[\\/]", p)) or p.startswith(("\\\\", "/"))


def _norm(p):
    return os.path.normcase(os.path.realpath(p))


def inside(p, folder):
    """True if path p sits in folder or below it (folder already normalised)."""
    p = _norm(p)
    return p == folder or p.startswith(folder.rstrip(os.sep) + os.sep)


def asset_location(attr, ap):
    """Where an asset path points on disk: USD's resolved path, or, when it doesn't resolve
    (a missing file or a <UDIM> pattern), the path anchored to the layer that authored it."""
    if ap.resolvedPath:
        return ap.resolvedPath
    for spec in attr.GetPropertyStack(Usd.TimeCode.Default()):
        if spec.layer and not spec.layer.anonymous and spec.layer.realPath:
            where = spec.layer.ComputeAbsolutePath(ap.path)
            # A bare "tex/a.png" is a USD search path and comes back unanchored; anchor it to the layer.
            if not os.path.isabs(where):
                where = os.path.join(os.path.dirname(spec.layer.realPath), where)
            return where
    return None


def census(path):
    stage = Usd.Stage.Open(str(path))
    if not stage:
        sys.exit(f"Could not open {path}")
    # The export folder is the root layer's folder. The server can only reach files under it,
    # so a layer or texture outside it (a "../" path) loads in Houdini and 404s in the browser.
    root_dir = _norm(os.path.dirname(stage.GetRootLayer().realPath))
    outside = []
    shaders, textures, lights, untested = {}, [], {}, {}
    no_uv, unbound, purposes, prims = [], [], {"proxy": 0, "guide": 0}, 0

    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        prims += 1
        t = prim.GetTypeName()
        if t in UNTESTED_TYPES:
            untested.setdefault(t, []).append(str(prim.GetPath()))
        if prim.IsA(UsdGeom.Imageable):
            p = UsdGeom.Imageable(prim).ComputePurpose()
            if p in purposes:
                purposes[p] += 1
        if prim.IsA(UsdShade.Shader):
            sh = UsdShade.Shader(prim)
            sid = sh.GetShaderId() or ("sourceAsset" if sh.GetImplementationSource() == "sourceAsset" else "(none)")
            e = shaders.setdefault(sid, {"count": 0, "example": str(prim.GetPath()),
                                         "inputs": sorted(i.GetBaseName() for i in sh.GetInputs())[:24]})
            e["count"] += 1
        if prim.HasAPI(UsdLux.LightAPI) or t.endswith("Light"):
            lights.setdefault(t, []).append(str(prim.GetPath()))
        if prim.IsA(UsdShade.Shader) or prim.HasAPI(UsdLux.LightAPI):
            for a in prim.GetAttributes():
                if a.GetTypeName() not in (Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray):
                    continue
                v = a.Get()
                for ap in ([v] if isinstance(v, Sdf.AssetPath) else list(v or [])):
                    if isinstance(ap, Sdf.AssetPath) and ap.path:
                        textures.append({"path": ap.path, "resolved": bool(ap.resolvedPath), "prim": str(prim.GetPath()),
                                         "dome": t.startswith("DomeLight"), "ext": os.path.splitext(ap.path)[1].lower()})
                        # Absolute and $VAR paths have their own fix-first line; don't count them twice.
                        if not is_absolute(ap.path) and "$" not in ap.path:
                            where = asset_location(a, ap)
                            if where and not inside(where, root_dir):
                                outside.append(ap.path)
        if prim.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(prim).ComputePurpose() in ("default", "render"):
            mats = [UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]]
            mats += [UsdShade.MaterialBindingAPI(s.GetPrim()).ComputeBoundMaterial()[0]
                     for s in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(prim))]
            mats = [m for m in mats if m]
            if not mats:
                unbound.append(str(prim.GetPath()))
            elif any(m.GetSurfaceOutput("mtlx") for m in mats):
                pv = UsdGeom.PrimvarsAPI(prim).GetPrimvars()
                if not any(p.GetTypeName() in (Sdf.ValueTypeNames.TexCoord2fArray, Sdf.ValueTypeNames.Float2Array) for p in pv):
                    no_uv.append(str(prim.GetPath()))

    layers = {}
    for layer in stage.GetUsedLayers():
        if layer.anonymous:
            continue
        ext = os.path.splitext(layer.identifier.split(":SDF_FORMAT_ARGS:")[0])[1].lower()
        layers.setdefault(ext, []).append(layer.identifier)
        if layer.realPath and not inside(layer.realPath, root_dir):
            try:  # shown as written on disk (realpath, not normcase, which lowercases on Windows)
                outside.append(os.path.relpath(os.path.realpath(layer.realPath),
                                               os.path.realpath(os.path.dirname(stage.GetRootLayer().realPath)))
                               .replace(os.sep, "/"))
            except ValueError:  # another drive on Windows: no relative path exists
                outside.append(layer.realPath)
    errors = list(getattr(stage, "GetCompositionErrors", lambda: [])())
    return {"stage": stage, "shaders": shaders, "textures": textures, "lights": lights, "untested": untested,
            "no_uv": no_uv, "unbound": unbound, "purposes": purposes, "prims": prims, "layers": layers,
            "outside": sorted(set(outside)), "errors": [str(e) for e in errors]}


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
    return {s: (answers[f"n{i}"]["choice"], float(answers[f"n{i}"]["confidence"])) for i, s in enumerate(ids)}, note


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
        elif not choice and s.startswith("ND_"):
            portable.append(s)          # no Jev: standard MaterialX naming, unchecked
        else:
            unsure.append(s if not choice else f"{s} ({choice} {conf:.2f})")
    add(amber, "renderer-only nodes (fall back to grey)", renderer)
    add(amber, "shader nodes that need your eyes", unsure)
    if portable:
        info.append((f"untested MaterialX nodes, {'Jev says portable' if calls else 'unchecked'}", len(portable), portable[0]))
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
