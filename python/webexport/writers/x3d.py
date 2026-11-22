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


def _attr_val(attr, tc=None):
    """Read an attribute value, falling back to earliest time sample when no default exists."""
    if not attr or not attr.IsValid():
        return None
    if tc is not None:
        val = attr.Get(tc)
        if val is not None:
            return val
    val = attr.Get()
    if val is not None:
        return val
    if attr.GetNumTimeSamples() > 0:
        return attr.Get(Usd.TimeCode.EarliestTime())
    return None


def _is_visible(prim) -> bool:
    return UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode.Default()) != UsdGeom.Tokens.invisible


def _prototype_paths(stage: Usd.Stage) -> list[str]:
    paths: list[str] = []
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdGeom.PointInstancer):
            for target in UsdGeom.PointInstancer(prim).GetPrototypesRel().GetTargets():
                paths.append(str(target))
    return paths


def _appearance(material):
    """(base colour, metallic, roughness, texture path), read through whichever context answers."""
    base, metal, rough, texture = (0.8, 0.8, 0.8), 0.0, 0.5, None
    if material:
        surface = None
        for context in (UsdShade.Tokens.universalRenderContext, "mtlx"):
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
                value = _attr_val(inp.GetAttr()) if inp else None
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
                asset = _attr_val(file_input.GetAttr()) if file_input else None
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


def triangulate_corners(counts, indices, reverse: bool = False) -> tuple[list[int], list[int], list[int]]:
    """Fan-triangulate faces, returning (tri_points, tri_corners, tri_faces)."""
    tri_points, tri_corners, tri_faces = [], [], []
    cursor = 0
    for face_idx, count in enumerate(counts):
        face = indices[cursor:cursor + count]
        for i in range(1, count - 1):
            if reverse:
                tri_points += [face[0], face[i + 1], face[i]]
                tri_corners += [cursor, cursor + i + 1, cursor + i]
            else:
                tri_points += [face[0], face[i], face[i + 1]]
                tri_corners += [cursor, cursor + i, cursor + i + 1]
            tri_faces += [face_idx, face_idx, face_idx]
        cursor += count
    return tri_points, tri_corners, tri_faces


def _uvs(prim, tri_points, tri_corners, tri_faces, point_count):
    """One (u, v) per triangle corner, honoring USD primvar interpolation and indexing."""
    api = UsdGeom.PrimvarsAPI(prim)
    primvar = None
    for name in ("st", "uv", "uv0", "map1"):
        candidate = api.GetPrimvar(name)
        if candidate and candidate.HasValue():
            primvar = candidate
            break
    if primvar is None:
        return None
    values = _attr_val(primvar.GetAttr())
    if not values:
        return None
    indexed = primvar.IsIndexed()
    uv_indices = _attr_val(primvar.GetIndicesAttr()) if indexed else None
    interp = primvar.GetInterpolation()
    out = []
    for k in range(len(tri_points)):
        if interp == UsdGeom.Tokens.constant:
            elem_idx = 0
        elif interp == UsdGeom.Tokens.uniform:
            elem_idx = tri_faces[k]
        elif interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
            elem_idx = tri_points[k]
        elif interp == UsdGeom.Tokens.faceVarying:
            elem_idx = tri_corners[k]
        else:
            elem_idx = tri_points[k] if len(values) == point_count else tri_corners[k]
        if indexed and uv_indices is not None:
            val_idx = int(uv_indices[elem_idx]) if 0 <= elem_idx < len(uv_indices) else -1
        else:
            val_idx = int(elem_idx)
        if 0 <= val_idx < len(values):
            value = values[val_idx]
            out.append((float(value[0]), float(value[1])))
        else:
            out.append((0.0, 0.0))
    return out


def _world_points(prim, points):
    xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    return [xform.Transform(Gf.Vec3d(p)) for p in points]


