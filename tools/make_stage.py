"""
Author a small USD stage shaped like a Solaris export, for testing web delivery.

Layout mirrors what a Component Builder asset + a LOP shot network write out:

  fixtures/stage/
    shot.usda          root layer: /hero (payload), /cameras, /lights, timeline
    hero/asset.usda    component: /hero/geo/{render,proxy}, /hero/mtl, variant set "look"
    env/sky.hdr        equirect environment for the dome light
  fixtures/stage-flat/
    hero_flat.usdz     flattened copy (no composition) for three.js's own USDLoader

Run:  python tools/make_stage.py      (needs usd-core and numpy, or hython)
"""
import math
import struct
from pathlib import Path

import numpy as np
from pxr import Gf, Kind, Sdf, Usd, UsdGeom, UsdLux, UsdShade, UsdUtils, Vt

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "stage"
FLAT = ROOT / "fixtures" / "stage-flat"
(OUT / "hero").mkdir(parents=True, exist_ok=True)
(OUT / "env").mkdir(parents=True, exist_ok=True)
FLAT.mkdir(parents=True, exist_ok=True)

T = Sdf.ValueTypeNames


# ---------------------------------------------------------------- geometry
def uv_sphere(radius=1.0, rings=32, segments=64):
    pts, nrm, st = [], [], []
    for r in range(rings + 1):
        v = r / rings
        phi = v * math.pi
        for s in range(segments + 1):
            u = s / segments
            th = u * 2 * math.pi
            n = (math.sin(phi) * math.cos(th), math.cos(phi), math.sin(phi) * math.sin(th))
            pts.append(Gf.Vec3f(*(radius * c for c in n)))
            nrm.append(Gf.Vec3f(*n))
            st.append(Gf.Vec2f(u, 1 - v))
    counts, idx = [], []
    row = segments + 1
    for r in range(rings):
        for s in range(segments):
            a, b = r * row + s, r * row + s + 1
            c, d = (r + 1) * row + s + 1, (r + 1) * row + s
            counts.append(4)
            idx += [a, d, c, b]  # counter-clockwise seen from outside (USD default)
    return pts, nrm, st, counts, idx


def define_mesh(stage, path, pts, counts, idx, nrm=None, st=None, subdiv="none"):
    m = UsdGeom.Mesh.Define(stage, path)
    m.CreatePointsAttr(Vt.Vec3fArray(pts))
    m.CreateFaceVertexCountsAttr(Vt.IntArray(counts))
    m.CreateFaceVertexIndicesAttr(Vt.IntArray(idx))
    m.CreateSubdivisionSchemeAttr(subdiv)
    if nrm is not None:
        m.CreateNormalsAttr(Vt.Vec3fArray(nrm))
        m.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    if st is not None:
        pv = UsdGeom.PrimvarsAPI(m).CreatePrimvar("st", T.TexCoord2fArray, UsdGeom.Tokens.vertex)
        pv.Set(Vt.Vec2fArray(st))
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
    m.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(min(xs), min(ys), min(zs)), Gf.Vec3f(max(xs), max(ys), max(zs))]))
    return m


