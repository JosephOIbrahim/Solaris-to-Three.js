"""Tests for the glTF profile and writer (M7). Needs pxr only, like the rest of the core.

    py -3.12 -m unittest tests.test_webexport_gltf -v

The M7 check from plan/web-export-blueprint.md lives here: the fixture exports to a .glb whose
JSON chunk parses, whose accessor counts fit their buffer views, and whose images resolve.
"""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from webexport import api  # noqa: E402
from webexport.profiles.gltf import GltfProfile  # noqa: E402
from webexport.report import LossItem, LossReport  # noqa: E402
from webexport.writers.gltf import GltfWriter, validate_glb  # noqa: E402

FIXTURE = ROOT / "fixtures" / "stage" / "shot.usda"


def read_glb(path):
    raw = Path(path).read_bytes()
    json_len = struct.unpack("<I", raw[12:16])[0]
    return json.loads(raw[20:20 + json_len])


def texture_stage(folder: Path, image: str) -> Path:
    """A quad whose UsdPreviewSurface samples a PNG next to it."""
    (folder / "checker.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x02\x00\x00\x00\x02\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    stage = Usd.Stage.CreateNew(str(folder / "tex.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/root").GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, "/root/quad")
    mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0),
                                         Gf.Vec3f(1, 0, 1), Gf.Vec3f(0, 0, 1)]))
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                            UsdGeom.Tokens.vertex).Set(
        Vt.Vec2fArray([Gf.Vec2f(0, 0), Gf.Vec2f(1, 0), Gf.Vec2f(1, 1), Gf.Vec2f(0, 1)]))
    material = UsdShade.Material.Define(stage, "/root/mtl")
    surface = UsdShade.Shader.Define(stage, "/root/mtl/surf")
    surface.CreateIdAttr("UsdPreviewSurface")
    tex = UsdShade.Shader.Define(stage, "/root/mtl/tex")
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(image))
    tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
    surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.GetOutput("rgb"))
    material.CreateSurfaceOutput().ConnectToSource(surface.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    stage.Save()
    return folder / "tex.usda"


QUAD = [Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(1, 0, 1), Gf.Vec3f(0, 0, 1)]


def attribute_stage(folder: Path, target: str) -> Path:
    """A quad that animates one attribute a transform bake cannot carry: points, a shader
    input, or visibility. Everything else stays static."""
    folder.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(folder / "attr.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(24)
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/root").GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, "/root/quad")
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    points = mesh.CreatePointsAttr()
    if target == "points":
        points.Set(QUAD, Usd.TimeCode(1))
        points.Set([Gf.Vec3f(p[0], p[1] + 1.0, p[2]) for p in QUAD], Usd.TimeCode(24))
    else:
        points.Set(QUAD)
    if target == "visibility":
        visibility = UsdGeom.Imageable(mesh.GetPrim()).CreateVisibilityAttr()
        visibility.Set(UsdGeom.Tokens.inherited, Usd.TimeCode(1))
        visibility.Set(UsdGeom.Tokens.invisible, Usd.TimeCode(24))
    if target == "shader":
        material = UsdShade.Material.Define(stage, "/root/mtl")
        surface = UsdShade.Shader.Define(stage, "/root/mtl/surf")
        surface.CreateIdAttr("UsdPreviewSurface")
        roughness = surface.CreateInput("roughness", Sdf.ValueTypeNames.Float)
        roughness.Set(0.1, Usd.TimeCode(1))
        roughness.Set(0.9, Usd.TimeCode(24))
        material.CreateSurfaceOutput().ConnectToSource(
            surface.CreateOutput("surface", Sdf.ValueTypeNames.Token))
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    stage.Save()
    return folder / "attr.usda"


def time_range_stage(folder: Path) -> Path:
    """A static quad with a declared time range and nothing that moves."""
    folder.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(folder / "still.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(24)
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/root").GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, "/root/quad")
    mesh.CreatePointsAttr(QUAD)
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateSubdivisionSchemeAttr("none")
    stage.Save()
    return folder / "still.usda"


class GltfExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, path=FIXTURE):
        return api.export(Usd.Stage.Open(str(path)), ["gltf"], self.out)

    def test_the_fixture_exports_to_a_structurally_valid_glb(self):
        stage = Usd.Stage.Open(str(FIXTURE))
        results = self.export()
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.format, "gltf")
        self.assertTrue(result.files, result.report.render_text())
        self.assertEqual(result.entry_file.suffix, ".glb")
        self.assertTrue(result.validated)

        ok, problems = validate_glb(result.entry_file)
        self.assertTrue(ok, problems)
        doc = read_glb(result.entry_file)

        self.assertEqual(doc["asset"]["version"], "2.0")
        for i, accessor in enumerate(doc["accessors"]):
            view = doc["bufferViews"][accessor["bufferView"]]
            width = 4 if accessor["componentType"] in (5125, 5126) else 2
            arity = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[accessor["type"]]
            self.assertLessEqual(accessor["count"] * arity * width, view["byteLength"],
                                 f"accessor {i} overruns its buffer view")
        for image in doc.get("images", []):
            self.assertIn("bufferView", image)
            self.assertLess(image["bufferView"], len(doc["bufferViews"]))

        # the fixture: 8 meshes plus the instancer's prototype, 36 instances, a camera, a light
        self.assertGreaterEqual(len(doc["meshes"]), 8)
        self.assertIn("EXT_mesh_gpu_instancing", doc["extensionsUsed"])
        self.assertEqual(len(doc["cameras"]), 1)
        instancing = [n for n in doc["nodes"] if "EXT_mesh_gpu_instancing" in n.get("extensions", {})]
        self.assertEqual(len(instancing), 1)
        transforms = doc["accessors"][
            instancing[0]["extensions"]["EXT_mesh_gpu_instancing"]["attributes"]["TRANSLATION"]]
        self.assertEqual(transforms["count"], 36)

    def test_the_profile_reports_what_gltf_cannot_carry(self):
        report = api.check(Usd.Stage.Open(str(FIXTURE)), ["gltf"])[0]
        features = {item.feature: item for item in report.items}
        self.assertEqual(report.verdict, "GO, WITH NOTES")
        self.assertIn("subdivision", features)       # catmullClark ships as its cage
        self.assertEqual(features["subdivision"].severity, "changes")
        self.assertIn("light_dropped", features)     # the RectLight has no punctual equivalent
        self.assertIn("material_variant", features)  # encoded, and reported as a note not a loss
        self.assertEqual(features["material_variant"].severity, "note")
        self.assertIn("instancing", features)
        # the fixture binds no image to a surface, so there is nothing to embed and no note
        self.assertNotIn("embedded_image", features)

    def test_material_look_variants_are_encoded(self):
        """The fixture's 'look' set has three looks; all three must be in the file."""
        result = self.export()[0]
        doc = read_glb(result.entry_file)
        self.assertIn("KHR_materials_variants", doc["extensionsUsed"])
        variants = [v["name"] for v in doc["extensions"]["KHR_materials_variants"]["variants"]]
        self.assertEqual(sorted(variants), ["chrome", "ember", "gold"])

        mapped = {}
        for mesh in doc["meshes"]:
            for primitive in mesh["primitives"]:
                maps = primitive.get("extensions", {}).get("KHR_materials_variants", {}).get("mappings", [])
                if maps:
                    mapped[mesh["name"]] = maps
        self.assertIn("orb_std", mapped, "the orb is the mesh the look variant set changes")
        mappings = mapped["orb_std"]
        self.assertEqual(len(mappings), 3)
        self.assertEqual({m["variants"][0] for m in mappings}, {0, 1, 2},
                         "every declared variant must be covered")
        looks = [tuple(round(c, 2) for c in
                       doc["materials"][m["material"]]["pbrMetallicRoughness"]["baseColorFactor"][:3])
                 for m in mappings]
        self.assertEqual(len(set(looks)), 3, f"each look needs its own material, got {looks}")

        by_name = {variants[m["variants"][0]]: doc["materials"][m["material"]]["pbrMetallicRoughness"]
                   for m in mappings}
        self.assertAlmostEqual(by_name["ember"]["metallicFactor"], 0.0, places=3)
        self.assertAlmostEqual(by_name["gold"]["metallicFactor"], 1.0, places=3)
        self.assertAlmostEqual(by_name["chrome"]["metallicFactor"], 1.0, places=3)
        self.assertLess(by_name["chrome"]["roughnessFactor"], by_name["ember"]["roughnessFactor"])

    def test_an_animated_stage_exports_time_samples(self):
        """The animated fixture turns a full circle over 121 frames; every frame must be in the file."""
        animated = ROOT / "fixtures" / "stage-animated" / "animated.usda"
        result = api.export(Usd.Stage.Open(str(animated)), ["gltf"], self.out)[0]
        self.assertTrue(result.validated, result.report.render_text())
        doc = read_glb(result.entry_file)
        self.assertEqual(len(doc["animations"]), 1)
        animation = doc["animations"][0]
        paths = {channel["target"]["path"] for channel in animation["channels"]}
        self.assertIn("rotation", paths)
        self.assertTrue(animation["samplers"])
        for sampler in animation["samplers"]:
            times = doc["accessors"][sampler["input"]]
            values = doc["accessors"][sampler["output"]]
            self.assertEqual(times["count"], 121, "one sample per frame of the range")
            self.assertEqual(values["count"], times["count"])
            self.assertEqual(times["type"], "SCALAR")
            self.assertAlmostEqual(times["min"][0], 0.0, places=6)
            self.assertAlmostEqual(times["max"][0], 5.0, places=3)  # 120 frames at 24 fps
            self.assertEqual(sampler["interpolation"], "LINEAR")
            for channel in animation["channels"]:
                self.assertLess(channel["target"]["node"], len(doc["nodes"]))
        # the profile reports the observed xform motion as a note now that xforms are baked
        features = {item.feature: item for item in result.report.items}
        self.assertIn("animation", features)
        note = features["animation"]
        self.assertEqual(note.severity, "note")
        self.assertEqual(note.count, 1, "one mesh's local-to-world moves")
        self.assertIn("xform animation on 1 prim", note.becomes)

    def test_a_time_range_without_motion_reports_no_animation(self):
        """A declared range is not motion: nothing moves, so there is nothing to report."""
        with tempfile.TemporaryDirectory() as folder:
            stage_path = time_range_stage(Path(folder))
            report = api.check(Usd.Stage.Open(str(stage_path)), ["gltf"])[0]
            features = {item.feature for item in report.items}
            self.assertNotIn("animation", features)
            self.assertNotIn("animated_attribute", features)

    def test_an_animated_non_transform_attribute_is_reported(self):
        """glTF bakes xforms only; animated points, shader inputs and visibility are one item."""
        expected = {"points": "/root/quad", "shader": "/root/mtl/surf", "visibility": "/root/quad"}
        for target, prim in expected.items():
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                stage_path = attribute_stage(Path(folder), target)
                report = api.check(Usd.Stage.Open(str(stage_path)), ["gltf"])[0]
                features = {item.feature: item for item in report.items}
                self.assertIn("animated_attribute", features)
                item = features["animated_attribute"]
                self.assertEqual(item.severity, "changes")
                self.assertEqual(item.becomes,
                                 "the current time sample only; only transforms are baked")
                self.assertIn(prim, item.examples)

    def test_a_transform_only_animation_is_not_an_animated_attribute(self):
        animated = ROOT / "fixtures" / "stage-animated" / "animated.usda"
        report = api.check(Usd.Stage.Open(str(animated)), ["gltf"])[0]
        features = {item.feature for item in report.items}
        self.assertIn("animation", features, "the rotation is still reported as baked")
        self.assertNotIn("animated_attribute", features)

    def test_a_png_samples_into_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            stage_path = texture_stage(Path(folder), "checker.png")
            result = api.export(Usd.Stage.Open(str(stage_path)), ["gltf"], self.out)[0]
            self.assertTrue(result.validated, result.report.render_text())
            doc = read_glb(result.entry_file)
            self.assertEqual(len(doc["images"]), 1)
            self.assertEqual(doc["images"][0]["mimeType"], "image/png")
            self.assertIn("baseColorTexture",
                          doc["materials"][0]["pbrMetallicRoughness"])

    def test_an_undecodable_image_blocks_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            stage_path = texture_stage(Path(folder), "missing.rat")
            result = api.export(Usd.Stage.Open(str(stage_path)), ["gltf"], self.out)[0]
            self.assertEqual(result.report.verdict, "FIX FIRST")
            self.assertEqual(result.files, [])
            self.assertFalse(result.validated)
            self.assertFalse(self.out.exists(), "a blocking report must not create the folder")

    def test_the_writer_refuses_a_report_from_another_stage(self):
        report = LossReport.from_items("gltf", [])
        writer = GltfWriter(report=report, root="/somewhere/else.usda")
        with self.assertRaises(ValueError):
            writer.write(Usd.Stage.Open(str(FIXTURE)), None, self.out, None)

    def test_the_writer_needs_its_report(self):
        with self.assertRaises(ValueError):
            GltfWriter(report=None).write(Usd.Stage.Open(str(FIXTURE)), None, self.out, None)


if __name__ == "__main__":
    unittest.main()
