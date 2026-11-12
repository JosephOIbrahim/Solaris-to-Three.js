"""glTF profile: what a stage loses on its way into a .glb.

Blueprint 4.2 and 4.6, mission M7. The answer is fixed by docs/decisions/0001-gltf-writer.md:
the writer is pure Python, so this profile must report exactly the places where pure Python
cannot reproduce the stage.

    blocks    a texture glTF can never carry (.rat, .tx, UDIM, .exr, .hdr, .tga), a missing image,
              a composition error
    changes   subdivision shipped as its cage, geometry variants reduced to the selected option,
              material variants not encoded yet, lights glTF has no punctual equivalent for,
              volumes dropped, MaterialX falling back to UsdPreviewSurface values
    note      what survives in another form: instancing, flattened references and payloads,
              PNG/JPEG images embedded in the file, the current time sample only

The profile never judges what the writer then does with textures or lights; it reports the
translation, and the writer's structural validation catches its own mistakes.
"""
from __future__ import annotations

from ..inventory import Inventory
from ..report import LossItem

EMBEDDABLE = {".png", ".jpg", ".jpeg"}
PUNCTUAL = {"SphereLight": "a point light", "DistantLight": "a directional light",
            "CylinderLight": "a point light"}
NO_EQUIVALENT = {"RectLight", "DiskLight", "DomeLight", "DomeLight_1", "GeometryLight", "MeshLight"}


def _item(severity, feature, paths, becomes, fix=None) -> LossItem:
    paths = [p for p in paths if p]
    return LossItem(severity, feature, len(paths), paths[:3], becomes, fix)


class GltfProfile:
    name = "gltf"
    extensions = (".glb",)

    def __init__(self, inv: Inventory):
        self.inv = inv

    # -- the pieces ------------------------------------------------------------------
    def _images(self) -> list[LossItem]:
        material_images = {t for m in self.inv.materials for t in m.textures}
        items = []
        bad = [t.path for t in self.inv.textures
               if t.path in material_images and t.ext not in EMBEDDABLE]
        if bad:
            items.append(_item("blocks", "image_format", bad, "not embeddable in a .glb",
                               "convert the image to PNG or JPEG"))
        missing = [t.path for t in self.inv.textures
                   if t.path in material_images and t.ext in EMBEDDABLE and not t.exists]
        if missing:
            items.append(_item("blocks", "missing_image", missing, "missing, so the file would not load",
                               "put the image where the stage points"))
        udim = [t.path for t in self.inv.textures if t.is_udim]
        if udim:
            items.append(_item("blocks", "udim_texture", udim, "not loaded: glTF has no UDIM",
                               "bake the tiles to one map"))
        if self.inv.composition.errors:
            items.append(_item("blocks", "composition_error", self.inv.composition.errors,
                               "part of the stage does not load", "fix or remove the broken arc"))
        good = [t.path for t in self.inv.textures if t.path in material_images and t.ext in EMBEDDABLE]
        if good:
            items.append(_item("note", "embedded_image", good, "embedded in the .glb, PNG or JPEG unchanged"))
        return items

    def _geometry(self) -> list[LossItem]:
        items = []
        subd = [m.path for m in self.inv.meshes if m.subdiv_scheme not in ("none", "")]
        if subd:
            items.append(_item("changes", "subdivision", subd, "the cage, not the limit surface",
                               "tessellate in Houdini before export if the smooth shape matters"))
        geo_variants = [f"{v.prim_path}:{v.set_name}" for v in self.inv.variant_sets
                        if v.kind in ("geometry", "mixed")]
        if geo_variants:
            items.append(_item("changes", "geometry_variant", geo_variants,
                               "the selected option only; the others are not in the file",
                               "export one file per look if the viewer must switch"))
        mat_variants = [f"{v.prim_path}:{v.set_name}" for v in self.inv.variant_sets if v.kind == "material"]
        if mat_variants:
            items.append(_item("note", "material_variant", mat_variants,
                               "KHR_materials_variants: every look is in the file and the player picks one"))
        if self.inv.volumes:
            items.append(_item("changes", "volume", self.inv.volumes, "dropped; glTF has no volume prim"))
        if self.inv.time_range and self.inv.time_range[0] != self.inv.time_range[1]:
            items.append(_item("note", "animation", [self.inv.root_layer],
                               "xform animation baked as glTF animations; other animated attributes are not"))
        return items

    def _lights(self) -> list[LossItem]:
        items = []
        kept = [l.path for l in self.inv.lights if l.type in PUNCTUAL]
        if kept:
            items.append(_item("note", "punctual_light", kept,
                               "a KHR_lights_punctual light; intensity converted to candela"))
        dropped = [f"{l.type} {l.path}" for l in self.inv.lights if l.type in NO_EQUIVALENT]
        if dropped:
            items.append(_item("changes", "light_dropped", dropped,
                               "dropped; glTF has no area or environment light",
                               "bake its effect into the stage, or accept an unlit export"))
        return items

    def _composition(self) -> list[LossItem]:
        items = []
        arcs = [p for p, _ in self.inv.composition.references] + \
               [p for p, _ in self.inv.composition.payloads]
        if arcs:
            items.append(_item("note", "composition_flattened", arcs,
                               "flattened into the file; the arcs do not survive"))
        if self.inv.instancers:
            items.append(_item("note", "instancing", [i.path for i in self.inv.instancers],
                               "EXT_mesh_gpu_instancing: one mesh, many transforms"))
        cameras = [c.path for c in self.inv.cameras if c.projection != "perspective"]
        if cameras:
            items.append(_item("changes", "orthographic_camera", cameras, "written as perspective"))
        materialx = [m.path for m in self.inv.materials if m.kind == "materialx"]
        if materialx:
            items.append(_item("changes", "materialx", materialx,
                               "the UsdPreviewSurface values, else a flat base colour",
                               "bind a UsdPreviewSurface for glTF, or accept the approximation"))
        return items

    # -- the contract ----------------------------------------------------------------
    def assess(self, inv: Inventory) -> list[LossItem]:
        self.inv = inv
        items = self._images() + self._geometry() + self._lights() + self._composition()
        return [i for i in items if i.count]
