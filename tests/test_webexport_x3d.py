"""Tests for the X3D profile and writer (M8). Needs pxr; schema validation needs lxml and network.

    py -3.12 -m unittest tests.test_webexport_x3d -v

The M8 check from plan/web-export-blueprint.md is here: the fixture exports to a schema-valid
.x3d whose look Switch has three children and whose instancer emits 36 USE references. The
schema check downloads the official X3D 4.0 schema and its two includes once per run and skips
when the machine is offline or lxml is missing; the structural checks always run.
"""
import functools
import sys
import tempfile
import unittest
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from webexport import api  # noqa: E402
from webexport.report import LossReport  # noqa: E402
from webexport.writers.x3d import X3dWriter, validate_x3d  # noqa: E402

FIXTURE = ROOT / "fixtures" / "stage" / "shot.usda"
ANIMATED = ROOT / "fixtures" / "stage-animated" / "animated.usda"
SCHEMA = "https://www.web3d.org/specifications/x3d-4.0.xsd"
INCLUDES = ("x3d-4.0-Web3dExtensionsPrivate.xsd", "x3d-4.0-Web3dExtensionsPublic.xsd")


def texture_stage(folder: Path, image: str) -> Path:
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
    """A quad that animates one attribute an xform bake cannot carry: points, a shader input,
    or visibility. Everything else stays static."""
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


