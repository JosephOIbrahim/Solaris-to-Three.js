"""Regression tests covering the first-principles audit fixes across the pure-pxr core,
format writers (glTF 2.0, X3D 4.0, USDZ, USD folder), Houdini adapter, and meta-tools.

    python -m unittest tests/test_audit_regressions.py -v
"""
import importlib
import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_router  # noqa: E402
import install_package  # noqa: E402
import jev  # noqa: E402
import release  # noqa: E402

try:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade  # noqa: E402
    import web_census  # noqa: E402
    from webexport import api, inventory, settings as ws, textures, validate  # noqa: E402
    from webexport.profiles.gltf import GltfProfile  # noqa: E402
    from webexport.profiles.usdz import UsdzProfile  # noqa: E402
    from webexport.profiles.x3d import X3dProfile  # noqa: E402
    from webexport.writers import gltf as gltf_mod  # noqa: E402
    from webexport.writers import usd_folder as folder_mod  # noqa: E402
    from webexport.writers import usdz as usdz_mod  # noqa: E402
except ImportError:
    Usd = None

NEEDS_PXR = "needs pxr (pip install usd-core, or run under hython)"

# Minimal valid 1x1 white PNG
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2&\x05\x00\x00\x00\x00IEND\xaeB`\x82"
)


def parse_glb(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", raw, 0)
    assert magic == 0x46546C67 and version == 2 and length == len(raw)
    jlen, jtype = struct.unpack_from("<II", raw, 12)
    assert jtype == 0x4E4F534A
    doc = json.loads(raw[20:20 + jlen].decode("utf-8"))
    off = 20 + jlen
    if off < len(raw):
        blen, btype = struct.unpack_from("<II", raw, off)
        assert btype == 0x004E4942
        blob = raw[off + 8:off + 8 + blen]
    else:
        blob = b""
    return doc, blob


def read_accessor(doc: dict, blob: bytes, idx: int) -> list:
    acc = doc["accessors"][idx]
    bv = doc["bufferViews"][acc["bufferView"]]
    off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    comp = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}[acc["componentType"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    size = struct.calcsize(comp) * width
    out = []
    for i in range(acc["count"]):
        vals = struct.unpack_from(f"<{width}{comp}", blob, off + i * size)
        out.append(vals[0] if width == 1 else vals)
    return out


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webexport_audit_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


@unittest.skipIf(Usd is None, NEEDS_PXR)
class InventoryAndPipelineRegressions(TempCase):
    def test_c1_row_vector_world_scale_invariance_and_reflection(self):
        stage = Usd.Stage.CreateNew(str(self.tmp / "scale.usda"))
        xf = UsdGeom.Xform.Define(stage, "/rot_scale")
        xf.AddRotateXOp().Set(45.0)
        xf.AddScaleOp().Set(Gf.Vec3f(2.0, 3.0, 5.0))
        s = inventory._world_scale(xf.GetPrim(), Usd.TimeCode.Default())
        for a, b in zip(s, (2.0, 3.0, 5.0)):
            self.assertAlmostEqual(a, b, places=5)

        neg = UsdGeom.Xform.Define(stage, "/mirror")
        neg.AddScaleOp().Set(Gf.Vec3f(-2.0, 3.0, 4.0))
        s_neg = inventory._world_scale(neg.GetPrim(), Usd.TimeCode.Default())
        self.assertLess(s_neg[0] * s_neg[1] * s_neg[2], 0.0)
        self.assertAlmostEqual(abs(s_neg[0]), 2.0, places=5)

    def test_c2_c3_material_nodegraph_textures_and_ies_exclusion(self):
        for name in ("albedo.png", "rough.png", "time.png"):
            (self.tmp / name).write_bytes(PNG_1X1)
        ies = self.tmp / "profile.ies"
        ies.write_text("IESNA:LM-63-2002\n", encoding="utf-8")

        stage = Usd.Stage.CreateNew(str(self.tmp / "tex.usda"))
        mat = UsdShade.Material.Define(stage, "/mat")
        mat.CreateInput("albedoMap", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./albedo.png"))
        ng = UsdShade.NodeGraph.Define(stage, "/mat/ng")
        ng.CreateInput("roughMap", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./rough.png"))
        sh = UsdShade.Shader.Define(stage, "/mat/sh")
        sh.CreateIdAttr("UsdUVTexture")
        sh.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./time.png"), 1.0)
        sh.GetPrim().CreateAttribute("info:sourceAsset", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./ignored.osl"))
        light = UsdLux.SphereLight.Define(stage, "/light")
        light.GetPrim().CreateAttribute("inputs:shaping:ies:file", Sdf.ValueTypeNames.Asset).Set(
            Sdf.AssetPath("./profile.ies"))
        stage.GetRootLayer().Save()

        facts = inventory.gather(stage)
        paths = [t.path for t in facts.inventory.textures]
        self.assertEqual(set(paths), {"./albedo.png", "./rough.png", "./time.png"})
        self.assertNotIn("./ignored.osl", paths)
        self.assertNotIn("./profile.ies", paths)

    def test_h2_asset_location_prefers_value_authoring_layer(self):
        sub_dir = self.tmp / "sub"
        sub_dir.mkdir()
        (sub_dir / "tile.png").write_bytes(PNG_1X1)

        sub = Usd.Stage.CreateNew(str(sub_dir / "asset.usda"))
        sh = UsdShade.Shader.Define(sub, "/mat/tex")
        sh.CreateIdAttr("UsdUVTexture")
        sh.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./tile.png"))
        sub.GetRootLayer().Save()

        root = Usd.Stage.CreateNew(str(self.tmp / "shot.usda"))
        root.GetRootLayer().subLayerPaths.append("./sub/asset.usda")
        over = root.OverridePrim("/mat/tex")
        attr = over.CreateAttribute("inputs:file", Sdf.ValueTypeNames.Asset)
        attr.SetDisplayName("Albedo File")  # metadata-only spec in stronger root layer
        root.GetRootLayer().Save()

        facts = inventory.gather(root)
        self.assertEqual(len(facts.inventory.textures), 1)
        t = facts.inventory.textures[0]
        self.assertTrue(t.exists, "asset_location should resolve from value-authoring sublayer, not metadata override")
        resolved = inventory.asset_location(
            root.GetPrimAtPath("/mat/tex").GetAttribute("inputs:file"),
            Sdf.AssetPath("./tile.png"),
        )
        self.assertTrue(Path(resolved).samefile(sub_dir / "tile.png"))

    def test_h3_instance_proxy_prim_stack_and_h4_surface_fallback_and_l1_blocked_subdiv(self):
        comp_dir = self.tmp / "comp"
        comp_dir.mkdir()
        (comp_dir / "c.png").write_bytes(PNG_1X1)
        comp = Usd.Stage.CreateNew(str(comp_dir / "comp.usda"))
        root = UsdGeom.Xform.Define(comp, "/comp")
        comp.SetDefaultPrim(root.GetPrim())
        mesh = UsdGeom.Mesh.Define(comp, "/comp/m")
        mesh.CreatePointsAttr([(-1, 0, 0), (1, 0, 0), (0, 1, 0)])
        mesh.CreateFaceVertexCountsAttr([3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
        mesh.CreateSubdivisionSchemeAttr().Block()  # L1: blocked subdiv scheme
        mat = UsdShade.Material.Define(comp, "/comp/mat")
        # H4: empty mtlx:surface output (unconnected) must fall back to UsdPreviewSurface
        mat.CreateSurfaceOutput("mtlx")
        sh = UsdShade.Shader.Define(comp, "/comp/mat/prev")
        sh.CreateIdAttr("UsdPreviewSurface")
        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        tex = UsdShade.Shader.Define(comp, "/comp/mat/tex")
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./c.png"))
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(), "rgb")
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat, UsdShade.Tokens.preview)
        comp.GetRootLayer().Save()

        shot = Usd.Stage.CreateNew(str(self.tmp / "shot.usda"))
        inst = shot.DefinePrim("/inst", "Xform")
        inst.GetReferences().AddReference("./comp/comp.usda")
        inst.SetInstanceable(True)
        shot.GetRootLayer().Save()

        facts = inventory.gather(shot)
        self.assertEqual(len(facts.inventory.meshes), 1)
        self.assertEqual(facts.inventory.meshes[0].subdiv_scheme, "none")
        self.assertEqual(facts.inventory.meshes[0].material_path, "/inst/mat")
        self.assertEqual(len(facts.inventory.materials), 1)
        self.assertEqual(facts.inventory.materials[0].kind, "preview")

    def test_m3_overlap_rejected_for_gltf_and_x3d_and_l2_preview_url_encoding(self):
        src_dir = self.tmp / "src"
        src_dir.mkdir()
        stage = Usd.Stage.CreateNew(str(src_dir / "shot.usda"))
        m = UsdGeom.Mesh.Define(stage, "/m")
        m.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        m.CreateFaceVertexCountsAttr([3])
        m.CreateFaceVertexIndicesAttr([0, 1, 2])
        stage.GetRootLayer().Save()

        for fmt in ("gltf", "x3d"):
            with self.assertRaises(ValueError):
                api.export(stage, [fmt], src_dir / "nested_out", jev_on=False)

        stages_entry = ROOT / "stages" / "my show #1" / "hero.usda"
        url = api.preview_url(stages_entry)
        self.assertIsNotNone(url)
        self.assertIn("/stages/my%20show%20%231/hero.usda", url)

    def test_textures_copy_writable_and_settings_validation_and_inactive_websettings(self):
        src = self.tmp / "ro.png"
        src.write_bytes(PNG_1X1)
        os.chmod(src, 0o444)
        dst = self.tmp / "dst" / "ro.png"
        textures.copy(src, dst)
        self.assertTrue(os.access(dst, os.W_OK))
        os.chmod(src, 0o644)

        self.assertIsNone(textures.flag("pkg.usdz[sub/tex.png]"))

        stage = Usd.Stage.CreateNew(str(self.tmp / "ws.usda"))
        prim = stage.DefinePrim("/WebSettings")
        prim.CreateAttribute("web:hudVariantSets", Sdf.ValueTypeNames.String).Set("look")
        prim.CreateAttribute("web:backgroundBlur", Sdf.ValueTypeNames.Float).Set(float("nan"))
        prim.CreateAttribute("web:exposureOffset", Sdf.ValueTypeNames.Float).Set(float("inf"))
        read = ws.read_settings(stage)
        self.assertEqual(read.hudVariantSets, ["look"])
        self.assertEqual(read.backgroundBlur, 0.35)
        self.assertEqual(read.exposureOffset, 0.0)

        prim.SetActive(False)
        stage.GetRootLayer().Save()
        v = validate.validate_folder(self.tmp / "ws.usda", jev_on=False)
        self.assertFalse(v.ok)
        self.assertTrue(any("/WebSettings" in p for p in v.problems), v.problems)

    def test_profiles_parity_udim_other_shader_lights_units_and_x3d_geometry_variant(self):
        stage = Usd.Stage.CreateNew(str(self.tmp / "prof.usda"))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)
        m = UsdGeom.Mesh.Define(stage, "/m")
        m.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        m.CreateFaceVertexCountsAttr([3])
        m.CreateFaceVertexIndicesAttr([0, 1, 2])
        vs = m.GetPrim().GetVariantSets().AddVariantSet("geo")
        vs.AddVariant("a")
        vs.AddVariant("b")
        vs.SetVariantSelection("a")
        with vs.GetVariantEditContext():
            m.CreatePointsAttr([(0, 0, 0), (2, 0, 0), (0, 1, 0)])

        mat = UsdShade.Material.Define(stage, "/mat")
        sh = UsdShade.Shader.Define(stage, "/mat/custom")
        sh.CreateIdAttr("kma_unknown_surface")
        sh.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./skin.<UDIM>.png"))
        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
        UsdLux.PortalLight.Define(stage, "/portal")
        stage.GetRootLayer().Save()

        facts = inventory.gather(stage)
        inv = facts.inventory

        g_items = {(i.severity, i.feature) for i in GltfProfile(inv, stage=stage).assess(inv)}
        self.assertIn(("blocks", "udim_texture"), g_items)
        self.assertNotIn(("blocks", "missing_file"), g_items)
        self.assertIn(("changes", "shader_not_read"), g_items)
        self.assertIn(("changes", "light_dropped"), g_items)
        self.assertIn(("note", "stage_units"), g_items)

        x_items = {(i.severity, i.feature) for i in X3dProfile(inv, stage=stage).assess(inv)}
        self.assertIn(("blocks", "udim_texture"), x_items)
        self.assertNotIn(("blocks", "missing_file"), x_items)
        self.assertIn(("changes", "shader_not_read"), x_items)
        self.assertIn(("changes", "light_dropped"), x_items)
        self.assertIn(("changes", "geometry_variant"), x_items)
        self.assertIn(("note", "stage_units"), x_items)

        up = UsdzProfile(stage=stage, facts=facts, jev_on=False)
        up.assess(inv)
        self.assertEqual(up.jev_note, "")


@unittest.skipIf(Usd is None, NEEDS_PXR)
class GltfCompilerRegressions(TempCase):
    def test_c1_trs_row_vector_zero_scale_and_reflection(self):
        stage = Usd.Stage.CreateNew(str(self.tmp / "trs.usda"))
        xf = UsdGeom.Xform.Define(stage, "/x")
        xf.AddTranslateOp().Set(Gf.Vec3d(1.0, 2.0, 3.0))
        xf.AddRotateYOp().Set(90.0)
        xf.AddScaleOp().Set(Gf.Vec3f(2.0, 3.0, 4.0))
        trs = gltf_mod._trs(xf.GetPrim(), 1.0)
        self.assertEqual([round(v, 5) for v in trs["translation"]], [1.0, 2.0, 3.0])
        self.assertEqual([round(v, 5) for v in trs["scale"]], [2.0, 3.0, 4.0])

        z = UsdGeom.Xform.Define(stage, "/z")
        z.AddScaleOp().Set(Gf.Vec3f(0.0, 2.0, 3.0))
        sz = gltf_mod._trs(z.GetPrim(), 1.0)["scale"]
        self.assertEqual(sz[0], 0.0)

        neg = UsdGeom.Xform.Define(stage, "/neg")
        neg.AddScaleOp().Set(Gf.Vec3f(-1.0, 2.0, 3.0))
        sn = gltf_mod._trs(neg.GetPrim(), 1.0)["scale"]
        self.assertLess(sn[0] * sn[1] * sn[2], 0.0)

    def test_c2_c3_c4_c5_c6_quad_uv_texture_pbr_winding_visibility_and_timesamples(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "albedo.png").write_bytes(PNG_1X1)
        stage = Usd.Stage.CreateNew(str(src / "quad.usda"))
        stage.SetStartTimeCode(0.0)
        stage.SetEndTimeCode(10.0)

        # Visible quad with faceVarying UVs, leftHanded orientation, doubleSided, time-sampled points
        q = UsdGeom.Mesh.Define(stage, "/quad")
        q.CreatePointsAttr().Set([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 0.0)
        q.CreateFaceVertexCountsAttr([4])
        q.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        q.CreateOrientationAttr(UsdGeom.Tokens.leftHanded)
        q.CreateDoubleSidedAttr(True)
        pv = UsdGeom.PrimvarsAPI(q).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
        pv.Set([(0.1, 0.2), (0.9, 0.2), (0.9, 0.8), (0.1, 0.8)])

        # Invisible mesh must be skipped
        inv_mesh = UsdGeom.Mesh.Define(stage, "/hidden")
        inv_mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        inv_mesh.CreateFaceVertexCountsAttr([3])
        inv_mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
        UsdGeom.Imageable(inv_mesh).MakeInvisible()

        # Connected UsdPreviewSurface -> UsdUVTexture -> UsdPrimvarReader_float2 + opacity + threshold
        mat = UsdShade.Material.Define(stage, "/mat")
        surf = UsdShade.Shader.Define(stage, "/mat/surf")
        surf.CreateIdAttr("UsdPreviewSurface")
        surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.2, 0.3, 0.4))
        surf.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(0.75)
        surf.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(0.5)
        tex = UsdShade.Shader.Define(stage, "/mat/tex")
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./albedo.png"))
        reader = UsdShade.Shader.Define(stage, "/mat/st")
        reader.CreateIdAttr("UsdPrimvarReader_float2")
        tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
        surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(), "rgb")
        mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI.Apply(q.GetPrim()).Bind(mat)

        # Time-sampled DistantLight: must not crash and must not have 4*pi factor
        dl = UsdLux.DistantLight.Define(stage, "/sun")
        dl.CreateIntensityAttr().Set(3.0, 0.0)
        dl.CreateExposureAttr().Set(1.0, 0.0)
        stage.GetRootLayer().Save()

        out = self.tmp / "out"
        res = api.export(stage, ["gltf"], out, jev_on=False)[0]
        self.assertTrue(res.validated, res.report.render_text())
        doc, blob = parse_glb(res.entry_file)

        # Only 1 mesh (invisible skipped)
        self.assertEqual(len(doc["meshes"]), 1)
        prim = doc["meshes"][0]["primitives"][0]
        uvs = read_accessor(doc, blob, prim["attributes"]["TEXCOORD_0"])
        self.assertEqual(len(uvs), 4)
        self.assertTrue(all(u > 0.05 and v > 0.1 for u, v in uvs), f"corrupted UVs: {uvs}")
        indices = read_accessor(doc, blob, prim["indices"])
        self.assertEqual(len(indices), 6)
        pos = read_accessor(doc, blob, prim["attributes"]["POSITION"])
        p0, p1, p2 = [Gf.Vec3f(*pos[i]) for i in indices[:3]]
        self.assertLess(((p1 - p0) ^ (p2 - p0))[2], 0.0, "leftHanded quad should flip winding to -Z")

        gmat = doc["materials"][0]
        self.assertTrue(gmat.get("doubleSided"))
        self.assertEqual(gmat.get("alphaMode"), "MASK")
        self.assertAlmostEqual(gmat.get("alphaCutoff"), 0.5, places=5)
        self.assertIn("baseColorTexture", gmat["pbrMetallicRoughness"])
        self.assertEqual([round(c, 3) for c in gmat["pbrMetallicRoughness"]["baseColorFactor"]], [1.0, 1.0, 1.0, 0.75])

        lights = doc["extensions"]["KHR_lights_punctual"]["lights"]
        self.assertEqual(len(lights), 1)
        self.assertAlmostEqual(lights[0]["intensity"], 6.0, places=4)

        self.assertNotIn("animations", doc)
        img_bv = doc["bufferViews"][doc["images"][0]["bufferView"]]
        self.assertNotIn("target", img_bv)

    def test_c7_m4_m2_m6_m7_instancer_variants_session_layer_and_units(self):
        src = self.tmp / "src"
        src.mkdir()
        stage = Usd.Stage.CreateNew(str(src / "complex.usda"))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)

        # Multi-prototype Xform PointInstancer with orientations, scales, invisibleIds
        inst = UsdGeom.PointInstancer.Define(stage, "/inst")
        p0 = UsdGeom.Xform.Define(stage, "/inst/protos/p0")
        m0 = UsdGeom.Mesh.Define(stage, "/inst/protos/p0/mesh")
        m0.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        m0.CreateFaceVertexCountsAttr([3])
        m0.CreateFaceVertexIndicesAttr([0, 1, 2])
        p1 = UsdGeom.Mesh.Define(stage, "/inst/protos/p1")
        p1.CreatePointsAttr([(0, 0, 0), (0, 1, 0), (0, 0, 1)])
        p1.CreateFaceVertexCountsAttr([3])
        p1.CreateFaceVertexIndicesAttr([0, 1, 2])
        inst.CreatePrototypesRel().SetTargets([p0.GetPath(), p1.GetPath()])
        inst.CreateProtoIndicesAttr([0, 0, 1])
        inst.CreatePositionsAttr([(0, 0, 0), (2, 0, 0), (4, 0, 0)])
        inst.CreateOrientationsAttr([Gf.Quath(1, 0, 0, 0), Gf.Quath(0.707, 0, 0.707, 0), Gf.Quath(1, 0, 0, 0)])
        inst.CreateScalesAttr([(1, 1, 1), (2, 2, 2), (3, 3, 3)])
        inst.CreateInvisibleIdsAttr([1])  # hide second instance of proto 0

        # Two disjoint material variant sets on separate asset prims (M2 + M6)
        for name, vset, c1, c2 in (("a", "lookA", (1, 0, 0), (0, 1, 0)), ("b", "lookB", (0, 0, 1), (1, 1, 0))):
            grp = UsdGeom.Xform.Define(stage, f"/{name}")
            mesh = UsdGeom.Mesh.Define(stage, f"/{name}/geo")
            mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
            mesh.CreateFaceVertexCountsAttr([3])
            mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
            mat = UsdShade.Material.Define(stage, f"/{name}/mat")
            sh = UsdShade.Shader.Define(stage, f"/{name}/mat/s")
            sh.CreateIdAttr("UsdPreviewSurface")
            mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
            vs = grp.GetPrim().GetVariantSets().AddVariantSet(vset)
            for opt, col in (("v1", c1), ("v2", c2)):
                vs.AddVariant(opt)
                vs.SetVariantSelection(opt)
                with vs.GetVariantEditContext():
                    sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*col))
            vs.SetVariantSelection("v1")

        stage.GetRootLayer().Save()
        self.assertFalse(stage.GetRootLayer().dirty)

        out = self.tmp / "out"
        res = api.export(stage, ["gltf"], out, jev_on=False)[0]
        self.assertTrue(res.validated, res.report.render_text())
        self.assertFalse(stage.GetRootLayer().dirty, "M2: variant export must not dirty the source root layer")

        doc, blob = parse_glb(res.entry_file)
        # Prototype meshes not duplicated: 2 regular meshes + 2 instanced prototype meshes = 4 meshes total
        self.assertEqual(len(doc["meshes"]), 4)
        root_node = doc["nodes"][doc["scenes"][0]["nodes"][0]]
        self.assertEqual(root_node["name"], "__usd_stage_root__")
        self.assertEqual(root_node["scale"], [0.01, 0.01, 0.01])

        inst_nodes = [n for n in doc["nodes"] if "EXT_mesh_gpu_instancing" in n.get("extensions", {})]
        self.assertEqual(len(inst_nodes), 2)
        for node in inst_nodes:
            attrs = node["extensions"]["EXT_mesh_gpu_instancing"]["attributes"]
            self.assertEqual(set(attrs), {"TRANSLATION", "ROTATION", "SCALE"})
            self.assertEqual(doc["accessors"][attrs["TRANSLATION"]]["count"], 1)
            for acc_idx in attrs.values():
                self.assertNotIn("target", doc["bufferViews"][doc["accessors"][acc_idx]["bufferView"]])

        variants = [v["name"] for v in doc["extensions"]["KHR_materials_variants"]["variants"]]
        self.assertEqual(variants, ["lookA:v1", "lookA:v2", "lookB:v1", "lookB:v2"])

    def test_h5_stage_time_start_zero_and_quaternion_continuity(self):
        src = self.tmp / "src"
        src.mkdir()
        stage = Usd.Stage.CreateNew(str(src / "anim.usda"))
        stage.SetStartTimeCode(0.0)
        stage.SetEndTimeCode(4.0)
        stage.SetTimeCodesPerSecond(24.0)
        m = UsdGeom.Mesh.Define(stage, "/spin")
        m.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        m.CreateFaceVertexCountsAttr([3])
        m.CreateFaceVertexIndicesAttr([0, 1, 2])
        rot = m.AddRotateYOp()
        for f, deg in ((0.0, 0.0), (1.0, 120.0), (2.0, 240.0), (3.0, 300.0), (4.0, 360.0)):
            rot.Set(deg, f)
        stage.GetRootLayer().Save()

        self.assertEqual(gltf_mod.stage_time(m.GetPrim()), 0.0)
        res = api.export(stage, ["gltf"], self.tmp / "out", jev_on=False)[0]
        self.assertTrue(res.validated, res.report.render_text())
        doc, blob = parse_glb(res.entry_file)
        anim = doc["animations"][0]
        paths = [c["target"]["path"] for c in anim["channels"]]
        self.assertEqual(paths, ["rotation"], "static translation/scale channels should be skipped")
        rot_sampler = anim["samplers"][0]
        quats = read_accessor(doc, blob, rot_sampler["output"])
        for a, b in zip(quats, quats[1:]):
            dot = sum(x * y for x, y in zip(a, b))
            self.assertGreaterEqual(dot, 0.0, f"quaternion flipped sign between {a} and {b}")
        for acc_idx in (rot_sampler["input"], rot_sampler["output"]):
            self.assertNotIn("target", doc["bufferViews"][doc["accessors"][acc_idx]["bufferView"]])


@unittest.skipIf(Usd is None, NEEDS_PXR)
class X3dUsdzFolderRegressions(TempCase):
    def test_x3d_quad_uvs_unique_def_physical_material_units_instancer_and_session_layer(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "a").mkdir()
        (src / "b").mkdir()
        (src / "a" / "tex.png").write_bytes(PNG_1X1)
        (src / "b" / "tex.png").write_bytes(PNG_1X1)

        stage = Usd.Stage.CreateNew(str(src / "scene.usda"))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)

        for idx, sub in enumerate(("a", "b")):
            m = UsdGeom.Mesh.Define(stage, f"/g{idx}/box")
            m.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
            m.CreateFaceVertexCountsAttr([4])
            m.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            pv = UsdGeom.PrimvarsAPI(m).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
            pv.Set([(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)])
            mat = UsdShade.Material.Define(stage, f"/g{idx}/mat")
            sh = UsdShade.Shader.Define(stage, f"/g{idx}/mat/s")
            sh.CreateIdAttr("UsdPreviewSurface")
            tex = UsdShade.Shader.Define(stage, f"/g{idx}/mat/t")
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(f"./{sub}/tex.png"))
            sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(), "rgb")
            mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)

        cam = UsdGeom.Camera.Define(stage, "/cam")
        cam.AddTranslateOp().Set(Gf.Vec3d(0, 0, 5))
        cam.AddRotateZOp().Set(45.0)

        dl = UsdLux.DistantLight.Define(stage, "/sun")
        dl.CreateIntensityAttr().Set(2.0, 1.0)
        stage.GetRootLayer().Save()

        out = self.tmp / "out"
        res = api.export(stage, ["x3d"], out, jev_on=False)[0]
        self.assertTrue(res.validated, res.report.render_text())
        root = ET.parse(res.entry_file).getroot()

        defs = [el.get("DEF") for el in root.iter() if el.get("DEF")]
        self.assertEqual(len(defs), len(set(defs)), f"duplicate X3D DEFs: {defs}")

        pms = list(root.iter("PhysicalMaterial"))
        self.assertEqual(len(pms), 2)
        tex_urls = []
        for pm in pms:
            self.assertEqual(pm.get("baseColor"), "1 1 1")
            it = pm.find("ImageTexture")
            self.assertIsNotNone(it)
            self.assertEqual(it.get("containerField"), "baseTexture")
            tex_urls.append(it.get("url"))
        self.assertEqual(len(set(tex_urls)), 2, "colliding texture basenames must copy to distinct files")

        ifs = root.find(".//IndexedFaceSet")
        tc_idx = ifs.get("texCoordIndex").split()
        self.assertEqual(len(tc_idx), 8)
        self.assertEqual(tc_idx[3], "-1")
        self.assertEqual(tc_idx[7], "-1")

        vp = root.find(".//Viewpoint")
        orient = [float(x) for x in vp.get("orientation").split()]
        self.assertGreater(abs(orient[2]), 0.9)
        self.assertAlmostEqual(orient[3], math.radians(45.0), places=2)

        d_el = root.find(".//DirectionalLight")
        self.assertIsNotNone(d_el)
        self.assertEqual(d_el.get("global"), "true")

    def test_usdz_atomic_replace_preserves_prior_archive_and_folder_writer_reusable(self):
        fixture = ROOT / "fixtures" / "stage" / "shot.usda"
        out = self.tmp / "out"
        stage = Usd.Stage.Open(str(fixture))
        first = api.export(stage, ["usdz"], out, jev_on=False)[0]
        self.assertTrue(first.validated)
        original_bytes = first.entry_file.read_bytes()

        with mock.patch.object(usdz_mod.UsdUtils, "CreateNewUsdzPackage", return_value=False):
            with self.assertRaises(RuntimeError):
                api.export(stage, ["usdz"], out, jev_on=False)
        self.assertEqual(first.entry_file.read_bytes(), original_bytes)

        w = folder_mod.UsdFolderWriter()
        self.assertIsNone(w.root)
        w.write(stage, inventory.gather(stage).inventory, self.tmp / "f1", ws.WebSettings())
        self.assertIsNone(w.root)


class HoudiniAdapterAndMetaToolsRegressions(TempCase):
    def test_hou_node_hudvs_limit_outdir_error_badge_and_missing_node(self):
        class FakeOpFailed(Exception):
            pass

        fake_hou = types.SimpleNamespace(
            OperationFailed=FakeOpFailed,
            NodeError=type("NodeError", (Exception,), {}),
            NodeWarning=type("NodeWarning", (Exception,), {}),
            getenv=lambda name: str(ROOT) if name == "WEBEXPORT_ROOT" else "",
            text=types.SimpleNamespace(
                expandString=mock.Mock(side_effect=AssertionError("must not double-expand"))
            ),
        )

        class FakeParm:
            def __init__(self, val):
                self._val = val

            def eval(self):
                return self._val

            def evalAsString(self):
                return str(self._val)

            def set(self, val):
                self._val = val

        class FakeNode:
            def __init__(self, parms):
                self._parms = {k: FakeParm(v) for k, v in parms.items()}

            def parm(self, name):
                return self._parms.get(name)

            def evalParm(self, name):
                return self._parms[name].eval()

            def inputConnections(self):
                return ()

            def cook(self, force=False):
                pass

        with mock.patch.dict(sys.modules, {"hou": fake_hou}):
            sys.modules.pop("webexport.hou_node", None)
            hou_node = importlib.import_module("webexport.hou_node")
            try:
                # web_hudvs_limit = 0 ignores web_hudvs text; = 1 parses it
                n0 = FakeNode({"web_title": "", "web_camera": "", "web_hudvs_limit": 0, "web_hudvs": "look,alt",
                               "web_showproxy": 0, "web_bgblur": 0.35, "web_exposure": 0.0, "web_showhud": 1})
                self.assertIsNone(hou_node.web_settings(n0).hudVariantSets)
                n1 = FakeNode({"web_title": "", "web_camera": "", "web_hudvs_limit": 1, "web_hudvs": "look, alt",
                               "web_showproxy": 0, "web_bgblur": 0.35, "web_exposure": 0.0, "web_showhud": 1})
                self.assertEqual(hou_node.web_settings(n1).hudVariantSets, ["look", "alt"])

                # _out_dir uses evalAsString without calling hou.text.expandString
                n_dir = FakeNode({"outdir": "C:/exports/$literal"})
                self.assertEqual(hou_node._out_dir(n_dir), "C:/exports/$literal")

                # Exception in on_check sets FIX FIRST badge instead of leaving stale GO
                n_err = FakeNode({"report": "old", "check_verdict": 0, "check_message": "",
                                  "check_enable": 0, "fmt_usd": 1})
                with mock.patch.object(hou_node, "incoming_stage", return_value=object()), \
                     mock.patch.object(api, "check", side_effect=RuntimeError("boom")):
                    hou_node.on_check({"node": n_err})
                self.assertEqual(n_err.evalParm("check_verdict"), hou_node.VERDICT_CODE["FIX FIRST"])
                self.assertEqual(n_err.evalParm("check_enable"), 1)
                self.assertIn("boom", n_err.evalParm("report"))

                # Missing node binary in on_preview writes clear error and does not crash
                n_prev = FakeNode({"report": "", "outdir": str(self.tmp)})
                with mock.patch.object(hou_node, "port_free", return_value=True), \
                     mock.patch.object(hou_node, "node_and_module_argv", return_value=["nonexistent_node_bin_xyz"]), \
                     mock.patch.object(hou_node.shutil, "which", return_value=None):
                    hou_node.on_preview({"node": n_prev, "webexport_no_browser": True})
                self.assertIn("was not found", n_prev.evalParm("report"))
            finally:
                sys.modules.pop("webexport.hou_node", None)

    def test_install_package_verify_checks_houdini_path(self):
        pkg_path = self.tmp / "webexport.json"
        bad = install_package.build_package(ROOT)
        bad["hpath"] = str(self.tmp / "wrong_houdini")
        pkg_path.write_text(json.dumps(bad), encoding="utf-8")
        problems = install_package.verify_target(pkg_path, ROOT)
        self.assertTrue(any("hpath" in p for p in problems), problems)

    def test_jev_answer_for_malformed_payload_and_web_census_m9(self):
        self.assertIsNone(jev.answer_for(None, "q1"))
        self.assertIsNone(jev.answer_for({}, "q1"))
        self.assertIsNone(jev.answer_for({"q1": "bad"}, "q1"))
        self.assertEqual(jev.answer_for({"q1": {"choice": "yes", "confidence": 0.9}}, "q1"),
                         {"choice": "yes", "confidence": 0.9})

        if Usd is not None:
            stage = Usd.Stage.CreateNew(str(self.tmp / "nd.usda"))
            mat = UsdShade.Material.Define(stage, "/m")
            sh = UsdShade.Shader.Define(stage, "/m/nd")
            sh.CreateIdAttr("ND_karma_custom_color3")
            mat.CreateSurfaceOutput("mtlx").ConnectToSource(sh.ConnectableAPI(), "out")
            stage.GetRootLayer().Save()
            c = web_census.census(self.tmp / "nd.usda")
            verdict, red, amber, info, _ = web_census.decide(c, jev_on=False)
            self.assertEqual(verdict, "GO, WITH NOTES")
            self.assertEqual(red, [])
            self.assertIn("shader nodes that need your eyes", {l for l, _, _ in amber})
            self.assertEqual(info, [])

    def test_build_router_check_exits_nonzero_on_mismatch(self):
        tasks_p = self.tmp / "tasks.json"
        exp_p = self.tmp / "expected.json"
        tasks_p.write_text(json.dumps({
            "tasks": [{"id": "T1", "title": "Fix glTF writer", "detail": "pure python", "needs": []}]
        }), encoding="utf-8")
        exp_p.write_text(json.dumps({"T1": "desk"}), encoding="utf-8")
        buf = io.StringIO()
        prev_key = os.environ.pop("TYPESAFE_API_KEY", None)
        try:
            with mock.patch.object(sys, "argv", ["build_router.py", str(tasks_p), "--check", str(exp_p)]), \
                 mock.patch.object(sys, "stdout", buf):
                rc = build_router.main()
            self.assertEqual(rc, 1)
        finally:
            if prev_key is not None:
                os.environ["TYPESAFE_API_KEY"] = prev_key

    def test_release_checks_filters_ci_workflow(self):
        captured = []

        def fake_run(*cmd):
            captured.append(cmd)
            if cmd[:2] == ("git", "branch"):
                return types.SimpleNamespace(returncode=0, stdout="main\n", stderr="")
            if cmd[:2] == ("git", "status"):
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if cmd[:2] == ("git", "fetch"):
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if cmd[:2] == ("git", "rev-parse"):
                return types.SimpleNamespace(returncode=0, stdout="abc1234\n", stderr="")
            if cmd[:2] == ("git", "ls-remote"):
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if cmd[:2] == ("gh", "run"):
                return types.SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps([{"workflowName": "CI", "event": "push", "status": "completed", "conclusion": "success"}]),
                    stderr="",
                )
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(release, "run", side_effect=fake_run):
            problems = release.checks("v9.9.9", "abc1234")
        self.assertEqual(problems, [])
        gh_run = [c for c in captured if c[:2] == ("gh", "run")]
        self.assertEqual(len(gh_run), 1)
        self.assertIn("--workflow", gh_run[0])
        self.assertIn("CI", gh_run[0])


if __name__ == "__main__":
    unittest.main()
