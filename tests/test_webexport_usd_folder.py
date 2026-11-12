"""Tests for the USD folder export (mission M2): profile, writer, validate, api.
Needs pxr: `pip install usd-core`, or run under hython.

    python -m unittest tests.test_webexport_usd_folder -v
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

try:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt
except ImportError:  # no pxr on this machine
    Usd = None
else:  # only pxr may be missing: a broken export must fail here, never skip
    import web_census
    from webexport import api
    from webexport.inventory import build_inventory, is_absolute
    from webexport.profiles.base import Profile
    from webexport.profiles.usd_folder import UsdFolderProfile
    from webexport.settings import WebSettings, read_settings, write_settings
    from webexport.validate import census_of, validate_folder
    from webexport.writers.base import Writer
    from webexport.writers.usd_folder import UsdFolderWriter, plan_folder

CLEAN = ROOT / "fixtures" / "stage" / "shot.usda"
DIRTY = ROOT / "fixtures" / "stage-dirty" / "dirty.usda"
ESCAPE = ROOT / "fixtures" / "stage-escape" / "export" / "shot.usda"
NEEDS_PXR = "needs pxr (pip install usd-core, or run under hython)"
PNG = b"\x89PNG\r\n\x1a\n"
USD_EXTS = {".usd", ".usda", ".usdc"}


def export_one(stage, out_dir, settings=None):
    if not isinstance(stage, Usd.Stage):
        stage = Usd.Stage.Open(str(stage))
    return api.export(stage, ["usd_folder"], out_dir, settings, jev_on=False)[0]


def digest(*folders):
    """Every file under the folders, by path, with a hash of its bytes."""
    out = {}
    for folder in folders:
        for f in sorted(Path(folder).rglob("*")):
            if f.is_file():
                out[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def layer_paths(folder):
    """(layer file, asset path) for every asset path in every USD layer under the folder. Read with
    USD's own ModifyAssetPaths, not with the export's code, so the check is independent."""
    out = []
    for f in sorted(Path(folder).rglob("*")):
        if f.suffix.lower() in USD_EXTS:
            layer = Sdf.Layer.FindOrOpen(str(f))
            UsdUtils.ModifyAssetPaths(layer, lambda p, f=f: (out.append((f, p)), p)[1],
                                      keepEmptyPathsInArrays=True)
    return [(f, p) for f, p in out if p]


def blocks(result):
    return {i.feature for i in result.report.items if i.severity == "blocks"}


def items(result, severity):
    return {i.feature: i for i in result.report.items if i.severity == severity}


def census_verdict(path):
    return web_census.decide(web_census.census(path), jev_on=False)[0]


def mesh_card(stage, path, material):
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(1, 1, 0), Gf.Vec3f(0, 1, 0)]))
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)


def preview_material(stage, path):
    mat = UsdShade.Material.Define(stage, path)
    surf = UsdShade.Shader.Define(stage, f"{path}/surf")
    surf.CreateIdAttr("UsdPreviewSurface")
    mat.CreateSurfaceOutput().ConnectToSource(surf.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    return mat


def texture(stage, path, file):
    sh = UsdShade.Shader.Define(stage, path)
    sh.CreateIdAttr("UsdUVTexture")
    sh.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(file))
    return sh


def new_root(path):
    """A saved root layer with /scene as its default prim, a preview material and one bound card."""
    path.parent.mkdir(parents=True, exist_ok=True)
    s = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(s, UsdGeom.Tokens.y)
    s.SetDefaultPrim(UsdGeom.Xform.Define(s, "/scene").GetPrim())
    mesh_card(s, "/scene/geo/card", preview_material(s, "/scene/mtl/look"))
    return s


def write_files(base, *names):
    for name in names:
        (base / name).parent.mkdir(parents=True, exist_ok=True)
        (base / name).write_bytes(PNG)


class TempCase(unittest.TestCase):
    """A fresh temp folder per test, and never a live Jev call."""

    def setUp(self):
        os.environ.pop("TYPESAFE_API_KEY", None)
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="webexport_")))
        self.addCleanup(shutil.rmtree, self.tmp, True)


@unittest.skipIf(Usd is None, NEEDS_PXR)
class CleanFixture(unittest.TestCase):
    """The mission's check: fixtures/stage/shot.usda exports GO into a folder that stands alone."""

    @classmethod
    def setUpClass(cls):
        os.environ.pop("TYPESAFE_API_KEY", None)
        cls.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="webexport_clean_")))
        cls.before = digest(CLEAN.parent)
        cls.result = export_one(CLEAN, cls.tmp / "hero")
        cls.out = cls.tmp / "hero"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_verdict_is_go(self):
        self.assertEqual(self.result.report.verdict, "GO")
        self.assertEqual(self.result.report.format, "usd_folder")

    def test_folder_is_written_and_validated(self):
        r = self.result
        self.assertEqual(r.entry_file, self.out / "shot.usda")
        self.assertTrue(r.entry_file.is_file())
        self.assertEqual(r.files[0], r.entry_file)
        self.assertEqual(r.bytes, sum(f.stat().st_size for f in r.files))
        self.assertTrue(r.validated)

    def test_source_layout_is_kept(self):
        rel = sorted(f.relative_to(self.out).as_posix() for f in self.result.files)
        self.assertEqual(rel, ["env/sky.hdr", "hero/asset.usda", "shot.usda"])

    def test_output_opens(self):
        stage = Usd.Stage.Open(str(self.result.entry_file))
        self.assertTrue(stage)
        self.assertTrue(stage.GetPrimAtPath("/hero/geo/render/ring/pebbles"))  # from the payload
        self.assertTrue(stage.GetPrimAtPath("/cameras/render_cam"))

    def test_zero_absolute_paths_in_every_layer(self):
        paths = layer_paths(self.out)
        self.assertTrue(paths)
        for layer, p in paths:
            self.assertFalse(is_absolute(p), f"{layer}: {p}")
            self.assertTrue(p.startswith(("./", "../")), f"{layer}: {p}")

    def test_websettings_in_the_root_layer(self):
        stage = Usd.Stage.Open(str(self.result.entry_file))
        self.assertTrue(stage.GetRootLayer().GetPrimAtPath("/WebSettings"))
        self.assertEqual(stage.GetPrimAtPath("/WebSettings").GetTypeName(), "Scope")
        self.assertEqual(read_settings(stage).title, "shot.usda")

    def test_census_on_the_output_says_go(self):
        self.assertEqual(census_verdict(self.result.entry_file), "GO")

    def test_source_is_untouched(self):
        self.assertEqual(digest(CLEAN.parent), self.before)

    def test_bridged_lights_are_notes(self):
        notes = items(self.result, "note")
        self.assertEqual((notes["rect_light"].count, notes["dome_light"].count), (1, 1))
        self.assertIn("proxy_guide_prims", notes)
        self.assertFalse(items(self.result, "changes"))

    def test_no_preview_url_outside_stages(self):
        self.assertIsNone(self.result.preview_url)


