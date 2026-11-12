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
