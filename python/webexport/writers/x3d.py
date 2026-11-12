"""X3D writer: one .x3d file in X3D 4.0 XML, standard library only.

Blueprint 4.4 and 4.6, mission M8. Mapping: Transform/Shape/IndexedFaceSet for geometry,
PhysicalMaterial for shading, Switch for a material variant set (one group per look),
DEF/USE for a PointInstancer, Viewpoint for the camera, DirectionalLight and PointLight for
the lights X3D 4.0 can express.

Everything is written from the composed stage, so references and payloads are flattened. World
transforms are baked into coordinates, exactly as the glTF writer does, so no rotation maths goes
into the file. A texture is copied beside the .x3d and referenced by URL, which is how X3D loads
images. validate_x3d() re-reads the file and checks what the M8 test checks.
"""
from __future__ import annotations

import math
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from pxr import Gf, Usd, UsdGeom, UsdShade

from ..report import LossReport
from .base import ExportResult

FORMAT = "x3d"
IMAGE_TYPES = {".png", ".jpg", ".jpeg"}
PUNCTUAL = ("SphereLight", "DistantLight", "CylinderLight")


def _num(value: float) -> str:
    text = f"{float(value):.6g}"
    return "0" if text == "-0" else text


def _vec(values, count: int) -> str:
    return " ".join(_num(v) for v in list(values)[:count])


def _name(text: str) -> str:
    """A legal X3D DEF name: letters, digits and underscores, never starting with a digit."""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")
    return ("n_" + clean) if not clean or clean[0].isdigit() else clean


def _appearance(material):
    """(base colour, metallic, roughness, texture path), read through whichever context answers."""
    base, metal, rough, texture = (0.8, 0.8, 0.8), 0.0, 0.5, None
    if material:
        surface = None
        for context in ("universal", "mtlx"):
            try:
                source = material.ComputeSurfaceSource(context)[0]
            except Exception:
                source = None
            if source and source.GetPrim().IsValid():
                surface = source
                break
        if surface:
            for field in ("diffuseColor", "base_color", "metallic", "metalness",
                          "roughness", "specular_roughness"):
                inp = surface.GetInput(field)
                value = inp.Get() if inp else None
                if value is None:
                    continue
                if field in ("diffuseColor", "base_color"):
                    base = (float(value[0]), float(value[1]), float(value[2]))
                elif field in ("metallic", "metalness"):
                    metal = float(value)
                else:
                    rough = float(value)
            for field in ("diffuseColor", "base_color"):
                inp = surface.GetInput(field)
                source = inp.GetConnectedSource() if inp else None
                if not source:
                    continue
                shader = UsdShade.Shader(source[0].GetPrim())
                file_input = shader.GetInput("file")
                asset = file_input.Get() if file_input else None
                if asset and Path(asset.resolvedPath or asset.path).suffix.lower() in IMAGE_TYPES:
                    texture = Path(asset.resolvedPath or asset.path)
                    break
    return base, metal, rough, texture


def triangulate(counts, indices) -> list[int]:
    out, cursor = [], 0
    for count in counts:
        face = indices[cursor:cursor + count]
        cursor += count
        for i in range(1, count - 1):
            out += [face[0], face[i], face[i + 1]]
    return out


def _world_points(prim, points):
    xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    return [xform.Transform(Gf.Vec3d(p)) for p in points]


