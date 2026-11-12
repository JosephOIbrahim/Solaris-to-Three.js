"""Tests for the USDZ profile and writer (task U1). Needs pxr only, like the rest of the core.

    py -3.12 -m unittest tests.test_webexport_usdz -v

The U1 check lives here: the fixture exports to one .usdz that opens with Usd.Stage.Open,
holds /WebSettings, keeps every asset path relative, and passes the web census; a stage with
a .rat texture blocks and writes nothing.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

import web_census  # noqa: E402
from webexport import api, textures  # noqa: E402
from webexport.inventory import is_absolute  # noqa: E402
from webexport.profiles.base import Profile  # noqa: E402
from webexport.profiles.usdz import UsdzProfile  # noqa: E402
from webexport.report import LossReport  # noqa: E402
from webexport.settings import read_settings  # noqa: E402
from webexport.writers.base import Writer  # noqa: E402
from webexport.writers.usdz import STAGING, UsdzWriter, validate_usdz  # noqa: E402

FIXTURE = ROOT / "fixtures" / "stage" / "shot.usda"
FLAT = ROOT / "fixtures" / "stage-flat" / "hero_flat.usdz"


def export_one(path, out_dir, formats=("usdz",), settings=None):
    return api.export(Usd.Stage.Open(str(path)), list(formats), out_dir, settings, jev_on=False)[0]


def blocks(result):
    return {i.feature for i in result.report.items if i.severity == "blocks"}


def features(result, severity):
    return {i.feature: i for i in result.report.items if i.severity == severity}


def texture_stage(folder: Path, image: str) -> Path:
    """A quad whose UsdPreviewSurface samples an image next to it."""
    stage = Usd.Stage.CreateNew(str(folder / "tex.usda"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/root").GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, "/root/quad")
    mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0),
                                         Gf.Vec3f(1, 0, 1), Gf.Vec3f(0, 0, 1)]))
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
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


class UsdzExportTest(unittest.TestCase):
    """The fixture in one archive: the U1 check."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="webexport_usdz_"))
        cls.out = cls.tmp / "out"
        cls.result = export_one(FIXTURE, cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_the_fixture_exports_to_one_valid_usdz(self):
        r = self.result
        self.assertEqual(r.format, "usdz")
        self.assertEqual(r.report.verdict, "GO, WITH NOTES")
        self.assertEqual(r.entry_file, self.out / "hero.usdz")
        self.assertEqual(r.files, [r.entry_file])
        self.assertEqual(r.bytes, r.entry_file.stat().st_size)
        self.assertTrue(r.validated)
        self.assertEqual(validate_usdz(r.entry_file), (True, []))
        self.assertIsNone(r.preview_url)

    def test_the_archive_opens_with_websettings_in_its_root(self):
        stage = Usd.Stage.Open(str(self.result.entry_file))
        self.assertTrue(stage)
        self.assertFalse(stage.GetRootLayer().anonymous)
        prim = stage.GetPrimAtPath("/WebSettings")
        self.assertTrue(prim)
        self.assertEqual(prim.GetTypeName(), "Scope")
        self.assertTrue(stage.GetRootLayer().GetPrimAtPath("/WebSettings"))
        self.assertEqual(read_settings(stage).title, "shot.usda")
        self.assertTrue(stage.GetPrimAtPath("/hero/geo/render/ring/pebbles"))  # from the payload
        self.assertTrue(stage.GetPrimAtPath("/cameras/render_cam"))

    def test_every_asset_path_in_every_layer_is_relative(self):
        stage = Usd.Stage.Open(str(self.result.entry_file))
        found = []
        for layer in stage.GetUsedLayers():
            if layer.anonymous:
                continue
            for path in textures.authored_paths(layer):
                found.append((layer.identifier, path))
                self.assertFalse(is_absolute(path), f"{layer.identifier}: {path}")
                self.assertTrue(path.startswith(("./", "../")), f"{layer.identifier}: {path}")
        self.assertTrue(found, "the fixture authors asset paths; none were found")

    def test_the_web_census_says_go(self):
        verdict = web_census.decide(web_census.census(self.result.entry_file), jev_on=False)[0]
        self.assertNotEqual(verdict, "FIX FIRST", self.result.report.render_text())
        self.assertEqual(verdict, "GO")

    def test_the_staging_folder_is_removed(self):
        kept = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(kept, ["hero.usdz"])
        self.assertFalse((self.out / STAGING).exists())

    def test_the_profile_reports_the_quicklook_limits(self):
        r = self.result
        changes = features(r, "changes")
        self.assertIn("materialx", changes)  # Quick Look reads little of a network
        self.assertEqual(changes["light_not_read"].count, 2)  # the RectLight and the DomeLight
        notes = features(r, "note")
        for kept in ("composition_packed", "variant_set", "instancing", "subdivision",
                     "preview_surface", "camera", "punctual_light", "texture_packed", "preview"):
            self.assertIn(kept, notes)
        self.assertFalse(set(features(r, "note")) & {"rect_light", "dome_light", "proxy_guide_prims"})

    def test_a_second_export_replaces_the_archive(self):
        out = self.tmp / "again"
        first = export_one(FIXTURE, out)
        second = export_one(FIXTURE, out)
        self.assertTrue(first.validated and second.validated)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["hero.usdz"])
        self.assertFalse((out / STAGING).exists())