@unittest.skipIf(Usd is None, NEEDS_PXR)
class DirtyFixture(TempCase):
    def test_fix_first_writes_nothing(self):
        out = self.tmp / "dirty"
        r = export_one(DIRTY, out)
        self.assertEqual(r.report.verdict, "FIX FIRST")
        self.assertEqual((r.files, r.bytes, r.validated), ([], 0, False))
        self.assertEqual(r.entry_file, out / "dirty.usda")
        self.assertFalse(out.exists())  # not even the folder

    def test_census_findings_keep_their_buckets(self):
        r = export_one(DIRTY, self.tmp / "dirty")
        # absolute_path is left out: whether D:/textures/wood.png exists depends on the machine
        self.assertLessEqual({"composition_error", "udim_texture", "texture_format", "missing_file",
                              "materialx_no_uv"}, blocks(r))
        self.assertEqual(set(items(r, "changes")), {"dome_texture", "approximated_light", "unrecognised_shader"})
        self.assertIn("untested_materialx_node", items(r, "note"))
        self.assertEqual(r.report.jev_note, "Jev off (--no-jev)")
        self.assertIn("FIX FIRST", r.report.render_text())


@unittest.skipIf(Usd is None, NEEDS_PXR)
class OutsideFiles(TempCase):
    """A stage with a reference and textures outside its folder, and one at an absolute path, all real
    files. The export pulls them in, every path ends up relative, and the census on the output says GO."""

    def setUp(self):
        super().setUp()
        write_files(self.tmp, "src/tex/ok.png", "lib/wood.png", "lib2/wood.png", "abs/grain.png")
        lib = Usd.Stage.CreateNew(str(self.tmp / "lib" / "asset.usda"))
        lib.SetDefaultPrim(UsdGeom.Xform.Define(lib, "/asset").GetPrim())
        texture(lib, "/asset/wood", "./wood.png")  # its own neighbour, relative to lib/
        lib.GetRootLayer().Save()
        s = new_root(self.tmp / "src" / "shot.usda")
        s.DefinePrim("/scene/asset", "Xform").GetReferences().AddReference("../lib/asset.usda")
        self.grain = (self.tmp / "abs" / "grain.png").as_posix()  # absolute, and real
        for name, file in (("ok", "./tex/ok.png"), ("wood", "../lib/wood.png"), ("wood2", "../lib2/wood.png"),
                           ("grain", self.grain)):
            texture(s, f"/scene/mtl/look/{name}", file)
        s.GetRootLayer().Save()
        self.src = self.tmp / "src" / "shot.usda"
        self.before = digest(self.tmp)
        self.out = self.tmp / "out"
        self.result = export_one(self.src, self.out)

    def test_the_census_alone_would_say_fix_first(self):
        self.assertEqual(census_verdict(self.src), "FIX FIRST")

    def test_copied_in_files_are_notes_and_the_verdict_is_go(self):
        r = self.result
        self.assertEqual(r.report.verdict, "GO")
        notes = items(r, "note")
        self.assertEqual(notes["outside_file"].count, 3)  # asset.usda and two wood.png; lib/wood.png once
        self.assertEqual(notes["absolute_path"].count, 1)
        self.assertEqual(notes["absolute_path"].becomes, "copied into the export folder, path made relative")
        self.assertEqual(notes["absolute_path"].examples, [self.grain])

    def test_outside_files_land_in_one_subfolder_without_collisions(self):
        rel = sorted(f.relative_to(self.out).as_posix() for f in self.result.files)
        self.assertEqual(rel, ["external/abs/grain.png", "external/lib/asset.usda", "external/lib/wood.png",
                               "external/lib2/wood.png", "shot.usda", "tex/ok.png"])

    def test_every_path_is_relative_and_resolves_inside(self):
        found = {(f.relative_to(self.out).as_posix(), p) for f, p in layer_paths(self.out)}
        self.assertEqual(found, {
            ("shot.usda", "./external/lib/asset.usda"), ("shot.usda", "./tex/ok.png"),
            ("shot.usda", "./external/lib/wood.png"), ("shot.usda", "./external/lib2/wood.png"),
            ("shot.usda", "./external/abs/grain.png"), ("external/lib/asset.usda", "./wood.png"),
        })
        stage = Usd.Stage.Open(str(self.result.entry_file))
        for prim in ("/scene/mtl/look/wood", "/scene/mtl/look/grain", "/scene/asset/wood"):
            resolved = UsdShade.Shader(stage.GetPrimAtPath(prim)).GetInput("file").Get().resolvedPath
            self.assertTrue(Path(resolved).resolve().is_relative_to(self.out.resolve()), prim)

    def test_output_is_validated_and_the_census_says_go(self):
        self.assertTrue(self.result.validated)
        self.assertEqual(census_verdict(self.result.entry_file), "GO")
        self.assertEqual(validate_folder(self.result.entry_file).problems, [])

    def test_sources_are_untouched(self):
        after = {k: v for k, v in digest(self.tmp).items() if not k.startswith(str(self.out))}
        self.assertEqual(after, self.before)