def scale_stage(folder: Path) -> Path:
    """A quad under a parent that scales from 1 to 2 over the range; it never moves or rotates."""
    folder.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(folder / "scale.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(24)
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/root").GetPrim())
    mover = UsdGeom.Xform.Define(stage, "/root/mover")
    scale = mover.AddScaleOp()
    scale.Set(Gf.Vec3f(1, 1, 1), Usd.TimeCode(1))
    scale.Set(Gf.Vec3f(2, 2, 2), Usd.TimeCode(24))
    mesh = UsdGeom.Mesh.Define(stage, "/root/mover/quad")
    mesh.CreatePointsAttr(QUAD)
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    stage.Save()
    return folder / "scale.usda"


@functools.lru_cache(maxsize=1)
def official_schema():
    """(schema, reason). Downloads the X3D 4.0 schema and its includes once per run."""
    try:
        from lxml import etree
    except ImportError:
        return None, "needs lxml"
    cache = Path(tempfile.mkdtemp(prefix="x3d_schema_"))
    try:
        for name, url in [("x3d-4.0.xsd", SCHEMA)] + [(n, SCHEMA.replace("x3d-4.0.xsd", n)) for n in INCLUDES]:
            urllib.request.urlretrieve(url, cache / name)
        return etree.XMLSchema(etree.parse(str(cache / "x3d-4.0.xsd"))), None
    except Exception as exc:  # offline or a moved schema: the structural checks still run
        return None, f"needs network ({type(exc).__name__})"


class X3dExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, path=FIXTURE):
        return api.export(Usd.Stage.Open(str(path)), ["x3d"], self.out)

    def test_the_fixture_exports_to_a_valid_x3d(self):
        result = self.export()[0]
        self.assertTrue(result.files, result.report.render_text())
        self.assertEqual(result.entry_file.suffix, ".x3d")
        self.assertTrue(result.validated)
        ok, problems = validate_x3d(result.entry_file)
        self.assertTrue(ok, problems)

        root = ET.parse(str(result.entry_file)).getroot()
        self.assertEqual((root.tag, root.get("version"), root.get("profile")), ("X3D", "4.0", "Immersive"))
        self.assertEqual(len(list(root.iter("Viewpoint"))), 1)
        self.assertTrue(list(root.iter("PhysicalMaterial")))

        # the look variant set becomes a Switch with one group per look
        switches = list(root.iter("Switch"))
        looks = [s for s in switches if len(list(s)) == 3]
        self.assertEqual(len(looks), 1, "the fixture has one three-look variant set")
        self.assertIn(looks[0].get("whichChoice"), {"0", "1", "2"})

        # the PointInstancer becomes DEF/USE: one prototype, 36 uses
        uses = [el for el in root.iter() if el.get("USE")]
        self.assertEqual(len(uses), 36)
        defs = {el.get("DEF") for el in root.iter() if el.get("DEF")}
        self.assertTrue(all(u.get("USE") in defs for u in uses))

        # the SphereLight is a PointLight; the RectLight has no X3D equivalent and is reported
        self.assertEqual(len(list(root.iter("PointLight"))), 1)
        self.assertIn("light_dropped", {i.feature for i in result.report.items})

    def test_an_animated_stage_bakes_time_sensors_and_interpolators(self):
        """The animated fixture turns a full circle over 121 frames, like the glTF test."""
        result = self.export(ANIMATED)[0]
        self.assertTrue(result.validated, result.report.render_text())
        root = ET.parse(str(result.entry_file)).getroot()

        sensors = list(root.iter("TimeSensor"))
        self.assertEqual(len(sensors), 1)
        self.assertEqual(sensors[0].get("loop"), "true")
        self.assertAlmostEqual(float(sensors[0].get("cycleInterval")), 5.0)  # 120 frames at 24 fps

        rotations = list(root.iter("OrientationInterpolator"))
        positions = list(root.iter("PositionInterpolator"))
        self.assertEqual(len(rotations), 1)
        self.assertEqual(positions, [], "the fixture only rotates; a static channel is skipped")
        keys = rotations[0].get("key").split()
        values = rotations[0].get("keyValue").split()
        self.assertEqual(len(keys), 121, "one key per frame of the range")
        self.assertEqual(len(values), 121 * 4, "SFRotation is axis + angle per key")
        self.assertEqual(float(keys[0]), 0.0)
        self.assertAlmostEqual(float(keys[-1]), 1.0)
        self.assertAlmostEqual(float(values[-1]), 2 * 3.141592653589793, places=3,
                               msg="the full turn must survive the bake, not collapse to identity")

        # every ROUTE names a DEF that exists, and the transform it drives has one
        defs = {el.get("DEF") for el in root.iter() if el.get("DEF")}
        routes = list(root.iter("ROUTE"))
        self.assertEqual(len(routes), 2, "sensor to interpolator, interpolator to transform")
        for route in routes:
            self.assertIn(route.get("fromNode"), defs)
            self.assertIn(route.get("toNode"), defs)
        targets = {route.get("toNode") for route in routes if route.get("toField") == "set_rotation"}
        self.assertEqual(len(targets), 1)
        self.assertTrue(any(el.get("DEF") in targets for el in root.iter("Transform")))
        self.assertTrue(validate_x3d(result.entry_file)[0])

        features = {item.feature: item for item in result.report.items}
        self.assertEqual(features["animation"].severity, "note")
        self.assertEqual(features["animation"].count, 1, "one mesh's local-to-world moves")
        self.assertIn("xform animation on 1 prim", features["animation"].becomes)

    def test_a_time_range_without_motion_reports_no_animation(self):
        """A declared range is not motion: nothing moves, so there is nothing to report."""
        with tempfile.TemporaryDirectory() as folder:
            stage_path = time_range_stage(Path(folder))
            report = api.check(Usd.Stage.Open(str(stage_path)), ["x3d"])[0]
            features = {item.feature for item in report.items}
            self.assertNotIn("animation", features)
            self.assertNotIn("animated_attribute", features)

    def test_an_animated_non_transform_attribute_is_reported(self):
        """X3D bakes xforms only; animated points, shader inputs and visibility are one item."""
        expected = {"points": "/root/quad", "shader": "/root/mtl/surf", "visibility": "/root/quad"}
        for target, prim in expected.items():
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                stage_path = attribute_stage(Path(folder), target)
                report = api.check(Usd.Stage.Open(str(stage_path)), ["x3d"])[0]
                features = {item.feature: item for item in report.items}
                self.assertIn("animated_attribute", features)
                item = features["animated_attribute"]
                self.assertEqual(item.severity, "changes")
                self.assertEqual(item.becomes,
                                 "the current time sample only; only transforms are baked")
                self.assertIn(prim, item.examples)

    def test_a_transform_only_animation_is_not_an_animated_attribute(self):
        report = api.check(Usd.Stage.Open(str(ANIMATED)), ["x3d"])[0]
        features = {item.feature for item in report.items}
        self.assertIn("animation", features, "the rotation is still reported as baked")
        self.assertNotIn("animated_attribute", features)

    def test_a_scale_only_animation_is_reported_not_dropped_silently(self):
        """X3D has no scale interpolator: the report names the gap and the writer leaves the
        start pose rather than inventing an animation it cannot express."""
        with tempfile.TemporaryDirectory() as folder:
            stage_path = scale_stage(Path(folder))
            report = api.check(Usd.Stage.Open(str(stage_path)), ["x3d"])[0]
            features = {item.feature: item for item in report.items}
            animation = features["animation"]
            self.assertEqual(animation.severity, "changes")
            self.assertEqual(animation.becomes,
                             "rotation and translation baked; scale change not representable in X3D")
            self.assertIn("/root/mover", animation.examples)

            result = api.export(Usd.Stage.Open(str(stage_path)), ["x3d"], self.out)[0]
            self.assertTrue(result.validated, result.report.render_text())
            root = ET.parse(str(result.entry_file)).getroot()
            self.assertEqual(list(root.iter("TimeSensor")), [],
                             "nothing writable moves, so no sensor is invented")
            self.assertEqual(list(root.iter("OrientationInterpolator")), [])
            self.assertEqual(list(root.iter("PositionInterpolator")), [])

    def test_the_file_validates_against_the_official_schema(self):
        schema, reason = official_schema()
        if schema is None:
            self.skipTest(reason)
        from lxml import etree
        result = self.export()[0]
        document = etree.parse(str(result.entry_file))
        self.assertTrue(schema.validate(document), [e.message for e in schema.error_log][:5])

    def test_the_profile_reports_what_x3d_cannot_carry(self):
        report = api.check(Usd.Stage.Open(str(FIXTURE)), ["x3d"])[0]
        features = {i.feature: i for i in report.items}
        self.assertEqual(report.verdict, "GO, WITH NOTES")
        self.assertEqual(features["subdivision"].severity, "changes")
        self.assertEqual(features["material_variant"].severity, "changes")  # geometry repeats per look
        self.assertIn("light_dropped", features)
        self.assertIn("instancing", features)

    def test_a_png_is_copied_beside_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            stage_path = texture_stage(Path(folder), "checker.png")
            result = api.export(Usd.Stage.Open(str(stage_path)), ["x3d"], self.out)[0]
            self.assertTrue(result.validated, result.report.render_text())
            self.assertIn("checker.png", [f.name for f in result.files])
            root = ET.parse(str(result.entry_file)).getroot()
            self.assertEqual([t.get("url") for t in root.iter("ImageTexture")], ["checker.png"])

    def test_an_undecodable_image_blocks_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            stage_path = texture_stage(Path(folder), "missing.rat")
            result = api.export(Usd.Stage.Open(str(stage_path)), ["x3d"], self.out)[0]
            self.assertEqual(result.report.verdict, "FIX FIRST")
            self.assertEqual(result.files, [])
            self.assertFalse(result.validated)
            self.assertFalse(self.out.exists(), "a blocking report must not create the folder")

    def test_the_writer_refuses_a_report_from_another_stage(self):
        with self.assertRaises(ValueError):
            X3dWriter(report=LossReport.from_items("x3d", []), root="/somewhere/else.usda").write(
                Usd.Stage.Open(str(FIXTURE)), None, self.out, None)


if __name__ == "__main__":
    unittest.main()
