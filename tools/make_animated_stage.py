"""Write the animated fixture the glTF animation test uses.

    python tools/make_animated_stage.py

fixtures/stage-animated/animated.usda holds one cube that turns a full circle over 121 frames,
so a writer that bakes xform animation has something unambiguous to sample.
"""
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "stage-animated"
OUT.mkdir(parents=True, exist_ok=True)

stage = Usd.Stage.CreateNew(str(OUT / "animated.usda"))
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
UsdGeom.SetStageMetersPerUnit(stage, 1.0)
stage.SetStartTimeCode(1)
stage.SetEndTimeCode(121)
stage.SetTimeCodesPerSecond(24)
root = UsdGeom.Xform.Define(stage, "/spin")
stage.SetDefaultPrim(root.GetPrim())

cube = UsdGeom.Mesh.Define(stage, "/spin/cube")
cube.CreatePointsAttr(Vt.Vec3fArray([
    Gf.Vec3f(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]))
cube.CreateFaceVertexCountsAttr([4] * 6)
cube.CreateFaceVertexIndicesAttr([0, 1, 3, 2, 4, 6, 7, 5, 0, 4, 5, 1, 2, 3, 7, 6, 0, 2, 6, 4, 1, 5, 7, 3])
UsdGeom.PrimvarsAPI(cube).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                        UsdGeom.Tokens.faceVarying).Set(
    Vt.Vec2fArray([Gf.Vec2f(0, 0), Gf.Vec2f(1, 0), Gf.Vec2f(1, 1), Gf.Vec2f(0, 1)] * 6))

material = UsdShade.Material.Define(stage, "/spin/mtl")
surface = UsdShade.Shader.Define(stage, "/spin/mtl/surf")
surface.CreateIdAttr("UsdPreviewSurface")
surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.2, 0.6, 0.9))
material.CreateSurfaceOutput().ConnectToSource(surface.CreateOutput("surface", Sdf.ValueTypeNames.Token))
UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(material)

spin = root.AddRotateYOp()
spin.Set(0.0, 1)
spin.Set(180.0, 61)
spin.Set(360.0, 121)

stage.GetRootLayer().Save()
print(OUT / "animated.usda")