CUBE_PTS = [Gf.Vec3f(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
# faces wound counter-clockwise from outside
CUBE_IDX = [0, 1, 3, 2,  4, 6, 7, 5,  0, 4, 5, 1,  2, 3, 7, 6,  0, 2, 6, 4,  1, 5, 7, 3]
CUBE_CNT = [4] * 6


def scaled(pts, sx, sy, sz, ty=0.0):
    return [Gf.Vec3f(p[0] * sx, p[1] * sy + ty, p[2] * sz) for p in pts]


# ---------------------------------------------------------------- materials
def mtlx_standard(stage, path, base_color, metalness, roughness, coat=0.0, look_in_variants=False):
    """What a Karma Material Builder with a mtlxstandard_surface writes to USD.

    look_in_variants: leave colour/metal/roughness unauthored here so a variant
    set can drive them. LIVRPS: a local opinion would beat the variant's.
    """
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, f"{path}/mtlxstandard_surface")
    sh.CreateIdAttr("ND_standard_surface_surfaceshader")
    sh.CreateInput("base", T.Float).Set(1.0)
    if not look_in_variants:
        sh.CreateInput("base_color", T.Color3f).Set(Gf.Vec3f(*base_color))
        sh.CreateInput("metalness", T.Float).Set(metalness)
        sh.CreateInput("specular_roughness", T.Float).Set(roughness)
    sh.CreateInput("coat", T.Float).Set(coat)
    out = sh.CreateOutput("out", T.Token)
    mat.CreateOutput("mtlx:surface", T.Token).ConnectToSource(out)
    return mat


def mtlx_openpbr(stage, path, base_color, metalness, roughness):
    """OpenPBR, the newer MaterialX surface (Houdini 21 era)."""
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, f"{path}/mtlxopen_pbr_surface")
    sh.CreateIdAttr("ND_open_pbr_surface_surfaceshader")
    sh.CreateInput("base_weight", T.Float).Set(1.0)
    sh.CreateInput("base_color", T.Color3f).Set(Gf.Vec3f(*base_color))
    sh.CreateInput("base_metalness", T.Float).Set(metalness)
    sh.CreateInput("specular_roughness", T.Float).Set(roughness)
    out = sh.CreateOutput("out", T.Token)
    mat.CreateOutput("mtlx:surface", T.Token).ConnectToSource(out)
    return mat


def preview_surface(stage, path, color, metallic, roughness):
    """UsdPreviewSurface: the lowest common denominator every USD reader knows."""
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, f"{path}/preview")
    sh.CreateIdAttr("UsdPreviewSurface")
    sh.CreateInput("diffuseColor", T.Color3f).Set(Gf.Vec3f(*color))
    sh.CreateInput("metallic", T.Float).Set(metallic)
    sh.CreateInput("roughness", T.Float).Set(roughness)
    out = sh.CreateOutput("surface", T.Token)
    mat.CreateSurfaceOutput().ConnectToSource(out)
    return mat


def bind(prim, mat):
    UsdShade.MaterialBindingAPI.Apply(prim)
    UsdShade.MaterialBindingAPI(prim).Bind(mat)


# ---------------------------------------------------------------- asset layer
asset_path = OUT / "hero" / "asset.usda"
a = Usd.Stage.CreateNew(str(asset_path))
UsdGeom.SetStageUpAxis(a, UsdGeom.Tokens.y)
UsdGeom.SetStageMetersPerUnit(a, 1.0)

hero = UsdGeom.Xform.Define(a, "/hero")
a.SetDefaultPrim(hero.GetPrim())
Usd.ModelAPI(hero.GetPrim()).SetKind(Kind.Tokens.component)

UsdGeom.Scope.Define(a, "/hero/geo")
render = UsdGeom.Xform.Define(a, "/hero/geo/render")
render.CreatePurposeAttr(UsdGeom.Tokens.render)
proxy = UsdGeom.Xform.Define(a, "/hero/geo/proxy")
proxy.CreatePurposeAttr(UsdGeom.Tokens.proxy)
UsdGeom.Scope.Define(a, "/hero/mtl")

m_std = mtlx_standard(a, "/hero/mtl/orb_std", (0.85, 0.25, 0.08), 0.0, 0.35, coat=0.6, look_in_variants=True)
m_pbr = mtlx_openpbr(a, "/hero/mtl/orb_openpbr", (0.20, 0.45, 0.85), 0.0, 0.25)
m_subd = mtlx_standard(a, "/hero/mtl/subd", (0.92, 0.90, 0.86), 0.0, 0.55)
m_peb = mtlx_standard(a, "/hero/mtl/pebble", (0.12, 0.12, 0.13), 0.0, 0.6)
m_ped = preview_surface(a, "/hero/mtl/pedestal", (0.55, 0.54, 0.52), 0.0, 0.8)
m_floor = preview_surface(a, "/hero/mtl/floor", (0.18, 0.18, 0.19), 0.0, 0.9)
m_proxy = preview_surface(a, "/hero/mtl/proxy", (1.0, 0.0, 1.0), 0.0, 1.0)