@unittest.skipIf(Usd is None, NEEDS_PXR)
class EscapeFixture(TempCase):
    def test_copyable_outside_files_are_notes_the_rest_blocks(self):
        out = self.tmp / "escape"
        r = export_one(ESCAPE, out)
        self.assertEqual(r.report.verdict, "FIX FIRST")
        self.assertEqual(blocks(r), {"udim_texture", "missing_file"})  # a UDIM set, and tex/missing.png
        self.assertEqual(items(r, "note")["outside_file"].count, 2)    # ../lib/asset.usda and ../lib/wood.png
        self.assertFalse(out.exists())


@unittest.skipIf(Usd is None, NEEDS_PXR)
class UnseenByTheCensus(TempCase):
    """The census reads the stage as composed now. The writer's plan reads every layer, so a file in a
    variant that isn't selected is still checked, and still copied in."""

    def variant_stage(self, b_file):
        write_files(self.tmp, "src/tex/ok.png")
        s = new_root(self.tmp / "src" / "shot.usda")
        tex = UsdShade.Shader.Define(s, "/scene/mtl/look/tex")
        tex.CreateIdAttr("UsdUVTexture")
        vset = s.GetPrimAtPath("/scene").GetVariantSets().AddVariantSet("look")
        for name, file in (("a", "./tex/ok.png"), ("b", b_file)):
            vset.AddVariant(name)
            vset.SetVariantSelection(name)
            with vset.GetVariantEditContext():  # only in the variants: a local value would always win
                tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(file))
        vset.SetVariantSelection("a")
        s.GetRootLayer().Save()
        return self.tmp / "src" / "shot.usda"

    def test_missing_file_in_an_unselected_variant_blocks(self):
        src = self.variant_stage("./tex/gone.png")
        self.assertNotEqual(census_verdict(src), "FIX FIRST")  # the census can't see variant b
        r = export_one(src, self.tmp / "out")
        self.assertEqual(r.report.verdict, "FIX FIRST")
        missing = items(r, "blocks")["missing_file"]
        self.assertEqual((missing.count, missing.examples), (1, ["./tex/gone.png"]))
        self.assertFalse((self.tmp / "out").exists())

    def test_rat_in_an_unselected_variant_blocks(self):
        write_files(self.tmp, "src/tex/rust.rat")
        r = export_one(self.variant_stage("./tex/rust.rat"), self.tmp / "out")
        self.assertIn("texture_format", blocks(r))

    def test_outside_file_in_an_unselected_variant_is_copied_in(self):
        write_files(self.tmp, "lib/extra.png")
        r = export_one(self.variant_stage("../lib/extra.png"), self.tmp / "out")
        self.assertEqual(r.report.verdict, "GO")
        self.assertTrue(r.validated)
        self.assertTrue((self.tmp / "out" / "external" / "extra.png").is_file())
        stage = Usd.Stage.Open(str(r.entry_file))
        stage.GetPrimAtPath("/scene").GetVariantSet("look").SetVariantSelection("b")
        file = UsdShade.Shader(stage.GetPrimAtPath("/scene/mtl/look/tex")).GetInput("file").Get()
        self.assertEqual(file.path, "./external/extra.png")
        self.assertTrue(file.resolvedPath)


