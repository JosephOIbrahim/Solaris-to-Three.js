"""X3D writer: one .x3d file in X3D 4.0 XML, standard library only.

Blueprint 4.4 and 4.6, mission M8. Mapping: Transform/Shape/IndexedFaceSet for geometry,
PhysicalMaterial for shading, Switch for a material variant set (one group per look),
DEF/USE for a PointInstancer, Viewpoint for the camera, DirectionalLight and PointLight for
the lights X3D 4.0 can express.

Everything is written from the composed stage, so references and payloads are flattened. Static
world transforms are baked into coordinates, exactly as the glTF writer does. A prim whose
local-to-world changes over the stage's time range cannot have that baked: it becomes its own
local Shape under a DEF'd Transform, with a TimeSensor, an OrientationInterpolator or a
PositionInterpolator per moving channel, and ROUTE statements wiring them to the Transform. The
static translation, rotation and scale come from the stage start time. A texture is copied beside
the .x3d and referenced by URL, which is how X3D loads images. validate_x3d() re-reads the file
and checks what the M8 test checks, including ROUTE targets and interpolator key counts.
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


def _stage_time(prim) -> float:
    """The time a static node is written at: the stage start, else 1."""
    stage = prim.GetStage()
    return float(stage.GetStartTimeCode() if stage.GetStartTimeCode() else 1.0)


def _trs(prim, time: float) -> dict:
    """Translation, rotation (x, y, z, w) and scale from a prim's local-to-world at one time."""
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode(time))
    translation = matrix.ExtractTranslation()
    scale = [Gf.Vec3d(matrix.GetColumn(i)[0], matrix.GetColumn(i)[1],
                      matrix.GetColumn(i)[2]).GetLength() for i in range(3)]
    quat = matrix.ExtractRotationQuat()
    imag = quat.GetImaginary()
    return {"translation": [float(translation[0]), float(translation[1]), float(translation[2])],
            "rotation": [float(imag[0]), float(imag[1]), float(imag[2]), float(quat.GetReal())],
            "scale": [float(s) if s else 1.0 for s in scale]}


def _same(a: dict, b: dict, eps: float = 1e-6) -> bool:
    return all(abs(x - y) <= eps for key in a for x, y in zip(a[key], b[key]))


def _animated(prim) -> bool:
    """True when the prim's local-to-world differs across the stage's time range.

    The same three-probe test as the glTF writer: start, middle and end. Sampling every frame
    would be more thorough but this decides whether to pay for a bake at all, and it is the
    contract the glTF output already keeps.
    """
    stage = prim.GetStage()
    start, end = stage.GetStartTimeCode(), stage.GetEndTimeCode()
    if start is None or end is None or end <= start:
        return False
    probes = [_trs(prim, start), _trs(prim, (start + end) / 2.0), _trs(prim, end)]
    return not (_same(probes[0], probes[1]) and _same(probes[1], probes[2]))


def _axis_angle(rotation, fallback_axis):
    """A quaternion (x, y, z, w) as X3D axis + angle, keeping the angle continuous.

    w may be negative when the caller has unwrapped the quaternion path past 180 degrees. atan2
    then returns an angle up to 2*pi, which is what a full turn needs: collapsing it to the
    shortest arc would spin the object backwards. A rotation with no imaginary part (identity,
    or the -1 quaternion that is a 360 degree turn) has no axis of its own, so the previous axis
    is reused.
    """
    x, y, z, w = rotation
    length = math.sqrt(x * x + y * y + z * z)
    angle = 2.0 * math.atan2(length, w)
    if length > 1e-12:
        return (x / length, y / length, z / length), angle
    return fallback_axis, angle


