"""Tests for exporting an in-memory (Houdini LOP) stage and the wave 2 decisions.

A LOP stage's root layer is anonymous and carries anonymous sublayers (session, viewport state,
purpose, solos) plus /HoudiniLayerInfo. api.export() flattens the layer stack, stages it to a
file, strips /HoudiniLayerInfo, and hands that file to the existing USD-folder writer. These
tests simulate the stage with Usd.Stage.CreateInMemory() and anonymous sublayers, so they run in
CI with usd-core and no Houdini.

    py -3.12 -m unittest tests.test_webexport_inmemory -v
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

try:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt
except ImportError:  # no pxr on this machine
    Usd = None
else:  # only pxr may be missing: a broken export must fail here, never skip
    import web_census
    from webexport import api
    from webexport.inventory import build_inventory, gather
    from webexport.profiles.usd_folder import UsdFolderProfile
    from webexport.report import LossItem, LossReport, verdict_for
    from webexport.settings import read_settings
    from webexport.validate import Validation
    from webexport.writers.usd_folder import UsdFolderWriter

PNG = b"\x89PNG\r\n\x1a\n"
NEEDS_PXR = "needs pxr (pip install usd-core, or run under hython)"


def mesh_card(stage, path, material):
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0),
                                         Gf.Vec3f(1, 1, 0), Gf.Vec3f(0, 1, 0)]))
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)


def preview_material(stage, path):
    mat = UsdShade.Material.Define(stage, path)
    surf = UsdShade.Shader.Define(stage, f"{path}/surf")
    surf.CreateIdAttr("UsdPreviewSurface")
    mat.CreateSurfaceOutput().ConnectToSource(surf.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    return mat


def clean_stage():
    """A LOP-shaped stage with an anonymous root and a clean, exportable /hero."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.Xform.Define(stage, "/hero")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/hero"))
    mesh_card(stage, "/hero/geo/card", preview_material(stage, "/hero/mtl/look"))
    return stage


def anon_sublayers(stage, *names):
    """Append anonymous sublayers, the way a LOP stage carries its session and viewport layers."""
    layers = []
    for name in names:
        layer = Sdf.Layer.CreateAnonymous(name)
        stage.GetRootLayer().subLayerPaths.append(layer.identifier)
        layers.append(layer)
    return layers


def add_look_variants(stage, override_layer):
    """look = red|blue defined on the root; override_layer, an anonymous LOP sublayer, selects blue.

    The selection has to live in the sublayer, because the root layer is the strongest layer in
    the stack: a selection authored there would win over the sublayer's.
    """
    prim = stage.GetPrimAtPath("/hero")
    vset = prim.GetVariantSets().AddVariantSet("look")
    for name, color in (("red", (1.0, 0.0, 0.0)), ("blue", (0.0, 0.0, 1.0))):
        vset.AddVariant(name)
        vset.SetVariantSelection(name)  # author this variant's contents
        with vset.GetVariantEditContext():
            prim.CreateAttribute("user:color", Sdf.ValueTypeNames.Float3).Set(color)
    vset.ClearVariantSelection()  # the root defines the options, not the choice
    with Usd.EditContext(stage, override_layer):
        vset.SetVariantSelection("blue")  # the anonymous sublayer chooses


def add_texture(stage, file):
    shader = UsdShade.Shader.Define(stage, "/hero/mtl/look/tex")
    shader.CreateIdAttr("UsdUVTexture")
    shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(file))


def lop_stage(title="LOP stage", texture=None):
    """An in-memory stage shaped like a live LOP stage: anonymous root and sublayers,
    /HoudiniLayerInfo, a variant selection overridden in a sublayer, and /WebSettings there too."""
    stage = clean_stage()
    layers = anon_sublayers(stage, "LOP:session", "LOP:viewport", "LOP:purpose", "LOP:solos")
    add_look_variants(stage, layers[0])
    with Usd.EditContext(stage, layers[0]):
        settings = stage.DefinePrim("/WebSettings", "Scope")
        settings.CreateAttribute("web:title", Sdf.ValueTypeNames.String, custom=True).Set(title)
    info = stage.DefinePrim("/HoudiniLayerInfo", "Scope")
    info.CreateAttribute("houdini:layerInfo", Sdf.ValueTypeNames.String, custom=True).Set("session")
    if texture:
        add_texture(stage, texture)
    return stage, layers


def export(stage, out_dir, settings=None):
    return api.export(stage, ["usd_folder"], out_dir, settings, jev_on=False)[0]


def blocks(result):
    return {i.feature for i in result.report.items if i.severity == "blocks"}


def item(result, severity, feature):
    found = [i for i in result.report.items if i.severity == severity and i.feature == feature]
    return found[0] if found else None


class TempCase(unittest.TestCase):
    """A fresh temp folder per test, and never a live Jev call."""

    def setUp(self):
        os.environ.pop("TYPESAFE_API_KEY", None)
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="webexport_inmem_")))
        self.addCleanup(shutil.rmtree, self.tmp, True)