@unittest.skipIf(Usd is None, NEEDS_PXR)
class UnsavedLayers(TempCase):
    """The writer copies files, so an edit that lives only in memory would be lost. It says so
    instead. An anonymous root layer is the exception: that is a LOP stage, and api.export
    flattens it to a file first (see test_webexport_inmemory.py)."""

    def test_unsaved_edit_blocks(self):
        shutil.copytree(CLEAN.parent, self.tmp / "src")
        stage = Usd.Stage.Open(str(self.tmp / "src" / "shot.usda"))
        stage.DefinePrim("/added_in_memory", "Xform")
        r = export_one(stage, self.tmp / "out")
        self.assertEqual(r.report.verdict, "FIX FIRST")
        self.assertIn("unsaved_layer", blocks(r))
        self.assertFalse((self.tmp / "out").exists())

    def test_usdz_root_blocks(self):
        r = export_one(ROOT / "fixtures" / "stage-flat" / "hero_flat.usdz", self.tmp / "out")
        self.assertIn("root_format", blocks(r))  # a package: /WebSettings has nowhere to go
        self.assertFalse((self.tmp / "out").exists())

    def test_in_memory_root_is_flattened_and_written(self):
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.Xform.Define(stage, "/scene")
        r = export_one(stage, self.tmp / "out")
        self.assertEqual(r.report.verdict, "GO")
        self.assertEqual(r.entry_file, self.tmp / "out" / "stage.usda")
        self.assertTrue(r.entry_file.is_file())
        self.assertTrue(r.validated)
        reopened = Usd.Stage.Open(str(r.entry_file))
        self.assertTrue(reopened.GetPrimAtPath("/scene"))