def _is_above(parent: str, child: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


class X3dWriter:
    name = FORMAT

    def __init__(self, report: LossReport, root: str | None = None):
        self.report = report
        self.root = root
        self.copied: dict[Path, str] = {}

    def entry_file(self, stage: Usd.Stage, out_dir: Path) -> Path:
        name = stage.GetDefaultPrim().GetName() if stage.GetDefaultPrim() else "stage"
        return Path(out_dir) / f"{name}.x3d"

    # -- prims -----------------------------------------------------------------------
    def _material(self, parent, material, label: str):
        base, metal, rough, texture = _appearance(material)
        appearance = ET.SubElement(parent, "Appearance")
        if texture:
            url = self._copy(texture)
            image = ET.SubElement(appearance, "ImageTexture", {"url": url})
            image.tail = "\n"
        material_node = ET.SubElement(appearance, "PhysicalMaterial", {
            "DEF": _name("mat_" + label),
            "baseColor": _vec(base, 3),
            "metallic": _num(min(1.0, max(0.0, metal))),
            "roughness": _num(min(1.0, max(0.0, rough)))})
        return material_node

    def _copy(self, path: Path) -> str:
        if path not in self.copied:
            target = Path(self.out_dir) / path.name
            shutil.copy2(path, target)
            self.copied[path] = path.name
        return path.name

    def _shape(self, parent, prim, points, indices, label: str, bound):
        shape = ET.SubElement(parent, "Shape")
        self._material(shape, bound, label)
        faces = " ".join(str(i) for i in indices) + " -1" if indices else ""
        face_set = ET.SubElement(shape, "IndexedFaceSet", {
            "coordIndex": faces, "solid": "false", "creaseAngle": "0.5"})
        coordinate = ET.SubElement(face_set, "Coordinate", {
            "point": " ".join(_vec(p, 3) for p in points)})
        coordinate.tail = "\n"
        return shape

    # -- the file --------------------------------------------------------------------
    def write(self, stage: Usd.Stage, inv, out_dir: Path, settings) -> ExportResult:
        if self.report is None:
            raise ValueError("the X3D writer needs its report first (principle 1)")
        if self.root is not None and self.root != stage.GetRootLayer().identifier:
            raise ValueError(f"this report was built for {self.root}, not {stage.GetRootLayer().identifier}")
        out_dir = Path(out_dir)
        if any(item.severity == "blocks" for item in self.report.items):
            return ExportResult(FORMAT, self.entry_file(stage, out_dir), [], 0, self.report, False)
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        root = ET.Element("X3D", {"profile": "Immersive", "version": "4.0"})
        scene = ET.SubElement(root, "Scene")
        self._viewpoint(scene, stage)
        self._lights(scene, stage)

        material_sets = [v for v in inv.variant_sets if v.kind == "material"]
        meshes = [p for p in stage.Traverse()
                  if p.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(p).ComputePurpose() in ("default", "render")]
        if material_sets:
            self._variant_switch(scene, stage, meshes, material_sets)
        else:
            for prim in meshes:
                self._mesh(scene, prim)

        self._instancers(scene, stage)

        entry = self.entry_file(stage, out_dir)
        ET.indent(root, space="  ")
        body = ET.tostring(root, encoding="unicode")
        entry.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n", encoding="utf-8")

        ok, problems = validate_x3d(entry)
        if not ok:
            raise RuntimeError("the .x3d failed its own validation: " + "; ".join(problems))
        files = [entry] + [out_dir / name for name in self.copied.values()]
        return ExportResult(FORMAT, entry, files, sum(f.stat().st_size for f in files),
                            self.report, ok)

    def _mesh(self, parent, prim, material=None, label_suffix=""):
        mesh = UsdGeom.Mesh(prim)
        counts, indices, points = (mesh.GetFaceVertexCountsAttr().Get(),
                                   mesh.GetFaceVertexIndicesAttr().Get(),
                                   mesh.GetPointsAttr().Get())
        if not counts or not indices or not points:
            return
        tris = triangulate(counts, indices)
        world = _world_points(prim, points)
        bound = material or UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
        self._shape(parent, prim, world, tris, _name(prim.GetName()) + label_suffix, bound)

    def _variant_switch(self, scene, stage, meshes, material_sets):
        """One Switch child per look; the selected look is shown. Cost: the geometry repeats."""
        fact = material_sets[0]
        owner = stage.GetPrimAtPath(fact.prim_path)
        vset = owner.GetVariantSets().GetVariantSet(fact.set_name)
        selected = vset.GetVariantSelection()
        switch = ET.SubElement(scene, "Switch", {
            "DEF": _name("looks"),
            "whichChoice": str(fact.options.index(selected)) if selected in fact.options else "0"})
        for option in fact.options:
            group = ET.SubElement(switch, "Group", {"DEF": _name("look_" + option)})
            vset.SetVariantSelection(option)
            try:
                for prim in meshes:
                    self._mesh(group, prim, label_suffix="_" + option)
            finally:
                vset.SetVariantSelection(selected)

    def _instancers(self, scene, stage):
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.PointInstancer):
                continue
            instancer = UsdGeom.PointInstancer(prim)
            positions = instancer.GetPositionsAttr().Get()
            prototypes = instancer.GetPrototypesRel().GetTargets()
            if not positions or not prototypes:
                continue
            proto = stage.GetPrimAtPath(prototypes[0])
            mesh = UsdGeom.Mesh(proto)
            counts, indices, points = (mesh.GetFaceVertexCountsAttr().Get(),
                                       mesh.GetFaceVertexIndicesAttr().Get(),
                                       mesh.GetPointsAttr().Get())
            if not counts or not indices or not points:
                continue
            proto_name = _name("proto_" + proto.GetName())
            # the prototype is DEF'd inside a Switch that shows nothing, then used per instance
            holder = ET.SubElement(scene, "Switch", {"DEF": _name("prototypes_" + prim.GetName()),
                                                     "whichChoice": "-1"})
            shape = self._shape(holder, proto, [Gf.Vec3d(p[0], p[1], p[2]) for p in points],
                                triangulate(counts, indices), proto_name,
                                UsdShade.MaterialBindingAPI(proto).ComputeBoundMaterial()[0])
            shape.set("DEF", proto_name)
            xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            for p in positions:
                world = xform.Transform(Gf.Vec3d(p))
                transform = ET.SubElement(scene, "Transform", {"translation": _vec(world, 3)})
                ET.SubElement(transform, "Shape", {"USE": proto_name})

    def _viewpoint(self, scene, stage):
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Camera):
                continue
            camera = UsdGeom.Camera(prim)
            xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            position = xform.ExtractTranslation()
            forward = xform.TransformDir(Gf.Vec3d(0, 0, -1)).GetNormalized()
            back = Gf.Vec3d(0, 0, -1)
            axis = back.GetCross(forward)
            dot = max(-1.0, min(1.0, back.GetDot(forward)))
            angle = math.acos(dot)
            axis = axis.GetNormalized() if axis.GetLength() > 1e-9 else Gf.Vec3d(0, 1, 0)
            focal = camera.GetFocalLengthAttr().Get() or 35.0
            aperture = camera.GetVerticalApertureAttr().Get() or 20.25
            fov = 2 * math.atan(aperture / (2 * focal))
            ET.SubElement(scene, "Viewpoint", {
                "DEF": _name("view_" + prim.GetName()),
                "description": str(prim.GetPath()),
                "position": _vec(position, 3),
                "orientation": f"{_vec(axis, 3)} {_num(angle)}",
                "fieldOfView": _num(fov)})
            return

    def _lights(self, scene, stage):
        for prim in stage.Traverse():
            kind = prim.GetTypeName()
            if kind not in PUNCTUAL:
                continue
            intensity = prim.GetAttribute("inputs:intensity")
            exposure = prim.GetAttribute("inputs:exposure")
            colour = prim.GetAttribute("inputs:color")
            lumens = intensity.Get() if intensity and intensity.HasAuthoredValue() else 1.0
            stops = exposure.Get() if exposure and exposure.HasAuthoredValue() else 0.0
            c = colour.Get() if colour and colour.HasAuthoredValue() else (1.0, 1.0, 1.0)
            common = {"color": _vec(c, 3), "intensity": _num(lumens * (2 ** stops)),
                      "global": "false"}
            xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            if kind == "DistantLight":
                direction = xform.TransformDir(Gf.Vec3d(0, 0, -1)).GetNormalized()
                ET.SubElement(scene, "DirectionalLight", dict(common, direction=_vec(direction, 3)))
            else:
                ET.SubElement(scene, "PointLight", dict(common,
                                                        location=_vec(xform.ExtractTranslation(), 3)))