class RatTexture(unittest.TestCase):
    """A .rat blocks, because Quick Look and the packaged USD cannot read it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webexport_usdz_rat_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_rat_texture_blocks_and_writes_nothing(self):
        (self.tmp / "rust.rat").write_bytes(b"not really a rat")
        src = texture_stage(self.tmp, "./rust.rat")
        out = self.tmp / "out"
        r = export_one(src, out)
        self.assertEqual(r.report.verdict, "FIX FIRST")
        self.assertIn("texture_format", blocks(r))
        self.assertEqual((r.files, r.bytes, r.validated), ([], 0, False))
        self.assertEqual(r.entry_file, out / "root.usdz")
        self.assertFalse(out.exists(), "a blocking report must not create the folder")


class FourFormatsOnALopStage(unittest.TestCase):
    """The shape of a live LOP stage: an anonymous root with a file sublayer, which
    api._materialized saves to a temporary file before any report is built.

    The glTF and X3D writers read variants by selecting each option and restoring the original.
    That round trip can leave the *source* root layer dirty; when the USDZ report was built after
    those writers it saw an unsaved layer and blocked. Every report must be built before the
    first writer runs, so all four formats go and all four files are written.
    """

    def test_all_four_formats_report_go_and_write_the_usdz(self):
        tmp = Path(tempfile.mkdtemp(prefix="webexport_usdz_lop_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        stage = Usd.Stage.CreateInMemory()
        stage.GetRootLayer().defaultPrim = "hero"
        stage.GetRootLayer().subLayerPaths = [str(FIXTURE.resolve())]
        out = tmp / "out"

        results = api.export(stage, ["usd_folder", "gltf", "x3d", "usdz"], out, jev_on=False)
        self.assertEqual([r.format for r in results], ["usd_folder", "gltf", "x3d", "usdz"])
        for result in results:
            self.assertNotEqual(result.report.verdict, "FIX FIRST", result.report.render_text())
        by_format = {r.format: r for r in results}
        self.assertTrue(by_format["usdz"].files)
        self.assertTrue(by_format["usdz"].validated)
        for name in ("hero.usda", "hero.glb", "hero.x3d", "hero.usdz"):
            self.assertTrue((out / name).is_file(), name)
        archive = Usd.Stage.Open(str(out / "hero.usdz"))
        self.assertTrue(archive.GetPrimAtPath("/WebSettings"))


class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webexport_usdz_contracts_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_profile_and_writer_meet_the_protocols(self):
        self.assertIsInstance(UsdzProfile(), Profile)
        self.assertIsInstance(UsdzWriter(), Writer)

    def test_check_writes_nothing(self):
        reports = api.check(Usd.Stage.Open(str(FIXTURE)), ["usdz"], jev_on=False)
        self.assertEqual([(r.format, r.verdict) for r in reports], [("usdz", "GO, WITH NOTES")])
        self.assertFalse(any(self.tmp.iterdir()))

    def test_the_writer_refuses_a_report_from_another_stage(self):
        writer = UsdzWriter(report=LossReport.from_items("usdz", []), root="/somewhere/else.usda")
        with self.assertRaises(ValueError):
            writer.write(Usd.Stage.Open(str(FIXTURE)), None, self.tmp / "out", None)

    def test_the_writer_needs_its_report(self):
        with self.assertRaises(ValueError):
            UsdzWriter(report=None).write(Usd.Stage.Open(str(FIXTURE)), None, self.tmp / "out", None)

    def test_a_usdz_source_blocks_because_websettings_has_nowhere_to_go(self):
        reports = api.check(Usd.Stage.Open(str(FLAT)), ["usdz"], jev_on=False)
        self.assertIn("root_format", {i.feature for i in reports[0].items if i.severity == "blocks"})
        out = self.tmp / "out"
        r = export_one(FLAT, out)
        self.assertEqual((r.report.verdict, r.files), ("FIX FIRST", []))
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