@unittest.skipIf(Usd is None, NEEDS_PXR)
class ExportFolder(TempCase):
    def setUp(self):
        super().setUp()
        self.src = self.tmp / "src"
        shutil.copytree(CLEAN.parent, self.src)
        self.stage = Usd.Stage.Open(str(self.src / "shot.usda"))
        self.before = digest(self.src)

    def assert_refused(self, out_dir):
        with self.assertRaises(ValueError):
            export_one(self.stage, out_dir)
        self.assertEqual(digest(self.src), self.before)

    def test_refuses_the_source_folder(self):
        self.assert_refused(self.src)

    def test_refuses_a_folder_inside_the_source_folder(self):
        self.assert_refused(self.src / "web")
        self.assertFalse((self.src / "web").exists())

    def test_refuses_a_folder_around_the_source_folder(self):
        self.assert_refused(self.tmp)

    def test_reexport_reads_the_new_source_not_a_stale_layer(self):
        out = self.tmp / "out"
        first = export_one(self.stage, out, WebSettings(title="One"))
        held = Usd.Stage.Open(str(first.entry_file))  # keeps the first output's layers in Sdf's registry
        self.stage.DefinePrim("/added", "Xform")
        self.stage.GetRootLayer().Save()  # the source changes on disk
        second = export_one(self.stage, out, WebSettings(title="Two"))
        self.assertTrue(second.validated)
        fresh = Usd.Stage.Open(str(second.entry_file))
        self.assertTrue(fresh.GetPrimAtPath("/added"))
        self.assertEqual(read_settings(fresh).title, "Two")
        self.assertIn('"Two"', second.entry_file.read_text())
        self.assertTrue(held.GetPrimAtPath("/added"))  # the held stage was reloaded, not left stale

    def test_outside_subfolder_never_collides_with_the_sources_own(self):
        write_files(self.tmp, "src2/external/mine.png", "lib/wood.png")
        s = new_root(self.tmp / "src2" / "shot.usda")
        texture(s, "/scene/mtl/look/mine", "./external/mine.png")
        texture(s, "/scene/mtl/look/wood", "../lib/wood.png")
        s.GetRootLayer().Save()
        dests = {p.dest for p in plan_folder(s.GetRootLayer()).files}
        self.assertEqual(dests, {"shot.usda", "external/mine.png", "external_2/wood.png"})
        r = export_one(s, self.tmp / "out2")
        self.assertTrue(r.validated)

    @unittest.skipUnless(os.name == "nt" and Path(tempfile.gettempdir()).resolve().drive.lower()
                         != ROOT.resolve().drive.lower(), "needs the temp folder and the repo on two drives")
    def test_outside_files_on_two_drives_get_a_folder_each(self):
        write_files(self.tmp, "lib/wood.png")
        s = new_root(self.tmp / "src3" / "shot.usda")
        texture(s, "/scene/mtl/look/near", "../lib/wood.png")
        far = (ROOT / "fixtures" / "stage-escape" / "lib" / "wood.png").resolve().as_posix()  # read, never written
        texture(s, "/scene/mtl/look/far", far)
        s.GetRootLayer().Save()
        dests = sorted(p.dest for p in plan_folder(s.GetRootLayer()).files)
        tmp_drive, repo_drive = (d.rstrip(":").lower() for d in (self.tmp.drive, ROOT.resolve().drive))
        self.assertEqual(dests, sorted(["shot.usda", f"external/{tmp_drive}/wood.png",
                                        f"external/{repo_drive}/wood.png"]))