def validate_x3d(path: Path) -> tuple[bool, list[str]]:
    """Structural checks on the file the writer produced; the test also validates the schema."""
    problems: list[str] = []
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as exc:
        return False, [f"not well-formed XML: {exc}"]
    if root.tag != "X3D":
        problems.append(f"root element is {root.tag}, expected X3D")
    if root.get("version") != "4.0":
        problems.append(f"version is {root.get('version')}, expected 4.0")
    if not root.get("profile"):
        problems.append("no profile on the X3D root")
    scenes = root.findall("Scene")
    if len(scenes) != 1:
        problems.append(f"{len(scenes)} Scene elements, expected 1")
        return False, problems
    scene = scenes[0]
    names = {el.get("DEF") for el in root.iter() if el.get("DEF")}
    for use in [el for el in root.iter() if el.get("USE")]:
        if use.get("USE") not in names:
            problems.append(f"USE {use.get('USE')} has no matching DEF")
    for face_set in root.iter("IndexedFaceSet"):
        points = face_set.find("Coordinate")
        if points is None:
            problems.append("IndexedFaceSet without a Coordinate")
            continue
        count = len(points.get("point", "").split()) // 3
        indices = [int(i) for i in face_set.get("coordIndex", "").split() if i != "-1"]
        if indices and max(indices) >= count:
            problems.append(f"coordIndex {max(indices)} is outside {count} points")
    if not list(scene):
        problems.append("the Scene is empty")
    return not problems, problems