def _trs_from_matrix(matrix: Gf.Matrix4d) -> dict:
    """Decompose a USD row-vector local-to-world matrix into translation, rotation quat, and scale."""
    translation = matrix.ExtractTranslation()
    rows = [Gf.Vec3d(matrix.GetRow(i)[0], matrix.GetRow(i)[1], matrix.GetRow(i)[2])
            for i in range(3)]
    scale = [r.GetLength() for r in rows]
    det = rows[0].GetDot(rows[1].GetCross(rows[2]))
    if det < 0.0:
        scale[0] = -scale[0]
    if all(abs(s) > 1e-12 for s in scale):
        rot_mat = Gf.Matrix4d(1.0)
        for i in range(3):
            unit = rows[i] / scale[i]
            rot_mat.SetRow(i, Gf.Vec4d(unit[0], unit[1], unit[2], 0.0))
        rot_mat.Orthonormalize(False)
        quat = rot_mat.ExtractRotationQuat().GetNormalized()
    else:
        quat = matrix.ExtractRotationQuat().GetNormalized()
    imag = quat.GetImaginary()
    rot = [float(imag[0]), float(imag[1]), float(imag[2]), float(quat.GetReal())]
    if not all(math.isfinite(v) for v in rot):
        rot = [0.0, 0.0, 0.0, 1.0]
    return {"translation": [float(translation[0]), float(translation[1]), float(translation[2])],
            "rotation": rot,
            "scale": [float(s) for s in scale]}


def _trs(prim, time: float) -> dict:
    """Translation, rotation (x, y, z, w) and scale from a prim's local-to-world at one time."""
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode(time))
    return _trs_from_matrix(matrix)


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