sp = uv_sphere(0.55)

floor = define_mesh(a, "/hero/geo/render/floor",
                    [Gf.Vec3f(-8, 0, -8), Gf.Vec3f(-8, 0, 8), Gf.Vec3f(8, 0, 8), Gf.Vec3f(8, 0, -8)],
                    [4], [0, 1, 2, 3],
                    nrm=[Gf.Vec3f(0, 1, 0)] * 4,
                    st=[Gf.Vec2f(0, 0), Gf.Vec2f(0, 1), Gf.Vec2f(1, 1), Gf.Vec2f(1, 0)])
bind(floor.GetPrim(), m_floor)

for name, x in (("pedestal_l", -1.5), ("pedestal_c", 0.0), ("pedestal_r", 1.5)):
    ped = define_mesh(a, f"/hero/geo/render/{name}", scaled(CUBE_PTS, 0.5, 0.25, 0.5, ty=0.25), CUBE_CNT, CUBE_IDX)
    UsdGeom.XformCommonAPI(ped).SetTranslate(Gf.Vec3d(x, 0, 0))
    bind(ped.GetPrim(), m_ped)

orb = define_mesh(a, "/hero/geo/render/orb_std", *sp[:1], sp[3], sp[4], nrm=sp[1], st=sp[2])
UsdGeom.XformCommonAPI(orb).SetTranslate(Gf.Vec3d(-1.5, 1.05, 0))
bind(orb.GetPrim(), m_std)

orb2 = define_mesh(a, "/hero/geo/render/orb_openpbr", *sp[:1], sp[3], sp[4], nrm=sp[1], st=sp[2])
UsdGeom.XformCommonAPI(orb2).SetTranslate(Gf.Vec3d(1.5, 1.05, 0))
bind(orb2.GetPrim(), m_pbr)

# A cube with catmullClark: only becomes round if OpenSubdiv runs.
# Give it UVs: MaterialX on the web builds tangents from st; a UV-less mesh renders black.
subd = define_mesh(a, "/hero/geo/render/orb_subd", scaled(CUBE_PTS, 0.55, 0.55, 0.55), CUBE_CNT, CUBE_IDX,
                   subdiv="catmullClark")