@unittest.skipIf(Usd is None, NEEDS_PXR)
class Settings(TempCase):
    def test_none_keeps_the_stages_own_websettings(self):
        shutil.copytree(CLEAN.parent, self.tmp / "src")
        stage = Usd.Stage.Open(str(self.tmp / "src" / "shot.usda"))
        write_settings(stage, WebSettings(title="Hero", showHud=False, backgroundBlur=0.6))
        stage.GetRootLayer().Save()
        r = export_one(stage, self.tmp / "out")
        got = read_settings(Usd.Stage.Open(str(r.entry_file)))
        self.assertEqual((got.title, got.showHud, got.backgroundBlur), ("Hero", False, 0.6))

    def test_given_settings_are_written(self):
        want = WebSettings(title="Mine", camera="/cameras/render_cam", hudVariantSets=["look"],
                           showProxy=True, backgroundBlur=0.8, exposureOffset=-1.0, showHud=False)
        r = export_one(CLEAN, self.tmp / "out", want)
        self.assertEqual(read_settings(Usd.Stage.Open(str(r.entry_file))), want)


@unittest.skipIf(Usd is None, NEEDS_PXR)
class Contracts(TempCase):
    def test_profile_and_writer_meet_the_protocols(self):
        self.assertIsInstance(UsdFolderProfile(), Profile)
        self.assertIsInstance(UsdFolderWriter(), Writer)

    def test_census_of_a_live_stage_matches_the_census(self):
        for path in (CLEAN, DIRTY, ESCAPE):
            live = census_of(Usd.Stage.Open(str(path)))
            disk = web_census.census(path)
            self.assertEqual({k: v for k, v in live.items() if k != "stage"},
                             {k: v for k, v in disk.items() if k != "stage"}, path.name)

    def test_assess_alone_opens_the_root_layer(self):
        inv = build_inventory(Usd.Stage.Open(str(CLEAN)))
        found = UsdFolderProfile(jev_on=False).assess(inv)
        self.assertFalse([i for i in found if i.severity != "note"])

    def test_the_writer_alone_still_refuses_a_blocked_stage(self):
        stage = Usd.Stage.Open(str(DIRTY))
        r = UsdFolderWriter(jev_on=False).write(stage, build_inventory(stage), self.tmp / "out", None)
        self.assertEqual((r.report.verdict, r.files), ("FIX FIRST", []))
        self.assertFalse((self.tmp / "out").exists())

    def test_check_writes_nothing(self):
        reports = api.check(Usd.Stage.Open(str(CLEAN)), ["usd_folder"], jev_on=False)
        self.assertEqual([(r.format, r.verdict) for r in reports], [("usd_folder", "GO")])

    def test_unknown_format_is_refused(self):
        with self.assertRaises(ValueError):
            api.export(Usd.Stage.Open(str(CLEAN)), ["usdz"], self.tmp / "out")  # not written yet
        self.assertFalse((self.tmp / "out").exists())

    def test_validate_reports_instead_of_exiting(self):
        missing = validate_folder(self.tmp / "nope.usda")
        self.assertFalse(missing.ok)
        self.assertIn("was not written", missing.problems[0])
        (self.tmp / "bad.usda").write_text("not a usd layer")
        broken = validate_folder(self.tmp / "bad.usda")  # census() would sys.exit on this
        self.assertFalse(broken.ok)
        self.assertIsNone(broken.verdict)

    def test_preview_url_only_under_stages(self):
        self.assertEqual(api.preview_url(ROOT / "stages" / "hero" / "shot.usda"),
                         "http://127.0.0.1:5180/?stage=/stages/hero/shot.usda")
        self.assertIsNone(api.preview_url(self.tmp / "shot.usda"))


if __name__ == "__main__":
    unittest.main()