@unittest.skipIf(Usd is None, NEEDS_PXR)
class LopStageExport(TempCase):
    """The mission's check: an in-memory LOP stage exports a folder that reopens intact."""

    def setUp(self):
        super().setUp()
        self.stage, self.layers = lop_stage()
        self.out = self.tmp / "web"
        self.result = export(self.stage, self.out)

    def test_folder_is_written_and_validated(self):
        r = self.result
        self.assertEqual(r.report.verdict, "GO")
        self.assertEqual(r.entry_file, self.out / "hero.usda")  # named after the default prim
        self.assertTrue(r.entry_file.is_file())
        self.assertEqual(r.files[0], r.entry_file)
        self.assertEqual(r.bytes, sum(f.stat().st_size for f in r.files))
        self.assertTrue(r.validated)

    def test_variants_reopen_intact(self):
        stage = Usd.Stage.Open(str(self.result.entry_file))
        vset = stage.GetPrimAtPath("/hero").GetVariantSet("look")
        self.assertEqual(sorted(vset.GetVariantNames()), ["blue", "red"])
        self.assertEqual(vset.GetVariantSelection(), "blue")  # the anonymous layer's override won
        self.assertEqual(stage.GetPrimAtPath("/hero").GetAttribute("user:color").Get(), Gf.Vec3f(0, 0, 1))

    def test_websettings_from_the_anonymous_sublayer_survive(self):
        reopened = Usd.Stage.Open(str(self.result.entry_file))
        self.assertEqual(read_settings(reopened).title, "LOP stage")

    def test_houdini_layer_info_is_not_written(self):
        reopened = Usd.Stage.Open(str(self.result.entry_file))
        self.assertTrue(reopened)  # keep the stage alive: an expired prim is falsy too
        self.assertFalse(reopened.GetPrimAtPath("/HoudiniLayerInfo"))
        self.assertTrue(reopened.GetPrimAtPath("/hero"))
        for f in self.out.rglob("*"):
            if f.suffix.lower() in {".usd", ".usda", ".usdc"}:
                self.assertNotIn("HoudiniLayerInfo", f.read_text(encoding="utf-8", errors="ignore"))

    def test_census_on_the_output_says_go(self):
        self.assertEqual(web_census.decide(web_census.census(self.result.entry_file), jev_on=False)[0], "GO")

    def test_no_preview_url_carries_a_label(self):
        self.assertIsNone(self.result.preview_url)
        note = item(self.result, "note", "preview_url")
        self.assertIsNotNone(note)
        self.assertIn("stages/", note.becomes)

    def test_verdict_agrees_with_its_items(self):
        self.assertEqual(self.result.report.verdict, verdict_for(self.result.report.items))

    def test_exporting_does_not_change_the_in_memory_stage(self):
        self.assertEqual(self.stage.GetPrimAtPath("/hero").GetVariantSet("look").GetVariantSelection(), "blue")
        self.assertTrue(self.stage.GetPrimAtPath("/HoudiniLayerInfo"))
        again = export(self.stage, self.tmp / "again")
        self.assertTrue(again.validated)
        self.assertEqual(again.entry_file, self.tmp / "again" / "hero.usda")


@unittest.skipIf(Usd is None, NEEDS_PXR)
class BlockingLopStage(TempCase):
    def test_a_blocking_finding_writes_nothing(self):
        stage, _layers = lop_stage(texture="./missing.png")  # relative to an anonymous layer
        out = self.tmp / "web"
        r = export(stage, out)
        self.assertEqual(r.report.verdict, "FIX FIRST")
        self.assertIn("missing_file", blocks(r))
        self.assertEqual((r.files, r.bytes, r.validated), ([], 0, False))
        self.assertEqual(r.entry_file, out / "hero.usda")
        self.assertFalse(out.exists())  # not even the folder


@unittest.skipIf(Usd is None, NEEDS_PXR)
class CheckInMemory(TempCase):
    def test_check_reports_a_lop_stage_without_writing_it(self):
        stage, _layers = lop_stage()
        reports = api.check(stage, jev_on=False)
        self.assertEqual([(r.format, r.verdict) for r in reports], [("usd_folder", "GO")])
        self.assertEqual(stage.GetPrimAtPath("/hero").GetVariantSet("look").GetVariantSelection(), "blue")

    def test_check_reports_a_blocking_lop_stage(self):
        stage, _layers = lop_stage(texture="./missing.png")
        self.assertEqual([r.verdict for r in api.check(stage, jev_on=False)], ["FIX FIRST"])