box_st = UsdGeom.PrimvarsAPI(subd).CreatePrimvar("st", T.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
box_st.Set(Vt.Vec2fArray([Gf.Vec2f(0, 0), Gf.Vec2f(1, 0), Gf.Vec2f(1, 1), Gf.Vec2f(0, 1)] * 6))
UsdGeom.XformCommonAPI(subd).SetTranslate(Gf.Vec3d(0, 1.05, 0))
bind(subd.GetPrim(), m_subd)

# Scatter: a PointInstancer ring, animated by its parent (a turntable).
ring = UsdGeom.Xform.Define(a, "/hero/geo/render/ring")
spin = ring.AddRotateYOp()
for t, deg in ((1, 0.0), (120, 360.0)):
    spin.Set(deg, t)
pi = UsdGeom.PointInstancer.Define(a, "/hero/geo/render/ring/pebbles")
proto_root = UsdGeom.Scope.Define(a, "/hero/geo/render/ring/pebbles/prototypes")
peb_sp = uv_sphere(0.09, 10, 20)
peb = define_mesh(a, "/hero/geo/render/ring/pebbles/prototypes/pebble", *peb_sp[:1], peb_sp[3], peb_sp[4],
                  nrm=peb_sp[1], st=peb_sp[2])
bind(peb.GetPrim(), m_peb)
pi.CreatePrototypesRel().SetTargets([peb.GetPath()])
n = 36
pi.CreatePositionsAttr(Vt.Vec3fArray([Gf.Vec3f(3.0 * math.cos(2 * math.pi * i / n), 0.09,
                                                3.0 * math.sin(2 * math.pi * i / n)) for i in range(n)]))
pi.CreateProtoIndicesAttr(Vt.IntArray([0] * n))
pi.CreateScalesAttr(Vt.Vec3fArray([Gf.Vec3f(0.6 + 0.8 * ((i * 7) % 5) / 4.0) for i in range(n)]))

# Proxy purpose: a magenta box. If you can see it, purpose filtering failed.
px = define_mesh(a, "/hero/geo/proxy/box", scaled(CUBE_PTS, 2.2, 0.8, 0.8, ty=0.8), CUBE_CNT, CUBE_IDX)
bind(px.GetPrim(), m_proxy)

# Variant set "look" drives the standard_surface orb, like a lookdev variant.
vset = hero.GetPrim().GetVariantSets().AddVariantSet("look")
looks = {
    "ember":  dict(base_color=(0.85, 0.25, 0.08), metalness=0.0, specular_roughness=0.35),
    "gold":   dict(base_color=(1.00, 0.76, 0.33), metalness=1.0, specular_roughness=0.18),
    "chrome": dict(base_color=(0.95, 0.95, 0.95), metalness=1.0, specular_roughness=0.04),
}
shader_path = "/hero/mtl/orb_std/mtlxstandard_surface"
for name, vals in looks.items():
    vset.AddVariant(name)
    vset.SetVariantSelection(name)
    with vset.GetVariantEditContext():
        shp = a.OverridePrim(shader_path)
        shp.CreateAttribute("inputs:base_color", T.Color3f).Set(Gf.Vec3f(*vals["base_color"]))
        shp.CreateAttribute("inputs:metalness", T.Float).Set(vals["metalness"])
        shp.CreateAttribute("inputs:specular_roughness", T.Float).Set(vals["specular_roughness"])
vset.SetVariantSelection("ember")
a.GetRootLayer().Save()

# ---------------------------------------------------------------- shot layer
shot_path = OUT / "shot.usda"
s = Usd.Stage.CreateNew(str(shot_path))
UsdGeom.SetStageUpAxis(s, UsdGeom.Tokens.y)
UsdGeom.SetStageMetersPerUnit(s, 1.0)
s.SetStartTimeCode(1)
s.SetEndTimeCode(120)
s.SetTimeCodesPerSecond(24)
s.SetFramesPerSecond(24)

hero_ref = s.DefinePrim("/hero", "Xform")
hero_ref.GetPayloads().AddPayload("./hero/asset.usda")
s.SetDefaultPrim(hero_ref)

UsdGeom.Scope.Define(s, "/cameras")
cam = UsdGeom.Camera.Define(s, "/cameras/render_cam")
cam.CreateFocalLengthAttr(35.0)
cam.CreateHorizontalApertureAttr(36.0)
cam.CreateVerticalApertureAttr(20.25)
cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 200.0))
eye, target = Gf.Vec3d(3.6, 2.4, 5.8), Gf.Vec3d(0, 0.85, 0)
view = Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 1, 0))
cam.AddTransformOp().Set(view.GetInverse())

UsdGeom.Scope.Define(s, "/lights")
# DomeLight_1 is what Houdini 22's Dome Light LOP (domelight::3.0) writes; the older schema is DomeLight.
dome = UsdLux.DomeLight_1.Define(s, "/lights/dome")
dome.CreateTextureFileAttr(Sdf.AssetPath("./env/sky.hdr"))
dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
dome.CreateIntensityAttr(1.0)
dome.CreateExposureAttr(0.0)
# Turned like a lighting TD turns an HDRI to place the sun. tests/browser/smoke.mjs looks for the sun
# where this rotation should put it (DOME_ROTATE_Y there must match).
dome.AddRotateYOp().Set(75.0)

