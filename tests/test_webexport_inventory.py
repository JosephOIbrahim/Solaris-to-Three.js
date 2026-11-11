"""Tests for python/webexport/inventory.py. Needs pxr: `pip install usd-core`, or run under hython.

    python -m unittest tests.test_webexport_inventory -v
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

try:
    from pxr import Sdf, Usd, UsdGeom, UsdLux, UsdShade
except ImportError:  # no pxr on this machine
    Usd = None
else:  # only pxr may be missing: a broken inventory or census must fail here, never skip
    import web_census
    from webexport.inventory import Inventory, TextureFact, build_inventory, gather

CLEAN = ROOT / "fixtures" / "stage" / "shot.usda"
DIRTY = ROOT / "fixtures" / "stage-dirty" / "dirty.usda"
ESCAPE = ROOT / "fixtures" / "stage-escape" / "export" / "shot.usda"
NEEDS_PXR = "needs pxr (pip install usd-core, or run under hython)"


def inventory_of(path):
    """Build from another working directory: files must be found from their layer, not from here."""
    cwd = os.getcwd()
    os.chdir(Path.home())
    try:
        return build_inventory(Usd.Stage.Open(str(path)))
    finally:
        os.chdir(cwd)


@unittest.skipIf(Usd is None, NEEDS_PXR)
class FixtureStage(unittest.TestCase):
    """fixtures/stage/shot.usda: the facts mission M1 names, and the rest of the contract."""

    @classmethod
    def setUpClass(cls):
        cls.inv = inventory_of(CLEAN)

    def test_stage_metrics(self):
        self.assertIsInstance(self.inv, Inventory)
        self.assertEqual(Path(self.inv.root_layer).name, "shot.usda")
        self.assertEqual((self.inv.up_axis, self.inv.meters_per_unit), ("Y", 1.0))
        self.assertEqual(self.inv.time_range, (1.0, 120.0))

    def test_one_instancer_with_36_instances(self):
        self.assertEqual(len(self.inv.instancers), 1)
        pi = self.inv.instancers[0]
        self.assertEqual(pi.path, "/hero/geo/render/ring/pebbles")
        self.assertEqual(pi.count, 36)
        self.assertEqual(pi.prototype_paths, ["/hero/geo/render/ring/pebbles/prototypes/pebble"])

    def test_one_variant_set_look_with_three_material_options(self):
        self.assertEqual(len(self.inv.variant_sets), 1)
        v = self.inv.variant_sets[0]
        self.assertEqual((v.prim_path, v.set_name), ("/hero", "look"))
        self.assertEqual(sorted(v.options), ["chrome", "ember", "gold"])
        self.assertEqual(v.kind, "material")

    def test_two_purposes_render_and_proxy(self):
        self.assertEqual({m.purpose for m in self.inv.meshes} - {"default"}, {"render", "proxy"})

    def test_one_camera(self):
        self.assertEqual([c.path for c in self.inv.cameras], ["/cameras/render_cam"])
        cam = self.inv.cameras[0]
        self.assertEqual((cam.projection, cam.focal_length), ("perspective", 35.0))

    def test_three_lights(self):
        self.assertEqual(sorted(light.type for light in self.inv.lights), ["DomeLight_1", "RectLight", "SphereLight"])
        by_type = {light.type: light for light in self.inv.lights}
        self.assertEqual(by_type["DomeLight_1"].path, "/lights/dome")
        self.assertEqual(by_type["DomeLight_1"].texture, "./env/sky.hdr")
        key = by_type["RectLight"]
        self.assertEqual((key.intensity, key.exposure, key.texture), (20.0, 2.0, None))
        self.assertAlmostEqual(key.color[1], 0.86, places=5)

    def test_meshes(self):
        meshes = {m.path: m for m in self.inv.meshes}
        self.assertEqual(len(meshes), 9)
        subd = meshes["/hero/geo/render/orb_subd"]
        self.assertEqual((subd.subdiv_scheme, subd.has_uv, subd.points), ("catmullClark", True, 8))
        floor = meshes["/hero/geo/render/floor"]
        self.assertEqual((floor.subdiv_scheme, floor.purpose, floor.material_path), ("none", "render", "/hero/mtl/floor"))
        box = meshes["/hero/geo/proxy/box"]
        self.assertEqual((box.purpose, box.has_uv, box.material_path), ("proxy", False, "/hero/mtl/proxy"))

    def test_materials(self):
        mats = {m.path: m for m in self.inv.materials}
        self.assertEqual(len(mats), 7)
        self.assertEqual((mats["/hero/mtl/orb_std"].kind, mats["/hero/mtl/orb_std"].shader_id),
                         ("materialx", "ND_standard_surface_surfaceshader"))
        self.assertEqual((mats["/hero/mtl/orb_openpbr"].kind, mats["/hero/mtl/orb_openpbr"].shader_id),
                         ("materialx", "ND_open_pbr_surface_surfaceshader"))
        self.assertEqual((mats["/hero/mtl/pedestal"].kind, mats["/hero/mtl/pedestal"].shader_id),
                         ("preview", "UsdPreviewSurface"))

    def test_composition(self):
        comp = self.inv.composition
        self.assertEqual(comp.payloads, [("/hero", "./hero/asset.usda")])
        self.assertEqual((comp.references, comp.sublayers, comp.errors), ([], [], []))

    def test_textures_and_volumes(self):
        self.assertEqual(self.inv.textures, [TextureFact("./env/sky.hdr", ".hdr", True, False, False)])
        self.assertEqual(self.inv.volumes, [])


@unittest.skipIf(Usd is None, NEEDS_PXR)
class TextureFacts(unittest.TestCase):
    def test_dirty_stage(self):
        tex = {t.path: t for t in inventory_of(DIRTY).textures}
        self.assertEqual(set(tex), {"./tex/ok.png", "./tex/rust.rat", "D:/textures/wood.png",
                                    "./tex/skin.<UDIM>.exr", "./tex/sky.exr"})
        ok = tex["./tex/ok.png"]
        self.assertEqual((ok.ext, ok.exists, ok.is_udim, ok.is_absolute), (".png", True, False, False))
        rat = tex["./tex/rust.rat"]
        self.assertEqual((rat.ext, rat.exists, rat.is_udim, rat.is_absolute), (".rat", True, False, False))
        # Whether this machine has a D: drive holding it is not a fact about the stage, so no `exists` here.
        wood = tex["D:/textures/wood.png"]
        self.assertEqual((wood.is_udim, wood.is_absolute), (False, True))
        udim = tex["./tex/skin.<UDIM>.exr"]
        self.assertEqual((udim.ext, udim.exists, udim.is_udim, udim.is_absolute), (".exr", False, True, False))
        sky = tex["./tex/sky.exr"]
        self.assertEqual((sky.exists, sky.is_udim, sky.is_absolute), (False, False, False))

    def test_escape_stage(self):
        tex = {t.path: t for t in inventory_of(ESCAPE).textures}
        self.assertEqual(set(tex), {"./tex/ok.png", "../lib/wood.png", "../lib/skin.<UDIM>.png", "tex/missing.png"})
        self.assertFalse(any(t.is_absolute for t in tex.values()))
        self.assertEqual((tex["./tex/ok.png"].exists, tex["./tex/ok.png"].is_udim), (True, False))
        self.assertEqual((tex["../lib/wood.png"].exists, tex["../lib/wood.png"].is_udim), (True, False))
        skin = tex["../lib/skin.<UDIM>.png"]  # tile 1001 sits next to it, found from the layer
        self.assertEqual((skin.ext, skin.exists, skin.is_udim), (".png", True, True))
        self.assertEqual((tex["tex/missing.png"].exists, tex["tex/missing.png"].is_udim), (False, False))


@unittest.skipIf(Usd is None, NEEDS_PXR)
class DirtyStage(unittest.TestCase):
    def test_materials_lights_and_arcs(self):
        inv = inventory_of(DIRTY)
        mats = {m.path: m for m in inv.materials}
        self.assertEqual(mats["/scene/mtl/good"].textures, ["./tex/ok.png"])  # an unconnected node still counts
        self.assertEqual(mats["/scene/mtl/karma"].kind, "materialx")
        self.assertEqual(sorted(light.type for light in inv.lights), ["CylinderLight", "DomeLight"])
        self.assertEqual(inv.composition.references, [("/scene/missing", "./nowhere.usd")])
        self.assertEqual(len(inv.composition.errors), 1)
        self.assertIsNone(inv.time_range)

    def test_escape_stage_arcs(self):
        refs = inventory_of(ESCAPE).composition.references
        self.assertEqual(sorted(refs), [("/scene/asset", "../lib/asset.usda"), ("/scene/inside", "./parts/inside.usda")])


@unittest.skipIf(Usd is None, NEEDS_PXR)
class OneSourceOfFacts(unittest.TestCase):
    """tools/web_census.py reads the stage through gather(), so the census and the Inventory agree."""

    def test_census_and_inventory_agree(self):
        c = web_census.census(DIRTY)
        inv = build_inventory(c["stage"])
        grouped = {}
        for light in inv.lights:
            grouped.setdefault(light.type, []).append(light.path)
        self.assertEqual(grouped, c["lights"])
        self.assertEqual({t.path for t in inv.textures}, {row["path"] for row in c["textures"]})
        self.assertEqual(inv.composition.errors, c["errors"])
        self.assertEqual(c["no_uv"], ["/scene/geo/nouv"])
        self.assertEqual([m.path for m in inv.meshes if not m.has_uv], c["no_uv"])

    def test_tracked_types_are_the_callers_choice(self):
        stage = Usd.Stage.Open(str(CLEAN))
        self.assertEqual(gather(stage).tracked, {})
        tracked = gather(stage, track_types={"PointInstancer", "Camera"}).tracked
        self.assertEqual(tracked, {"PointInstancer": ["/hero/geo/render/ring/pebbles"],
                                   "Camera": ["/cameras/render_cam"]})


@unittest.skipIf(Usd is None, NEEDS_PXR)
class VariantKinds(unittest.TestCase):
    """The kind comes from what each variant authors, read from the layer, never from names."""

    def setUp(self):
        s = self.stage = Usd.Stage.CreateInMemory()
        self.asset = UsdGeom.Xform.Define(s, "/asset").GetPrim()
        self.geo = UsdGeom.Mesh.Define(s, "/asset/geo").GetPrim()
        UsdGeom.Scope.Define(s, "/asset/mtl")
        self.mats = {}
        for name in ("red", "blue"):
            mat = UsdShade.Material.Define(s, f"/asset/mtl/{name}")
            sh = UsdShade.Shader.Define(s, f"/asset/mtl/{name}/surface")
            sh.CreateIdAttr("UsdPreviewSurface")
            mat.CreateSurfaceOutput().ConnectToSource(sh.CreateOutput("surface", Sdf.ValueTypeNames.Token))
            self.mats[name] = mat
        self.key = UsdLux.SphereLight.Define(s, "/asset/key")

    def kind_of(self, set_name, variants):
        """variants: name -> a function that authors that variant. The first one ends up selected."""
        vset = self.asset.GetVariantSets().AddVariantSet(set_name)
        for name, author in variants.items():
            vset.AddVariant(name)
            vset.SetVariantSelection(name)
            with vset.GetVariantEditContext():
                author()
        vset.SetVariantSelection(next(iter(variants)))
        found = {v.set_name: v for v in build_inventory(self.stage).variant_sets}
        self.assertEqual(sorted(found[set_name].options), sorted(variants))
        return found[set_name].kind

    def roughness(self, name, value):
        shader = UsdShade.Shader(self.stage.GetPrimAtPath(f"/asset/mtl/{name}/surface"))
        return lambda: shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(value)

    def hide(self, path):
        return lambda: UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).MakeInvisible()

    def mesh(self, path):
        return lambda: UsdGeom.Mesh.Define(self.stage, path)

    def bind(self, name):
        def author():
            UsdShade.MaterialBindingAPI.Apply(self.geo)
            UsdShade.MaterialBindingAPI(self.geo).Bind(self.mats[name])
        return author

    def test_shader_inputs_only_is_material(self):
        self.assertEqual(self.kind_of("finish", {"rough": self.roughness("red", 0.9),
                                                 "glossy": self.roughness("red", 0.1)}), "material")

    def test_rebinding_meshes_is_material(self):
        self.assertEqual(self.kind_of("paint", {"red": self.bind("red"), "blue": self.bind("blue")}), "material")

    def test_meshes_only_is_geometry_even_when_unselected(self):
        kind = self.kind_of("model", {"lo": self.hide("/asset/geo"), "hi": self.mesh("/asset/geo_hi")})
        self.assertFalse(self.stage.GetPrimAtPath("/asset/geo_hi"))  # "hi" is not selected: its mesh is not on the stage
        self.assertEqual(kind, "geometry")

    def test_names_do_not_decide(self):
        self.assertEqual(self.kind_of("look", {"a": self.hide("/asset/geo"), "b": self.mesh("/asset/geo_b")}), "geometry")

    def test_material_and_geometry_is_mixed(self):
        self.assertEqual(self.kind_of("mix", {"a": self.roughness("blue", 0.5), "b": self.hide("/asset/geo")}), "mixed")

    def test_a_light_is_neither(self):
        self.assertEqual(self.kind_of("sun", {"on": lambda: self.key.CreateIntensityAttr(10.0),
                                              "off": lambda: self.key.CreateIntensityAttr(0.0)}), "mixed")


@unittest.skipIf(Usd is None, NEEDS_PXR)
class MaterialKinds(unittest.TestCase):
    def material(self, stage, path, shader_id, output):
        mat = UsdShade.Material.Define(stage, path)
        sh = UsdShade.Shader.Define(stage, f"{path}/surface")
        sh.CreateIdAttr(shader_id)
        out = sh.CreateOutput("out", Sdf.ValueTypeNames.Token)
        mat.CreateOutput(output, Sdf.ValueTypeNames.Token).ConnectToSource(out)

    def test_renderer_only_and_universal_materialx(self):
        s = Usd.Stage.CreateInMemory()
        self.material(s, "/mtl/karma", "kma_material_builder", "kma:surface")
        self.material(s, "/mtl/universal", "ND_standard_surface_surfaceshader", "surface")
        self.material(s, "/mtl/empty", "UsdPreviewSurface", "displacement")
        mats = {m.path: (m.kind, m.shader_id) for m in build_inventory(s).materials}
        self.assertEqual(mats["/mtl/karma"], ("other", "kma_material_builder"))
        self.assertEqual(mats["/mtl/universal"], ("materialx", "ND_standard_surface_surfaceshader"))
        self.assertEqual(mats["/mtl/empty"], ("other", None))


if __name__ == "__main__":
    unittest.main()