@unittest.skipIf(Usd is None, NEEDS_PXR)
class FileLayerDependencies(TempCase):
    """A file layer inside the anonymous stack keeps its relative asset path through the flatten."""

    def test_a_file_sublayers_texture_is_copied_in_and_made_relative(self):
        lib = self.tmp / "lib"
        lib.mkdir()
        (lib / "wood.png").write_bytes(PNG)
        layer_stage = Usd.Stage.CreateNew(str(lib / "look.usda"))
        preview_material(layer_stage, "/look")
        shader = UsdShade.Shader.Define(layer_stage, "/look/tex")
        shader.CreateIdAttr("UsdUVTexture")
        shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./wood.png"))
        layer_stage.GetRootLayer().Save()

        stage = clean_stage()
        stage.GetRootLayer().subLayerPaths.append(str(lib / "look.usda"))
        out = self.tmp / "web"
        r = export(stage, out)
        self.assertEqual(r.report.verdict, "GO")
        self.assertTrue(r.validated)
        copied = list(out.rglob("wood.png"))
        self.assertEqual(len(copied), 1)
        reopened = Usd.Stage.Open(str(r.entry_file))
        path = UsdShade.Shader(reopened.GetPrimAtPath("/look/tex")).GetInput("file").Get()
        self.assertTrue(path.path.startswith(("./", "../")))
        self.assertTrue(Path(path.resolvedPath).resolve().is_relative_to(out.resolve()))


class VerdictConsistency(unittest.TestCase):
    """Decision verdict-consistency: a report cannot disagree with its own items."""

    def test_a_contradictory_verdict_is_refused(self):
        with self.assertRaises(ValueError):
            LossReport("usd_folder", "GO", [LossItem("blocks", "missing_file", 1)])
        with self.assertRaises(ValueError):
            LossReport("usd_folder", "FIX FIRST", [LossItem("note", "proxy", 1)])

    def test_from_json_recomputes_the_verdict(self):
        blocks = {"format": "usd_folder", "verdict": "GO",
                  "items": [{"severity": "blocks", "feature": "missing_file", "count": 1}]}
        self.assertEqual(LossReport.from_json(blocks).verdict, "FIX FIRST")
        none = {"format": "usd_folder", "verdict": "GO, WITH NOTES", "items": []}
        self.assertEqual(LossReport.from_json(none).verdict, "GO")


@unittest.skipIf(Usd is None, NEEDS_PXR)
class ValidationReporting(TempCase):
    """Decision validation-reporting: validated=False always carries its reason."""

    def test_each_problem_becomes_a_changes_item(self):
        stage, _layers = lop_stage()
        problems = ["outside the folder: C:/textures/wood.png", "/WebSettings is not a Scope in hero.usda"]
        fake = Validation(self.tmp / "hero.usda", verdict="GO", problems=problems)
        with mock.patch("webexport.writers.usd_folder.validate_folder", return_value=fake):
            r = export(stage, self.tmp / "web")
        self.assertFalse(r.validated)
        found = [i for i in r.report.items if i.feature == "validation"]
        self.assertEqual([i.becomes for i in found], problems)
        self.assertTrue(all(i.severity == "changes" for i in found))
        self.assertEqual(r.report.verdict, "GO, WITH NOTES")
        self.assertEqual(r.report.verdict, verdict_for(r.report.items))

    def test_census_fix_first_without_problems_still_has_a_reason(self):
        stage, _layers = lop_stage()
        fake = Validation(self.tmp / "hero.usda", verdict="FIX FIRST")
        with mock.patch("webexport.writers.usd_folder.validate_folder", return_value=fake):
            r = export(stage, self.tmp / "web")
        self.assertFalse(r.validated)
        found = item(r, "changes", "validation")
        self.assertIsNotNone(found)
        self.assertIn("FIX FIRST", found.becomes)


@unittest.skipIf(Usd is None, NEEDS_PXR)
class ReportStageBinding(TempCase):
    """Decision report-stage-binding: a report for one stage cannot authorise writing another."""

    def test_a_report_cannot_authorise_writing_a_different_stage(self):
        first, _layers = lop_stage()
        second, _layers2 = lop_stage()
        facts = gather(first, track_types=web_census.UNTESTED_TYPES)
        profile = UsdFolderProfile(stage=first, facts=facts, jev_on=False)
        report = LossReport.from_items("usd_folder", profile.assess(facts.inventory))
        writer = UsdFolderWriter(report=report, plan=profile.plan, root=first.GetRootLayer().identifier)
        out = self.tmp / "web"
        with self.assertRaises(ValueError):
            writer.write(second, build_inventory(second), out, None)
        self.assertFalse(out.exists())

    def test_a_supplied_report_must_name_its_root_layer(self):
        with self.assertRaises(ValueError):
            UsdFolderWriter(report=LossReport.from_items("usd_folder", []))

    def test_the_shipping_path_binds_the_report_to_its_own_stage(self):
        first, _layers = lop_stage()
        self.assertTrue(export(first, self.tmp / "web").validated)


if __name__ == "__main__":
    unittest.main()