def _frames(stage) -> tuple[int, int, float, list[int]]:
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
        self._used_defs: set[str] = set()

    def _def(self, base: str) -> str:
        """Allocate a globally unique legal X3D DEF name for this document."""
        candidate = _name(base)
        if candidate not in self._used_defs:
            self._used_defs.add(candidate)
            return candidate
        n = 2
        while f"{candidate}_{n}" in self._used_defs:
            n += 1
        unique = f"{candidate}_{n}"
        self._used_defs.add(unique)
        return unique

    def entry_file(self, stage: Usd.Stage, out_dir: Path) -> Path:
        name = stage.GetDefaultPrim().GetName() if stage.GetDefaultPrim() else "stage"
        return Path(out_dir) / f"{name}.x3d"

    # -- prims -----------------------------------------------------------------------
    def _material(self, parent, material, label: str):
        base, metal, rough, texture = _appearance(material)
        appearance = ET.SubElement(parent, "Appearance")
        # Neutral white baseColor when a baseTexture is attached so USD's unconnected fallback
        # never darkens the texture.
        effective_base = (1.0, 1.0, 1.0) if texture else base
        material_node = ET.SubElement(appearance, "PhysicalMaterial", {
            "DEF": self._def("mat_" + label),
            "baseColor": _vec(effective_base, 3),
            "metallic": _num(min(1.0, max(0.0, metal))),
            "roughness": _num(min(1.0, max(0.0, rough)))})
        if texture:
            url = self._copy(texture)
            ET.SubElement(material_node, "ImageTexture", {
                "containerField": "baseTexture",
                "url": url})
        return material_node

    def _copy(self, path: Path) -> str:
        key = path.resolve()
        if key not in self.copied:
            used = {name.lower() for name in self.copied.values()}
            name = path.name
            n = 2
            while name.lower() in used:
                name = f"{path.stem}_{n}{path.suffix}"
                n += 1
            target = Path(self.out_dir) / name
            shutil.copy2(path, target)
            self.copied[key] = name
        return self.copied[key]

    def _shape(self, parent, prim, points, tri_points, tri_corners, tri_faces, label: str, bound):
        shape = ET.SubElement(parent, "Shape")
        self._material(shape, bound, label)
        triangles = [tri_points[i:i + 3] for i in range(0, len(tri_points), 3)]
        # -1 ends each face, not only the last one: a single trailing -1 would read as one polygon
        faces = " ".join(" ".join(str(v) for v in tri) + " -1" for tri in triangles)
        face_set = ET.SubElement(shape, "IndexedFaceSet", {
            "coordIndex": faces, "solid": "false", "creaseAngle": "0.5"})
        coordinate = ET.SubElement(face_set, "Coordinate", {
            "point": " ".join(_vec(p, 3) for p in points)})
        uv = _uvs(prim, tri_points, tri_corners, tri_faces, len(points))
        if uv:
            ET.SubElement(face_set, "TextureCoordinate", {
                "point": " ".join(_vec(p, 2) for p in uv)})
            # UVs are per corner, so texCoordIndex walks corners in order, with the same -1 ends
            cursor = 0
            tex_faces = []
            for tri in triangles:
                tex_faces.append(" ".join(str(c) for c in range(cursor, cursor + len(tri))) + " -1")
                cursor += len(tri)
            face_set.set("texCoordIndex", " ".join(tex_faces))
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
        self.copied = {}
        self._used_defs = set()
        out_dir.mkdir(parents=True, exist_ok=True)

        root = ET.Element("X3D", {"profile": "Immersive", "version": "4.0"})
        scene = ET.SubElement(root, "Scene")
        self.scene = scene  # TimeSensors, interpolators and ROUTEs live beside the nodes

        up_axis = UsdGeom.GetStageUpAxis(stage)
        mpu = float(UsdGeom.GetStageMetersPerUnit(stage)) if stage.HasAuthoredMetadata("metersPerUnit") else 1.0
        need_up = up_axis == UsdGeom.Tokens.z
        need_scale = abs(mpu - 1.0) > 1e-9
        if need_up or need_scale:
            root_attrs = {}
            if need_up:
                root_attrs["rotation"] = f"1 0 0 {-math.pi / 2:.6f}"
            if need_scale:
                root_attrs["scale"] = _vec((mpu, mpu, mpu), 3)
            container = ET.SubElement(scene, "Transform", root_attrs)
        else:
            container = scene

        self._viewpoint(container, stage, settings)
        self._lights(container, stage)

        proto_paths = _prototype_paths(stage)
        material_sets = [v for v in inv.variant_sets if v.kind == "material"]
        meshes = [p for p in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies())
                  if p.IsA(UsdGeom.Mesh)
                  and UsdGeom.Imageable(p).ComputePurpose() in ("default", "render")
                  and _is_visible(p)
                  and not any(_is_above(proto, str(p.GetPath())) for proto in proto_paths)]
        if material_sets:
            self._variant_switch(container, stage, meshes, material_sets)
        else:
            for prim in meshes:
                self._mesh(container, prim)

        self._instancers(container, stage)

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
        counts = _attr_val(mesh.GetFaceVertexCountsAttr())
        indices = _attr_val(mesh.GetFaceVertexIndicesAttr())
        points = _attr_val(mesh.GetPointsAttr())
        if not counts or not indices or not points:
            return
        reverse = _attr_val(mesh.GetOrientationAttr()) == UsdGeom.Tokens.leftHanded
        tri_points, tri_corners, tri_faces = triangulate_corners(counts, indices, reverse=reverse)
        bound = material or UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
        label = _name(prim.GetName()) + label_suffix
        if _animated(prim):
            self._moving_mesh(parent, prim, points, tri_points, tri_corners, tri_faces,
                              label, bound, label_suffix)
        else:
            self._shape(parent, prim, _world_points(prim, points),
                        tri_points, tri_corners, tri_faces, label, bound)

    def _moving_mesh(self, parent, prim, points, tri_points, tri_corners, tri_faces,
                     label, bound, label_suffix):
        """A moving prim: its object-space Shape under a DEF'd Transform, then the bake.

        The Transform starts at the stage start time so a viewer that has not run the animation
        yet, or has it paused at zero, sees the same pose as USD at the first frame. The
        geometry stays local because the interpolators move the Transform, not the points.
        """
        name = self._def("node_" + prim.GetPath().pathString.strip("/") + label_suffix)
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
        self._shape(transform, prim, points, tri_points, tri_corners, tri_faces, label, bound)
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
        sensor = self._def("time_" + node_name)
        ET.SubElement(self.scene, "TimeSensor", {
            "DEF": sensor, "cycleInterval": _num((end - start) / fps), "loop": "true"})
        if rotation_moves:
            values = " ".join(f"{_vec(axis, 3)} {_num(angle)}"
                              for axis, angle in _rotation_keys(samples))
            interpolator = self._def("rot_" + node_name)
            ET.SubElement(self.scene, "OrientationInterpolator", {
                "DEF": interpolator, "key": keys, "keyValue": values})
            self._route(sensor, "fraction_changed", interpolator, "set_fraction")
            self._route(interpolator, "value_changed", node_name, "set_rotation")
        if translation_moves:
            values = " ".join(_vec(sample["translation"], 3) for sample in samples)
            interpolator = self._def("pos_" + node_name)
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
            "DEF": self._def("looks"),
            "whichChoice": str(fact.options.index(selected)) if selected in fact.options else "0"})
        session = stage.GetSessionLayer()
        spec = session.GetPrimAtPath(owner.GetPath())
        had_session_sel = bool(spec and fact.set_name in spec.variantSelections)
        prev_session_sel = spec.variantSelections[fact.set_name] if had_session_sel else None
        with Usd.EditContext(stage, session):
            try:
                for option in fact.options:
                    group = ET.SubElement(switch, "Group", {"DEF": self._def("look_" + option)})
                    vset.SetVariantSelection(option)
                    for prim in meshes:
                        self._mesh(group, prim, label_suffix="_" + option)
            finally:
                if had_session_sel:
                    vset.SetVariantSelection(prev_session_sel)
                else:
                    vset.ClearVariantSelection()

    def _proto_meshes(self, proto):
        out = []
        for p in Usd.PrimRange(proto, Usd.TraverseInstanceProxies()):
            if not p.IsA(UsdGeom.Mesh):
                continue
            if UsdGeom.Imageable(p).ComputePurpose() not in ("default", "render"):
                continue
            hidden = False
            cur = p
            while cur and cur.IsValid():
                if cur != proto:
                    vis = cur.GetAttribute("visibility")
                    if vis and vis.HasAuthoredValue() and vis.Get() == UsdGeom.Tokens.invisible:
                        hidden = True
                        break
                if cur == proto:
                    break
                cur = cur.GetParent()
            if not hidden:
                out.append(p)
        return out

    def _instancers(self, scene, stage):
        for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
            if not prim.IsA(UsdGeom.PointInstancer) or not _is_visible(prim):
                continue
            instancer = UsdGeom.PointInstancer(prim)
            positions = _attr_val(instancer.GetPositionsAttr())
            prototypes = instancer.GetPrototypesRel().GetTargets()
            if not positions or not prototypes:
                continue
            proto_indices = _attr_val(instancer.GetProtoIndicesAttr())
            orientations = _attr_val(instancer.GetOrientationsAttr())
            scales = _attr_val(instancer.GetScalesAttr())
            invisible_ids = set(_attr_val(instancer.GetInvisibleIdsAttr()) or ())
            ids = _attr_val(instancer.GetIdsAttr())

            visible_instances = []
            used_proto_indices: set[int] = set()
            for index, p in enumerate(positions):
                inst_id = int(ids[index]) if ids is not None and index < len(ids) else index
                if inst_id in invisible_ids:
                    continue
                p_idx = int(proto_indices[index]) if proto_indices is not None and index < len(proto_indices) else 0
                if 0 <= p_idx < len(prototypes):
                    visible_instances.append((index, p_idx, p))
                    used_proto_indices.add(p_idx)
            if not visible_instances:
                continue

            holder = None
            proto_defs: dict[int, tuple[str, str]] = {}
            for p_idx in sorted(used_proto_indices):
                proto = stage.GetPrimAtPath(prototypes[p_idx])
                if not proto or not proto.IsValid():
                    continue
                p_meshes = self._proto_meshes(proto)
                mesh_entries = []
                proto_to_world = UsdGeom.Xformable(proto).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                world_to_proto = proto_to_world.GetInverse()
                for m_prim in p_meshes:
                    mesh = UsdGeom.Mesh(m_prim)
                    counts = _attr_val(mesh.GetFaceVertexCountsAttr())
                    indices = _attr_val(mesh.GetFaceVertexIndicesAttr())
                    points = _attr_val(mesh.GetPointsAttr())
                    if not counts or not indices or not points:
                        continue
                    reverse = _attr_val(mesh.GetOrientationAttr()) == UsdGeom.Tokens.leftHanded
                    tri_points, tri_corners, tri_faces = triangulate_corners(counts, indices, reverse=reverse)
                    if m_prim == proto:
                        rel_pts = [Gf.Vec3d(pt[0], pt[1], pt[2]) for pt in points]
                    else:
                        p_to_proto = (UsdGeom.Xformable(m_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                                      * world_to_proto)
                        rel_pts = [p_to_proto.Transform(Gf.Vec3d(pt[0], pt[1], pt[2])) for pt in points]
                    bound = UsdShade.MaterialBindingAPI(m_prim).ComputeBoundMaterial()[0]
                    mesh_entries.append((m_prim, rel_pts, tri_points, tri_corners, tri_faces, bound))
                if not mesh_entries:
                    continue
                if holder is None:
                    holder = ET.SubElement(scene, "Switch", {
                        "DEF": self._def("prototypes_" + prim.GetName()),
                        "whichChoice": "-1"})
                proto_def = self._def("proto_" + proto.GetName())
                if len(mesh_entries) == 1:
                    m_prim, rel_pts, tri_points, tri_corners, tri_faces, bound = mesh_entries[0]
                    shape = self._shape(holder, m_prim, rel_pts, tri_points, tri_corners,
                                        tri_faces, proto_def, bound)
                    shape.set("DEF", proto_def)
                    proto_defs[p_idx] = ("Shape", proto_def)
                else:
                    group = ET.SubElement(holder, "Group", {"DEF": proto_def})
                    for idx_m, (m_prim, rel_pts, tri_points, tri_corners, tri_faces, bound) in enumerate(mesh_entries):
                        self._shape(group, m_prim, rel_pts, tri_points, tri_corners,
                                    tri_faces, f"{proto_def}_{idx_m}", bound)
                    proto_defs[p_idx] = ("Group", proto_def)

            if not proto_defs:
                continue

            if _animated(prim):
                name = self._def("inst_" + prim.GetPath().pathString.strip("/"))
                start, end, fps, frames = _frames(stage)
                samples = [_trs(prim, frame) for frame in frames]
                inst_parent = ET.SubElement(scene, "Transform", {"DEF": name})
                at_start = samples[0]
                if any(abs(v) > 1e-9 for v in at_start["translation"]):
                    inst_parent.set("translation", _vec(at_start["translation"], 3))
                axis, angle = _axis_angle(at_start["rotation"], (0.0, 1.0, 0.0))
                if abs(angle) > 1e-9:
                    inst_parent.set("rotation", f"{_vec(axis, 3)} {_num(angle)}")
                if any(abs(v - 1.0) > 1e-9 for v in at_start["scale"]):
                    inst_parent.set("scale", _vec(at_start["scale"], 3))
                self._animate(name, start, end, fps, frames, samples)
            else:
                at_rest = _trs_from_matrix(
                    UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
                has_t = any(abs(v) > 1e-9 for v in at_rest["translation"])
                axis, angle = _axis_angle(at_rest["rotation"], (0.0, 1.0, 0.0))
                has_r = abs(angle) > 1e-9
                has_s = any(abs(v - 1.0) > 1e-9 for v in at_rest["scale"])
                if has_t or has_r or has_s:
                    inst_parent = ET.SubElement(scene, "Transform")
                    if has_t:
                        inst_parent.set("translation", _vec(at_rest["translation"], 3))
                    if has_r:
                        inst_parent.set("rotation", f"{_vec(axis, 3)} {_num(angle)}")
                    if has_s:
                        inst_parent.set("scale", _vec(at_rest["scale"], 3))
                else:
                    inst_parent = scene

            for index, p_idx, p in visible_instances:
                if p_idx not in proto_defs:
                    continue
                tag, proto_def = proto_defs[p_idx]
                attrs = {"translation": _vec(p, 3)}
                if orientations is not None and index < len(orientations):
                    q = orientations[index]
                    imag = q.GetImaginary()
                    qx, qy, qz, qw = float(imag[0]), float(imag[1]), float(imag[2]), float(q.GetReal())
                    if qw < 0.0:
                        qx, qy, qz, qw = -qx, -qy, -qz, -qw
                    o_axis, o_angle = _axis_angle((qx, qy, qz, qw), (0.0, 1.0, 0.0))
                    if abs(o_angle) > 1e-9:
                        attrs["rotation"] = f"{_vec(o_axis, 3)} {_num(o_angle)}"
                if scales is not None and index < len(scales):
                    sc = scales[index]
                    if any(abs(float(v) - 1.0) > 1e-9 for v in sc):
                        attrs["scale"] = _vec(sc, 3)
                transform = ET.SubElement(inst_parent, "Transform", attrs)
                ET.SubElement(transform, tag, {"USE": proto_def})

    def _viewpoint(self, scene, stage, settings=None):
        cameras = [p for p in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Camera)]
        if not cameras:
            return
        want = getattr(settings, "camera", None) if settings is not None else None
        prim = next((c for c in cameras if str(c.GetPath()) == want), cameras[0])
        camera = UsdGeom.Camera(prim)
        xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        trs = _trs_from_matrix(xform)
        qx, qy, qz, qw = trs["rotation"]
        if qw < 0.0:
            qx, qy, qz, qw = -qx, -qy, -qz, -qw
        axis, angle = _axis_angle((qx, qy, qz, qw), (0.0, 1.0, 0.0))
        focal = _attr_val(camera.GetFocalLengthAttr()) or 35.0
        aperture = _attr_val(camera.GetVerticalApertureAttr()) or 20.25
        fov = 2 * math.atan(float(aperture) / (2 * float(focal)))
        ET.SubElement(scene, "Viewpoint", {
            "DEF": self._def("view_" + prim.GetName()),
            "description": str(prim.GetPath()),
            "position": _vec(trs["translation"], 3),
            "orientation": f"{_vec(axis, 3)} {_num(angle)}",
            "fieldOfView": _num(fov)})

    def _lights(self, scene, stage):
        for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
            kind = prim.GetTypeName()
            if kind not in PUNCTUAL or not _is_visible(prim):
                continue
            lumens = _attr_val(prim.GetAttribute("inputs:intensity"))
            lumens = 1.0 if lumens is None else float(lumens)
            stops = _attr_val(prim.GetAttribute("inputs:exposure"))
            stops = 0.0 if stops is None else float(stops)
            c = _attr_val(prim.GetAttribute("inputs:color"))
            c = (1.0, 1.0, 1.0) if c is None else c
            common = {"color": _vec(c, 3), "intensity": _num(lumens * (2 ** stops)),
                      "global": "true"}
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
