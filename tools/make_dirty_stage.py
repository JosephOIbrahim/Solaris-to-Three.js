"""Test fixture for web_census.py: a small stage with eight planted problems.

Run: python tools/make_dirty_stage.py   (writes fixtures/stage-dirty/dirty.usda)
The shader ID "kma_fixture_node" is made up for the test, not a real Karma node.
"""
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade, Vt

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "stage-dirty"
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "tex").mkdir(exist_ok=True)
(OUT / "tex" / "ok.png").write_bytes(b"\x89PNG\r\n\x1a\n")          # resolves; format fine
(OUT / "tex" / "rust.rat").write_bytes(b"RAT")                      # resolves; format the browser can't load

s = Usd.Stage.CreateNew(str(OUT / "dirty.usda"))
UsdGeom.SetStageUpAxis(s, UsdGeom.Tokens.y)
root = UsdGeom.Xform.Define(s, "/scene")
s.SetDefaultPrim(root.GetPrim())
T = Sdf.ValueTypeNames

def mtlx(path, extra_id=None, tex=None):
    m = UsdShade.Material.Define(s, path)
    sh = UsdShade.Shader.Define(s, f"{path}/surf")
    sh.CreateIdAttr("ND_standard_surface_surfaceshader")
    m.CreateOutput("mtlx:surface", T.Token).ConnectToSource(sh.CreateOutput("out", T.Token))
    if tex:
        img = UsdShade.Shader.Define(s, f"{path}/img")
        img.CreateIdAttr("ND_image_color3")
        img.CreateInput("file", T.Asset).Set(Sdf.AssetPath(tex))
    if extra_id:
        k = UsdShade.Shader.Define(s, f"{path}/extra")
        k.CreateIdAttr(extra_id)
        k.CreateInput("ray_types", T.String).Set("all")
    return m

def mesh(path, mat, uv=True):
    m = UsdGeom.Mesh.Define(s, path)
    m.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(1, 1, 0), Gf.Vec3f(0, 1, 0)]))
    m.CreateFaceVertexCountsAttr([4]); m.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    if uv:
        UsdGeom.PrimvarsAPI(m).CreatePrimvar("st", T.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(
            Vt.Vec2fArray([Gf.Vec2f(0, 0), Gf.Vec2f(1, 0), Gf.Vec2f(1, 1), Gf.Vec2f(0, 1)]))
    if mat:
        UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)

good = mtlx("/scene/mtl/good", tex="./tex/ok.png")
mesh("/scene/geo/good", good)                                                   # clean
mesh("/scene/geo/rat", mtlx("/scene/mtl/rat", tex="./tex/rust.rat"))            # 1 format
mesh("/scene/geo/abs", mtlx("/scene/mtl/abs", tex="D:/textures/wood.png"))      # 2 absolute path
mesh("/scene/geo/udim", mtlx("/scene/mtl/udim", tex="./tex/skin.<UDIM>.exr"))   # 3 UDIM
mesh("/scene/geo/nouv", good, uv=False)                                         # 4 MaterialX without UVs
mesh("/scene/geo/karma", mtlx("/scene/mtl/karma", extra_id="kma_fixture_node")) # 5 unfamiliar node
UsdLux.CylinderLight.Define(s, "/scene/lights/tube")                            # 6 approximated light
UsdLux.DomeLight.Define(s, "/scene/lights/dome").CreateTextureFileAttr(Sdf.AssetPath("./tex/sky.exr"))  # 7 dome .exr + missing
s.DefinePrim("/scene/missing").GetReferences().AddReference("./nowhere.usd")    # 8 composition error
s.GetRootLayer().Save()
print(OUT / "dirty.usda")
