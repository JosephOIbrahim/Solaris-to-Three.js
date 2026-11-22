"""X3D profile: what a stage loses on its way into an X3D 4.0 file.

Blueprint 4.2 and 4.6, mission M8. X3D is the most forgiving of the three formats: geometry
variants become a Switch, instancing becomes DEF/USE, and a material variant set is duplicated
into the Switch (which costs file size, not correctness).

    blocks    an image X3D cannot fetch or that glTF-style decoders cannot read (.rat, .tx,
              UDIM, .exr, .hdr, .tga), a missing image, a composition error
    changes   subdivision shipped as its cage, a material variant set duplicated once per look,
              volumes dropped, area and dome lights dropped, MaterialX falling back to
              UsdPreviewSurface values, a scale animation X3D has no interpolator for, an
              animated attribute the writers do not bake
    note      geometry variants kept as a Switch, instancing as DEF/USE, images copied beside
              the file, references and payloads flattened, rotation and translation baked as a
              TimeSensor and interpolators
"""
from __future__ import annotations

from ..inventory import Inventory, animated_attributes, scale_animated, xform_animated
from ..report import LossItem

FETCHABLE = {".png", ".jpg", ".jpeg"}
PUNCTUAL = {"SphereLight": "a point light", "DistantLight": "a directional light",
            "CylinderLight": "a point light"}
NO_EQUIVALENT = {"RectLight", "DiskLight", "DomeLight", "DomeLight_1", "GeometryLight", "MeshLight"}


def _item(severity, feature, paths, becomes, fix=None) -> LossItem:
    paths = [p for p in paths if p]
    return LossItem(severity, feature, len(paths), paths[:3], becomes, fix)