def _rotation_keys(samples) -> list[tuple[tuple[float, float, float], float]]:
    """One axis + angle per sample, with the quaternion signs made continuous first.

    Matrix.ExtractRotationQuat() may return q or -q for the same orientation. Comparing each
    quaternion with the previous one and flipping when the dot product is negative keeps the
    baked path short, so a sampled spin never jumps a full turn between two keys.
    """
    rotations, previous = [], None
    for sample in samples:
        x, y, z, w = sample["rotation"]
        if previous is not None and (x * previous[0] + y * previous[1]
                                     + z * previous[2] + w * previous[3]) < 0.0:
            x, y, z, w = -x, -y, -z, -w
        previous = (x, y, z, w)
        rotations.append(previous)
    fallback = (0.0, 1.0, 0.0)
    for x, y, z, _ in rotations:
        length = math.sqrt(x * x + y * y + z * z)
        if length > 1e-12:
            fallback = (x / length, y / length, z / length)
            break
    keys, axis = [], fallback
    for rotation in rotations:
        axis, angle = _axis_angle(rotation, axis)
        keys.append((axis, angle))
    return keys


def _frames(stage) -> tuple[float, float, float, list[int]]:
    """(start, end, fps, frames) sampled across the stage's range.

    A very long range is decimated so the file stays bounded; the last frame is always kept.
    """
    start, end = int(stage.GetStartTimeCode()), int(stage.GetEndTimeCode())
    fps = stage.GetTimeCodesPerSecond() or stage.GetFramesPerSecond() or 24.0
    step = max(1, round((end - start + 1) / 600))
    frames = list(range(start, end + 1, step))
    if frames[-1] != end:
        frames.append(end)
    return start, end, float(fps), frames


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
        self.scene = scene  # TimeSensors, interpolators and ROUTEs live beside the nodes
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
        bound = material or UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
        label = _name(prim.GetName()) + label_suffix
        if _animated(prim):
            self._moving_mesh(parent, prim, points, tris, label, bound, label_suffix)
        else:
            self._shape(parent, prim, _world_points(prim, points), tris, label, bound)

    def _moving_mesh(self, parent, prim, points, tris, label, bound, label_suffix):
        """A moving prim: its object-space Shape under a DEF'd Transform, then the bake.

        The Transform starts at the stage start time so a viewer that has not run the animation
        yet, or has it paused at zero, sees the same pose as USD at the first frame. The
        geometry stays local because the interpolators move the Transform, not the points.
        """
        name = _name("node_" + prim.GetPath().pathString.strip("/") + label_suffix)
        start, end, fps, frames = _frames(prim.GetStage())
        samples = [_trs(prim, frame) for frame in frames]
        transform = ET.SubElement(parent, "Transform", {"DEF": name})
        at_start = samples[0]
        if any(abs(v) > 1e-9 for v in at_start["translation"]):
            transform.set("translation", _vec(at_start["translation"], 3))
        axis, angle = _axis_angle(at_start["rotation"], (0.0, 1.0, 0.0))
        if abs(angle) > 1e-9:
            transform.set("rotation", f"{_vec(axis, 3)} {_num(angle)}")
        if any(abs(v - 1.0) > 1e-9 for v in at_start["scale"]):
            transform.set("scale", _vec(at_start["scale"], 3))
        self._shape(transform, prim, points, tris, label, bound)
        self._animate(name, start, end, fps, frames, samples)

    def _animate(self, node_name, start, end, fps, frames, samples):
        """TimeSensor plus one interpolator per channel that actually changes.

        Key times are fractions of the stage range, as X3D interpolators expect. X3D has no
        scale interpolator, so a scale-only animation is left at its start pose; every channel
        that never changes is skipped rather than written as a constant.
        """
        translation_moves = not all(
            _same({"translation": samples[0]["translation"]},
                  {"translation": sample["translation"]}) for sample in samples)
        rotation_moves = not all(
            _same({"rotation": samples[0]["rotation"]},
                  {"rotation": sample["rotation"]}) for sample in samples)
        if not (translation_moves or rotation_moves):
            return
        keys = " ".join(_num((frame - start) / float(end - start)) for frame in frames)
        sensor = _name("time_" + node_name)
        ET.SubElement(self.scene, "TimeSensor", {
            "DEF": sensor, "cycleInterval": _num((end - start) / fps), "loop": "true"})
        if rotation_moves:
            values = " ".join(f"{_vec(axis, 3)} {_num(angle)}"
                              for axis, angle in _rotation_keys(samples))
            interpolator = _name("rot_" + node_name)
            ET.SubElement(self.scene, "OrientationInterpolator", {
                "DEF": interpolator, "key": keys, "keyValue": values})
            self._route(sensor, "fraction_changed", interpolator, "set_fraction")
            self._route(interpolator, "value_changed", node_name, "set_rotation")
        if translation_moves:
            values = " ".join(_vec(sample["translation"], 3) for sample in samples)
            interpolator = _name("pos_" + node_name)
            ET.SubElement(self.scene, "PositionInterpolator", {
                "DEF": interpolator, "key": keys, "keyValue": values})
            self._route(sensor, "fraction_changed", interpolator, "set_fraction")
            self._route(interpolator, "value_changed", node_name, "set_translation")

    def _route(self, source: str, source_field: str, target: str, target_field: str) -> None:
        ET.SubElement(self.scene, "ROUTE", {"fromNode": source, "fromField": source_field,
                                            "toNode": target, "toField": target_field})

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
            # an instancer that moves needs an animated Transform per instance; a static one
            # keeps its single baked translation, as before.
            moving = _animated(prim)
            if moving:
                start, end, fps, frames = _frames(stage)
                matrices = [UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode(frame))
                            for frame in frames]
                keys = " ".join(_num((frame - start) / float(end - start)) for frame in frames)
                sensor = None
            else:
                xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            for index, p in enumerate(positions):
                point = Gf.Vec3d(p)
                if not moving:
                    transform = ET.SubElement(scene, "Transform",
                                              {"translation": _vec(xform.Transform(point), 3)})
                else:
                    over_time = [matrix.Transform(point) for matrix in matrices]
                    moves = any((value - over_time[0]).GetLength() > 1e-9
                                for value in over_time[1:])
                    name = _name("inst_" + prim.GetPath().pathString.strip("/") + "_" + str(index))
                    attributes = {"translation": _vec(over_time[0], 3)}
                    if moves:
                        attributes["DEF"] = name
                    transform = ET.SubElement(scene, "Transform", attributes)
                    if moves:
                        if sensor is None:
                            sensor = _name("time_inst_" + prim.GetPath().pathString.strip("/"))
                            ET.SubElement(scene, "TimeSensor", {
                                "DEF": sensor, "cycleInterval": _num((end - start) / fps),
                                "loop": "true"})
                        interpolator = _name("pos_" + name)
                        ET.SubElement(scene, "PositionInterpolator", {
                            "DEF": interpolator, "key": keys,
                            "keyValue": " ".join(_vec(value, 3) for value in over_time)})
                        self._route(sensor, "fraction_changed", interpolator, "set_fraction")
                        self._route(interpolator, "value_changed", name, "set_translation")
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
    for route in root.iter("ROUTE"):
        for end in ("fromNode", "toNode"):
            target = route.get(end)
            if not target:
                problems.append(f"ROUTE has no {end}")
            elif target not in names:
                problems.append(f"ROUTE {end} {target} has no matching DEF")
        for field in ("fromField", "toField"):
            if not route.get(field):
                problems.append(f"ROUTE has no {field}")
    strides = {"OrientationInterpolator": 4, "PositionInterpolator": 3}
    for tag, stride in strides.items():
        for interpolator in root.iter(tag):
            key = interpolator.get("key", "").split()
            value = interpolator.get("keyValue", "").split()
            if not key:
                problems.append(f"{tag} has no key")
            if len(value) != len(key) * stride:
                problems.append(f"{tag} has {len(key)} keys and {len(value)} values, "
                                f"expected {len(key) * stride}")
    for sensor in root.iter("TimeSensor"):
        try:
            if float(sensor.get("cycleInterval", "0")) <= 0:
                problems.append("TimeSensor cycleInterval must be positive")
        except ValueError:
            problems.append(f"TimeSensor cycleInterval {sensor.get('cycleInterval')!r} is not a number")
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
