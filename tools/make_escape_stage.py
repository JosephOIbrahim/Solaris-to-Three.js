"""Test fixture for web_census.py: an export that climbs out of its own folder.

Houdini's default "Use Relative Paths" output processor writes "../" paths for anything
saved or read outside the USD ROP's output folder. They load in Houdini and 404 in the
browser, because only the export folder is copied to stages/<name>/.

    fixtures/stage-escape/
      export/shot.usda         root layer (the "export folder" is export/)
      export/parts/inside.usda inside: fine
      export/tex/ok.png        inside: fine
      lib/asset.usda           outside: referenced as ../lib/asset.usda
      lib/wood.png             outside: texture ../lib/wood.png
      lib/skin.1001.png        outside: UDIM ../lib/skin.<UDIM>.png
      (tex/missing.png)        inside but missing, written without ./ : a missing file, not outside

Run: python tools/make_escape_stage.py   (needs usd-core, or hython)
"""
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade, Vt

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "stage-escape"
EXPORT, LIB = OUT / "export", OUT / "lib"
for d in (EXPORT / "parts", EXPORT / "tex", LIB):
    d.mkdir(parents=True, exist_ok=True)
PNG = b"\x89PNG\r\n\x1a\n"
for f in (EXPORT / "tex" / "ok.png", LIB / "wood.png", LIB / "skin.1001.png"):
    f.write_bytes(PNG)
T = Sdf.ValueTypeNames


def leaf_layer(path, root):
    s = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(s, UsdGeom.Tokens.y)
    s.SetDefaultPrim(UsdGeom.Xform.Define(s, root).GetPrim())
    s.GetRootLayer().Save()


leaf_layer(LIB / "asset.usda", "/asset")
leaf_layer(EXPORT / "parts" / "inside.usda", "/inside")

s = Usd.Stage.CreateNew(str(EXPORT / "shot.usda"))
UsdGeom.SetStageUpAxis(s, UsdGeom.Tokens.y)
UsdGeom.SetStageMetersPerUnit(s, 1.0)
s.SetDefaultPrim(UsdGeom.Xform.Define(s, "/scene").GetPrim())
s.DefinePrim("/scene/asset", "Xform").GetReferences().AddReference("../lib/asset.usda")     # outside
s.DefinePrim("/scene/inside", "Xform").GetReferences().AddReference("./parts/inside.usda")  # inside

mat = UsdShade.Material.Define(s, "/scene/mtl/look")
surf = UsdShade.Shader.Define(s, "/scene/mtl/look/surf")
surf.CreateIdAttr("UsdPreviewSurface")
mat.CreateSurfaceOutput().ConnectToSource(surf.CreateOutput("surface", T.Token))
for name, tex in (("ok", "./tex/ok.png"), ("wood", "../lib/wood.png"), ("skin", "../lib/skin.<UDIM>.png"),
                  ("missing", "tex/missing.png")):  # bare search path, missing: inside, not "outside"
    sh = UsdShade.Shader.Define(s, f"/scene/mtl/look/{name}")
    sh.CreateIdAttr("UsdUVTexture")
    sh.CreateInput("file", T.Asset).Set(Sdf.AssetPath(tex))

m = UsdGeom.Mesh.Define(s, "/scene/geo/card")
m.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(1, 1, 0), Gf.Vec3f(0, 1, 0)]))
m.CreateFaceVertexCountsAttr([4])
m.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)

UsdLux.DomeLight_1.Define(s, "/scene/lights/dome")  # the schema Houdini 22 writes; no texture
s.GetRootLayer().Save()
print(EXPORT / "shot.usda")