class X3dProfile:
    name = "x3d"
    extensions = (".x3d",)

    def __init__(self, inv: Inventory | None = None, stage=None):
        self.inv = inv
        self.stage = stage  # the live stage, for the animation checks; None means skip them

    def _images(self) -> list[LossItem]:
        material_images = {t for m in self.inv.materials for t in m.textures}
        items = []
        bad = [t.path for t in self.inv.textures
               if t.path in material_images and not t.is_udim and t.ext not in FETCHABLE]
        if bad:
            items.append(_item("blocks", "image_format", bad, "not usable as an ImageTexture",
                               "convert the image to PNG or JPEG"))
        missing = [t.path for t in self.inv.textures
                   if t.path in material_images and not t.is_udim and t.ext in FETCHABLE and not t.exists]
        if missing:
            items.append(_item("blocks", "missing_image", missing, "missing, so the texture would not load",
                               "put the image where the stage points"))
        udim = [t.path for t in self.inv.textures if t.is_udim]
        if udim:
            items.append(_item("blocks", "udim_texture", udim, "not loaded: X3D has no UDIM",
                               "bake the tiles to one map"))
        if self.inv.composition.errors:
            items.append(_item("blocks", "composition_error", self.inv.composition.errors,
                               "part of the stage does not load", "fix or remove the broken arc"))
        good = [t.path for t in self.inv.textures
                if t.path in material_images and not t.is_udim and t.exists and t.ext in FETCHABLE]
        if good:
            items.append(_item("note", "copied_image", good, "copied next to the .x3d and referenced by URL"))
        return items

    def _geometry(self) -> list[LossItem]:
        items = []
        subd = [m.path for m in self.inv.meshes if m.subdiv_scheme not in ("none", "")]
        if subd:
            items.append(_item("changes", "subdivision", subd, "the cage, not the limit surface",
                               "tessellate in Houdini before export if the smooth shape matters"))
        geo = [f"{v.prim_path}:{v.set_name}" for v in self.inv.variant_sets if v.kind in ("geometry", "mixed")]
        if geo:
            items.append(_item("changes", "geometry_variant", geo,
                               "the selected option only; geometry variants are not written as a Switch",
                               "export one file per option if the viewer must switch"))
        mat = [f"{v.prim_path}:{v.set_name}" for v in self.inv.variant_sets if v.kind == "material"]
        if mat:
            items.append(_item("changes", "material_variant", mat,
                               "duplicated into the Switch: one copy of the geometry per look",
                               "export one file per look if size matters"))
        if self.inv.volumes:
            items.append(_item("changes", "volume", self.inv.volumes, "dropped; X3D has no volume prim"))
        if self.stage is not None:
            scaling = scale_animated(self.stage)
            if scaling:
                items.append(_item("changes", "animation", scaling,
                                   "rotation and translation baked; scale change not representable "
                                   "in X3D",
                                   "bake the scale into the mesh points, or accept the start pose"))
            scaling_set = set(scaling)
            moved = [p for p in xform_animated(self.stage) if p not in scaling_set]
            if moved:
                n = len(moved)
                items.append(_item("note", "animation", moved,
                                   f"xform animation on {n} prim{'s' if n != 1 else ''} baked as "
                                   "TimeSensor and interpolators; other animated attributes are not"))
            if (scaling or moved) and self.inv.time_range and (self.inv.time_range[1] - self.inv.time_range[0] + 1) > 600:
                span = int(self.inv.time_range[1] - self.inv.time_range[0] + 1)
                items.append(_item("note", "animation_decimated", scaling or moved,
                                   f"range of {span} frames decimated to at most 600 samples"))
        moving = animated_attributes(self.stage) if self.stage is not None else []
        if moving:
            items.append(_item("changes", "animated_attribute", moving,
                               "the current time sample only; only transforms are baked"))
        return items

    def _lights(self) -> list[LossItem]:
        items = []
        kept = [l.path for l in self.inv.lights if l.type in PUNCTUAL]
        if kept:
            items.append(_item("note", "punctual_light", kept, "a PointLight or DirectionalLight"))
        dropped = [f"{l.type} {l.path}" for l in self.inv.lights if l.type not in PUNCTUAL]
        if dropped:
            items.append(_item("changes", "light_dropped", dropped,
                               "dropped; X3D 4.0 has no area or environment light",
                               "bake its effect into the stage, or accept an unlit export"))
        return items

    def _composition(self) -> list[LossItem]:
        items = []
        arcs = [p for p, _ in self.inv.composition.references] + [p for p, _ in self.inv.composition.payloads]
        if arcs:
            items.append(_item("note", "composition_flattened", arcs, "flattened into the file"))
        if self.inv.instancers:
            items.append(_item("note", "instancing", [i.path for i in self.inv.instancers],
                               "DEF/USE: one prototype, one Transform per instance"))
        cameras = [c.path for c in self.inv.cameras if c.projection != "perspective"]
        if cameras:
            items.append(_item("changes", "orthographic_camera", cameras, "written as a perspective Viewpoint"))
        materialx = [m.path for m in self.inv.materials if m.kind == "materialx"]
        if materialx:
            items.append(_item("changes", "materialx", materialx,
                               "the UsdPreviewSurface values, else a flat base colour",
                               "bind a UsdPreviewSurface for X3D, or accept the approximation"))
        other = [m.path for m in self.inv.materials if m.kind == "other"]
        if other:
            items.append(_item("changes", "shader_not_read", other,
                               "renderer-specific or unbound shader falls back to default colour",
                               "bind a UsdPreviewSurface for X3D"))
        parts = []
        if self.inv.up_axis and self.inv.up_axis.upper() != "Y":
            parts.append(f"{self.inv.up_axis}-up rotated to Y-up")
        if self.inv.meters_per_unit and abs(self.inv.meters_per_unit - 1.0) > 1e-6:
            parts.append(f"{self.inv.meters_per_unit:g} m/unit scaled to metres")
        if parts:
            items.append(LossItem("note", "stage_units", 1, [self.inv.root_layer], "; ".join(parts)))
        return items

    def assess(self, inv: Inventory) -> list[LossItem]:
        self.inv = inv
        items = self._images() + self._geometry() + self._lights() + self._composition()
        return [i for i in items if i.count]