key = UsdLux.RectLight.Define(s, "/lights/key")
key.CreateWidthAttr(2.0)
key.CreateHeightAttr(2.0)
key.CreateIntensityAttr(20.0)
key.CreateExposureAttr(2.0)
key.CreateColorAttr(Gf.Vec3f(1.0, 0.86, 0.7))
kv = Gf.Matrix4d().SetLookAt(Gf.Vec3d(-3.5, 4.0, 3.0), Gf.Vec3d(0, 0.8, 0), Gf.Vec3d(0, 1, 0))
key.AddTransformOp().Set(kv.GetInverse())

rim = UsdLux.SphereLight.Define(s, "/lights/rim")
rim.CreateRadiusAttr(0.25)
rim.CreateIntensityAttr(30.0)
rim.CreateExposureAttr(1.0)
rim.CreateColorAttr(Gf.Vec3f(0.6, 0.75, 1.0))
UsdGeom.XformCommonAPI(rim).SetTranslate(Gf.Vec3d(2.5, 2.5, -3.0))

s.GetRootLayer().Save()


# ---------------------------------------------------------------- environment
def write_hdr(path, img):
    """Flat (non-RLE) Radiance RGBE writer."""
    h, w, _ = img.shape
    mx = img.max(axis=2)
    e = np.ceil(np.log2(np.maximum(mx, 1e-32)))
    scale = np.where(mx > 1e-32, 256.0 / np.exp2(e), 0)
    rgbe = np.zeros((h, w, 4), np.uint8)
    rgbe[..., :3] = np.clip(img * scale[..., None], 0, 255).astype(np.uint8)
    rgbe[..., 3] = np.where(mx > 1e-32, e + 128, 0).astype(np.uint8)
    with open(path, "wb") as f:
        f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n")
        f.write(f"-Y {h} +X {w}\n".encode())
        f.write(rgbe.tobytes())


H, W = 256, 512
v = (np.arange(H) + 0.5) / H
u = (np.arange(W) + 0.5) / W
phi = v[:, None] * np.pi                     # 0 = up
th = u[None, :] * 2 * np.pi
d = np.stack([np.sin(phi) * np.cos(th), np.cos(phi) * np.ones_like(th), np.sin(phi) * np.sin(th)], -1)
up = d[..., 1]
sky = np.where(up[..., None] > 0,
               (1 - up[..., None]) * np.array([0.9, 0.95, 1.1]) + up[..., None] * np.array([0.25, 0.42, 0.85]),
               np.array([0.16, 0.14, 0.12]) * (1 + up[..., None]))
sun_dir = np.array([-0.55, 0.62, 0.56]); sun_dir /= np.linalg.norm(sun_dir)
cosang = (d @ sun_dir)
sun = np.where(cosang > math.cos(math.radians(3.0)), 1.0, 0.0)[..., None] * np.array([60.0, 52.0, 40.0])
glow = np.clip(cosang, 0, 1)[..., None] ** 64 * np.array([2.0, 1.6, 1.1])
write_hdr(OUT / "env" / "sky.hdr", (sky + sun + glow).astype(np.float32))

# ---------------------------------------------------------------- flattened usdz
flat_stage = Usd.Stage.Open(str(shot_path))
flat_layer = flat_stage.Flatten()
flat_usdc = FLAT / "hero_flat.usdc"
flat_layer.Export(str(flat_usdc))
fl = Usd.Stage.Open(str(flat_usdc))
# .hdr isn't a legal USDZ texture type; drop the dome texture in the flat copy.
fl.GetPrimAtPath("/lights/dome").GetAttribute("inputs:texture:file").Clear()
fl.GetRootLayer().Save()
ok = UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(flat_usdc)), str(FLAT / "hero_flat.usdz"))
flat_usdc.unlink()

print("stage:", shot_path.relative_to(ROOT), "| asset:", asset_path.relative_to(ROOT), "| usdz ok:", ok)
for p in sorted(list(OUT.rglob("*")) + list(FLAT.rglob("*"))):
    if p.is_file():
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size/1024:.0f} KB")
