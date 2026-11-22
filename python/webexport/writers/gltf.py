"""glTF writer: one .glb with a JSON chunk and a BIN chunk, packed with stdlib array/struct.

Blueprint 4.4, mission M7, and the route chosen in docs/decisions/0001-gltf-writer.md: pure
Python from pxr, no glTF library, no numpy.

What it writes: meshes (quads/ngons triangulated, purpose default and render, authored normals and
UVs, leftHanded winding correction), stage upAxis/metersPerUnit normalization, UsdPreviewSurface
and MaterialX to pbrMetallicRoughness, base colour textures embedded as PNG or JPEG, all cameras,
KHR_lights_punctual for sphere/distant/cylinder lights, EXT_mesh_gpu_instancing for PointInstancers
(multi-prototype with orientations/scales), KHR_materials_variants (session-layer safe), and baked
node transform animations. The seven /WebSettings values travel in `extras`.

validate_glb() re-opens what was written and checks the container structurally; the M7 unit test
and ExportResult.validated both use it.
"""
from __future__ import annotations

import array
import json
import math
import struct
import sys
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

from ..report import LossReport
from .base import ExportResult

FORMAT = "gltf"
COMPONENT = {"float": 5126, "uint": 5125}
COMPONENT_WIDTH = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
INDEX_STRUCT_FMT = {5121: "B", 5123: "H", 5125: "I"}
ARITY = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_OPTIONAL_TOP_ARRAYS = (
    "extensionsUsed", "extensionsRequired", "nodes", "meshes", "materials",
    "cameras", "images", "textures", "samplers", "accessors", "bufferViews",
    "animations",
)


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _attr_val(attr, tc: Usd.TimeCode):
    """Read an attribute at timecode tc, falling back to Default() then EarliestTime()."""
    if not attr or not attr.IsValid():
        return None
    val = attr.Get(tc)
    if val is None and tc != Usd.TimeCode.Default():
        val = attr.Get(Usd.TimeCode.Default())
    if val is None and attr.GetNumTimeSamples() > 0:
        val = attr.Get(Usd.TimeCode.EarliestTime())
    return val


class _Glb:
    """Builds the two chunks. Nothing here knows about USD."""

    def __init__(self):
        self.bin = bytearray()
        self.doc = {
            "asset": {"version": "2.0", "generator": "webexport"},
            "extensionsUsed": [], "scene": 0, "scenes": [{"nodes": [], "extras": {}}],
            "nodes": [], "meshes": [], "materials": [], "cameras": [], "images": [],
            "textures": [], "samplers": [], "accessors": [], "bufferViews": [], "buffers": [],
            "animations": [],
        }
        self._materials: dict[str, int] = {}
        self._images: dict[str, int] = {}
        self._default_sampler: int | None = None
        self.variants: list[str] = []  # KHR_materials_variants names, in the order they are met

    def view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        offset = len(self.bin)
        self.bin += data
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.doc["bufferViews"].append(view)
        return len(self.doc["bufferViews"]) - 1

    def accessor(self, values, kind: str, gtype: str, minmax: bool = False,
                 target: int | None = 34962) -> int:
        fmt = {"float": "f", "uint": "I"}[kind]
        arr = array.array(fmt)
        if gtype == "SCALAR":
            arr.extend(values)
        else:
            for v in values:
                arr.extend(v)
        if sys.byteorder != "little":
            arr.byteswap()
        data = arr.tobytes()
        accessor = {"bufferView": self.view(data, target), "componentType": COMPONENT[kind],
                    "count": len(values), "type": gtype}
        if minmax and values:
            cast = float if kind == "float" else int
            if gtype == "SCALAR":  # animation times: plain floats, min/max are required by the spec
                accessor["min"], accessor["max"] = [cast(min(values))], [cast(max(values))]
            else:
                arity = ARITY[gtype]
                accessor["min"] = [cast(min(v[i] for v in values)) for i in range(arity)]
                accessor["max"] = [cast(max(v[i] for v in values)) for i in range(arity)]
        self.doc["accessors"].append(accessor)
        return len(self.doc["accessors"]) - 1

    def node(self, **fields) -> int:
        self.doc["nodes"].append(fields)
        self.doc["scenes"][0]["nodes"].append(len(self.doc["nodes"]) - 1)
        return len(self.doc["nodes"]) - 1

    def image(self, path: Path) -> int | None:
        mime = IMAGE_TYPES.get(path.suffix.lower())
        if not mime or not path.exists():
            return None
        key = str(path.resolve())
        if key in self._images:
            return self._images[key]
        if self._default_sampler is None:
            self.doc["samplers"].append({"magFilter": 9729, "minFilter": 9987,
                                         "wrapS": 10497, "wrapT": 10497})
            self._default_sampler = len(self.doc["samplers"]) - 1
        view = self.view(path.read_bytes(), target=None)
        self.doc["images"].append({"mimeType": mime, "bufferView": view, "name": path.name})
        self.doc["textures"].append({"sampler": self._default_sampler,
                                     "source": len(self.doc["images"]) - 1})
        index = len(self.doc["textures"]) - 1
        self._images[key] = index
        return index

    def material(self, material: UsdShade.Material | None, label: str, tag: str = "",
                 look=None, double_sided: bool = False) -> int:
        key = ((str(material.GetPath()) if material else f"default:{label}")
               + (f"|{tag}" if tag else "")
               + ("|ds" if double_sided else ""))
        if key in self._materials:
            return self._materials[key]
        if look is None:
            look = _appearance(material)
        base, metal, rough, opacity, opacity_threshold, emissive, texture, _uv_name = look
        tex_index = self.image(texture) if texture else None
        if tex_index is not None:
            # Connected diffuseColor overrides fallback color in USD; do not tint texture by fallback.
            base_factor = [1.0, 1.0, 1.0, opacity]
        else:
            base_factor = [base[0], base[1], base[2], opacity]
        pbr: dict = {"baseColorFactor": base_factor,
                     "metallicFactor": metal, "roughnessFactor": rough}
        if tex_index is not None:
            pbr["baseColorTexture"] = {"index": tex_index}
        entry: dict = {"name": label, "pbrMetallicRoughness": pbr}
        if opacity_threshold > 0.0:
            entry["alphaMode"] = "MASK"
            entry["alphaCutoff"] = opacity_threshold
        elif opacity < 1.0 - 1e-6:
            entry["alphaMode"] = "BLEND"
        if any(c > 1e-6 for c in emissive):
            entry["emissiveFactor"] = [emissive[0], emissive[1], emissive[2]]
        if double_sided:
            entry["doubleSided"] = True
        self.doc["materials"].append(entry)
        self._materials[key] = len(self.doc["materials"]) - 1
        return self._materials[key]

    def encode(self) -> bytes:
        doc = dict(self.doc)
        if len(self.bin) > 0:
            doc["buffers"] = [{"byteLength": len(self.bin)}]
        else:
            doc.pop("buffers", None)
        for key in _OPTIONAL_TOP_ARRAYS:
            if not doc.get(key):
                doc.pop(key, None)
        scenes = []
        for scene in doc.get("scenes", []):
            s = dict(scene)
            if not s.get("nodes"):
                s.pop("nodes", None)
            scenes.append(s)
        if scenes:
            doc["scenes"] = scenes
        chunk = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode("utf-8")
        chunk += b" " * ((4 - len(chunk) % 4) % 4)
        if len(self.bin) > 0:
            body = bytes(self.bin) + b"\0" * ((4 - len(self.bin) % 4) % 4)
            total = 12 + 8 + len(chunk) + 8 + len(body)
            return (b"glTF" + struct.pack("<II", 2, total)
                    + struct.pack("<I4s", len(chunk), b"JSON") + chunk
                    + struct.pack("<I4s", len(body), b"BIN\0") + body)
        total = 12 + 8 + len(chunk)
        return (b"glTF" + struct.pack("<II", 2, total)
                + struct.pack("<I4s", len(chunk), b"JSON") + chunk)


def _resolve_input_value(inp, tc: Usd.TimeCode, _visited: set[str] | None = None):
    """Read a UsdShade.Input value, following interface connections on Material/NodeGraph."""
    if not inp:
        return None
    if _visited is None:
        _visited = set()
    if inp.HasConnectedSource():
        try:
            sources, _invalid = UsdShade.ConnectableAPI.GetConnectedSources(inp)
        except Exception:
            src = inp.GetConnectedSource()
            sources = [src] if src else []
        for src in sources:
            connectable = getattr(src, "source", None) if not isinstance(src, tuple) else src[0]
            src_name = getattr(src, "sourceName", None) if not isinstance(src, tuple) else src[1]
            if not connectable or not src_name:
                continue
            prim = connectable.GetPrim()
            key = f"{prim.GetPath()}:{src_name}"
            if key in _visited:
                continue
            _visited.add(key)
            upstream = connectable.GetInput(src_name)
            if upstream:
                val = _resolve_input_value(upstream, tc, _visited)
                if val is not None:
                    return val
    return _attr_val(inp.GetAttr(), tc)


def _resolve_asset_path(attr, asset, tc: Usd.TimeCode) -> Path | None:
    """Resolve an Sdf.AssetPath using resolvedPath or layer-anchored relative resolution."""
    if not asset or not asset.path:
        return None
    if asset.resolvedPath:
        resolved = Path(asset.resolvedPath)
        if resolved.exists():
            return resolved
    raw = Path(asset.path)
    if raw.is_absolute():
        return raw
    for time_code in (tc, Usd.TimeCode.Default()):
        try:
            stack = attr.GetPropertyStack(time_code)
        except Exception:
            stack = []
        for spec in stack:
            real = spec.layer.realPath
            if real:
                candidate = (Path(real).parent / asset.path).resolve()
                if candidate.exists():
                    return candidate
    stage = attr.GetPrim().GetStage() if attr else None
    root_real = stage.GetRootLayer().realPath if stage else ""
    if root_real:
        candidate = (Path(root_real).parent / asset.path).resolve()
        if candidate.exists():
            return candidate
    return raw


def _walk_connected_shaders(start_input) -> list[UsdShade.Shader]:
    """Cycle-safe BFS over connected UsdShade shaders and NodeGraphs starting from an Input."""
    if not start_input:
        return []
    visited: set[str] = set()
    queue = [start_input]
    shaders: list[UsdShade.Shader] = []
    while queue:
        inp = queue.pop(0)
        try:
            sources, _invalid = UsdShade.ConnectableAPI.GetConnectedSources(inp)
        except Exception:
            src = inp.GetConnectedSource()
            sources = [src] if src else []
        for src in sources:
            connectable = getattr(src, "source", None) if not isinstance(src, tuple) else src[0]
            src_name = getattr(src, "sourceName", None) if not isinstance(src, tuple) else src[1]
            if not connectable:
                continue
            prim = connectable.GetPrim()
            if not prim or not prim.IsValid():
                continue
            path_str = prim.GetPath().pathString
            if path_str in visited:
                continue
            visited.add(path_str)
            if prim.IsA(UsdShade.Shader):
                shader = UsdShade.Shader(prim)
                shaders.append(shader)
                for sub_inp in shader.GetInputs():
                    if sub_inp.HasConnectedSource():
                        queue.append(sub_inp)
            elif prim.IsA(UsdShade.NodeGraph):
                ng = UsdShade.NodeGraph(prim)
                if src_name:
                    out = ng.GetOutput(src_name)
                    if out and out.HasConnectedSource():
                        queue.append(out)
                for sub_inp in ng.GetInputs():
                    if sub_inp.HasConnectedSource():
                        queue.append(sub_inp)
    return shaders


def _appearance(material, tc: Usd.TimeCode = Usd.TimeCode.Default()):
    """(base, metal, rough, opacity, opacity_threshold, emissive, texture, uv_name).

    Pure: nothing is added to the file, so a caller can compare two variant looks before
    deciding whether the primitive needs a KHR_materials_variants mapping at all.
    """
    base = (0.8, 0.8, 0.8)
    metal = 0.0
    rough = 0.5
    opacity = 1.0
    opacity_threshold = 0.0
    emissive = (0.0, 0.0, 0.0)
    texture: Path | None = None
    uv_name: str | None = None
    if material:
        surface = _surface_source(material)
        if surface:
            for name in ("diffuseColor", "base_color", "metallic", "metalness",
                         "roughness", "specular_roughness", "opacity", "opacityThreshold",
                         "emissiveColor", "emission_color"):
                inp = surface.GetInput(name)
                value = _resolve_input_value(inp, tc) if inp else None
                if value is None:
                    continue
                if name in ("diffuseColor", "base_color"):
                    base = (_clamp01(float(value[0])), _clamp01(float(value[1])),
                            _clamp01(float(value[2])))
                elif name in ("metallic", "metalness"):
                    metal = _clamp01(float(value))
                elif name in ("roughness", "specular_roughness"):
                    rough = _clamp01(float(value))
                elif name == "opacity":
                    opacity = _clamp01(float(value))
                elif name == "opacityThreshold":
                    opacity_threshold = _clamp01(float(value))
                elif name in ("emissiveColor", "emission_color"):
                    emissive = (_clamp01(float(value[0])), _clamp01(float(value[1])),
                                _clamp01(float(value[2])))
            texture, uv_name = _base_colour_texture_and_uv(surface, tc)
    return base, metal, rough, opacity, opacity_threshold, emissive, texture, uv_name


def _surface_source(material):
    """The shader output that shades this material, whichever render context carries it."""
    for context in (UsdShade.Tokens.universalRenderContext, "mtlx"):
        try:
            source = material.ComputeSurfaceSource(context)[0]
        except Exception:  # an unusable context is not an error: try the next one
            source = None
        if source and source.GetPrim().IsValid():
            return source
    return None


def _base_colour_texture_and_uv(surface, tc: Usd.TimeCode = Usd.TimeCode.Default()) -> tuple[Path | None, str | None]:
    """Return (texture_path, uv_primvar_name) behind diffuseColor or base_color, if connected."""
    tex_path: Path | None = None
    uv_name: str | None = None
    for name in ("diffuseColor", "base_color"):
        inp = surface.GetInput(name)
        if not inp or not inp.HasConnectedSource():
            continue
        shaders = _walk_connected_shaders(inp)
        for shader in shaders:
            if tex_path is None:
                file_input = shader.GetInput("file")
                if file_input:
                    asset = _resolve_input_value(file_input, tc)
                    if asset and getattr(asset, "path", None):
                        path = _resolve_asset_path(file_input.GetAttr(), asset, tc)
                        if path is not None and path.suffix.lower() in IMAGE_TYPES:
                            tex_path = path
            if uv_name is None:
                shader_id = str(_attr_val(shader.GetIdAttr(), tc) or "")
                if shader_id.startswith("UsdPrimvarReader"):
                    vn_input = shader.GetInput("varname")
                    vn = _resolve_input_value(vn_input, tc) if vn_input else None
                    if vn:
                        uv_name = str(vn)
        if tex_path is not None:
            break
    return tex_path, uv_name


def _base_colour_texture(surface) -> Path | None:
    """The PNG or JPEG behind diffuseColor or base_color, if one is connected."""
    return _base_colour_texture_and_uv(surface)[0]


def triangulate(counts, indices) -> list[int]:
    out, cursor = [], 0
    for count in counts:
        face = indices[cursor:cursor + count]
        cursor += count
        for i in range(1, count - 1):
            out += [face[0], face[i], face[i + 1]]
    return out


def triangulate_corners(counts, indices, reverse: bool = False) -> tuple[list[int], list[int], list[int]]:
    """Fan-triangulate faces, returning (tri_point_indices, tri_corner_indices, tri_face_indices)."""
    tri_points: list[int] = []
    tri_corners: list[int] = []
    tri_faces: list[int] = []
    cursor = 0
    for face_idx, count in enumerate(counts):
        for i in range(1, count - 1):
            if reverse:
                c0, c1, c2 = cursor, cursor + i + 1, cursor + i
            else:
                c0, c1, c2 = cursor, cursor + i, cursor + i + 1
            tri_points.extend((int(indices[c0]), int(indices[c1]), int(indices[c2])))
            tri_corners.extend((c0, c1, c2))
            tri_faces.extend((face_idx, face_idx, face_idx))
        cursor += count
    return tri_points, tri_corners, tri_faces


def _is_above(parent: str, child: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def _prototype_paths(stage: Usd.Stage) -> list[str]:
    paths: list[str] = []
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdGeom.PointInstancer):
            for target in UsdGeom.PointInstancer(prim).GetPrototypesRel().GetTargets():
                paths.append(str(target))
    return paths


def _is_visible(prim, tc: Usd.TimeCode) -> bool:
    return UsdGeom.Imageable(prim).ComputeVisibility(tc) != UsdGeom.Tokens.invisible


def _variant_mappings(glb: _Glb, prim, inv, double_sided: bool = False) -> list[dict]:
    """One mapping per option of every material variant set above this prim.

    Selections are evaluated strictly inside stage.GetSessionLayer() so the caller's root layer
    is never dirtied, and session-layer state is cleanly restored afterward.
    """
    if inv is None:
        return []
    all_material_sets = [v for v in inv.variant_sets if v.kind == "material"]
    use_namespace = len(all_material_sets) > 1
    sets = [v for v in all_material_sets if _is_above(v.prim_path, prim.GetPath().pathString)]
    if not sets:
        return []
    mappings: list[dict] = []
    mapped_variants: set[int] = set()
    stage = prim.GetStage()
    session_layer = stage.GetSessionLayer()
    tc = Usd.TimeCode(stage_time(prim))
    with Usd.EditContext(stage, session_layer):
        for fact in sets:
            owner = stage.GetPrimAtPath(fact.prim_path)
            if not owner:
                continue
            vset = owner.GetVariantSets().GetVariantSet(fact.set_name)
            spec = session_layer.GetPrimAtPath(owner.GetPath())
            prev_session = (spec.variantSelections[fact.set_name]
                            if (spec and fact.set_name in spec.variantSelections) else None)
            looks = []
            try:
                for option in fact.options:  # read every look before writing any of them
                    vset.SetVariantSelection(option)
                    bound = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
                    looks.append((option, bound, _appearance(bound, tc)))
            finally:
                if prev_session is not None:
                    vset.SetVariantSelection(prev_session)
                else:
                    vset.ClearVariantSelection()
            if len({repr(look[2]) for look in looks}) == 1:
                continue  # this prim looks the same in every variant: nothing to map
            for option, bound, look in looks:
                vname = f"{fact.set_name}:{option}" if use_namespace else option
                if vname not in glb.variants:
                    glb.variants.append(vname)
                v_idx = glb.variants.index(vname)
                if v_idx in mapped_variants:
                    continue
                mapped_variants.add(v_idx)
                index = glb.material(bound, prim.GetName(), tag=f"{fact.set_name}={option}",
                                     look=look, double_sided=double_sided)
                mappings.append({"material": index, "variants": [v_idx]})
    return mappings


def stage_time(prim) -> float:
    """The time a static node is written at: the stage start if authored, else 1.0."""
    stage = prim.GetStage()
    if stage.HasAuthoredTimeCodeRange() or stage.GetRootLayer().HasStartTimeCode():
        return float(stage.GetStartTimeCode())
    return 1.0


def _trs(prim, time: float) -> dict:
    """Translation, rotation (x, y, z, w) and scale from a prim's local-to-world at one time.

    USD Gf.Matrix4d uses row-vector convention (v' = v * S * R * T), so basis scale lengths live
    in the matrix ROWS, not columns. Negative determinant is absorbed into sx so R stays in SO(3).
    """
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode(time))
    translation = matrix.ExtractTranslation()
    row0, row1, row2 = matrix.GetRow(0), matrix.GetRow(1), matrix.GetRow(2)
    r0 = Gf.Vec3d(row0[0], row0[1], row0[2])
    r1 = Gf.Vec3d(row1[0], row1[1], row1[2])
    r2 = Gf.Vec3d(row2[0], row2[1], row2[2])
    sx, sy, sz = r0.GetLength(), r1.GetLength(), r2.GetLength()
    if matrix.GetDeterminant3() < 0.0:
        sx = -sx
    eps = 1e-12
    nz = (abs(sx) > eps, abs(sy) > eps, abs(sz) > eps)
    quat = Gf.Quatd(1.0, 0.0, 0.0, 0.0)
    if all(nz):
        u0, u1, u2 = r0 / sx, r1 / sy, r2 / sz
        rot_mat = Gf.Matrix4d(
            u0[0], u0[1], u0[2], 0.0,
            u1[0], u1[1], u1[2], 0.0,
            u2[0], u2[1], u2[2], 0.0,
            0.0, 0.0, 0.0, 1.0,
        )
        rot_mat.Orthonormalize(False)
        quat = rot_mat.ExtractRotationQuat().GetNormalized()
    elif sum(nz) == 2:
        u0 = (r0 / sx) if nz[0] else None
        u1 = (r1 / sy) if nz[1] else None
        u2 = (r2 / sz) if nz[2] else None
        valid = True
        if u0 is None:
            cross = u1.GetCross(u2)
            valid = cross.GetLength() > eps
            u0 = cross.GetNormalized() if valid else Gf.Vec3d(1, 0, 0)
        elif u1 is None:
            cross = u2.GetCross(u0)
            valid = cross.GetLength() > eps
            u1 = cross.GetNormalized() if valid else Gf.Vec3d(0, 1, 0)
        else:
            cross = u0.GetCross(u1)
            valid = cross.GetLength() > eps
            u2 = cross.GetNormalized() if valid else Gf.Vec3d(0, 0, 1)
        if valid:
            rot_mat = Gf.Matrix4d(
                u0[0], u0[1], u0[2], 0.0,
                u1[0], u1[1], u1[2], 0.0,
                u2[0], u2[1], u2[2], 0.0,
                0.0, 0.0, 0.0, 1.0,
            )
            rot_mat.Orthonormalize(False)
            quat = rot_mat.ExtractRotationQuat().GetNormalized()
    imag = quat.GetImaginary()
    return {"translation": [float(translation[0]), float(translation[1]), float(translation[2])],
            "rotation": [float(imag[0]), float(imag[1]), float(imag[2]), float(quat.GetReal())],
            "scale": [float(sx), float(sy), float(sz)]}


def _same(a: dict, b: dict, eps: float = 1e-6) -> bool:
    for key in a:
        va, vb = a[key], b[key]
        if key == "rotation":
            pos = all(abs(x - y) <= eps for x, y in zip(va, vb))
            neg = all(abs(x + y) <= eps for x, y in zip(va, vb))
            if not (pos or neg):
                return False
        elif not all(abs(x - y) <= eps for x, y in zip(va, vb)):
            return False
    return True


def _node_fields(trs: dict) -> dict:
    """Only the channels that are not identity, so a static node stays small."""
    fields = {}
    if any(abs(v) > 1e-9 for v in trs["translation"]):
        fields["translation"] = trs["translation"]
    rot = trs["rotation"]
    if abs(abs(rot[3]) - 1.0) > 1e-9 or any(abs(v) > 1e-9 for v in rot[:3]):
        fields["rotation"] = rot
    if any(abs(v - 1.0) > 1e-9 for v in trs["scale"]):
        fields["scale"] = trs["scale"]
    return fields


def _animated(prim) -> bool:
    """True when the prim's local-to-world differs across the stage's time range."""
    stage = prim.GetStage()
    if not stage.HasAuthoredTimeCodeRange():
        return False
    start, end = stage.GetStartTimeCode(), stage.GetEndTimeCode()
    if end <= start:
        return False
    probes = [_trs(prim, start + (end - start) * f) for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    return any(not _same(probes[0], p) for p in probes[1:])


def expand_vertices(points, normals, uvs, indices):
    """Make POSITION, NORMAL and TEXCOORD_0 parallel arrays, indexed by the same indices."""
    seen = {}
    positions, out_normals, out_uvs, out_indices = [], [], [], []
    for corner, point_index in enumerate(indices):
        normal = normals[corner] if normals else None
        uv = uvs[corner] if uvs else None
        key = (point_index, tuple(normal) if normal else None, tuple(uv) if uv else None)
        vertex = seen.get(key)
        if vertex is None:
            vertex = len(positions)
            seen[key] = vertex
            p = points[point_index]
            positions.append([float(p[0]), float(p[1]), float(p[2])])
            if normal is not None:
                out_normals.append(list(normal))
            if uv is not None:
                out_uvs.append(list(uv))
        out_indices.append(vertex)
    return positions, out_normals, out_uvs, out_indices


def _resolve_corner_indices(interp: str, val_count: int, pv_indices,
                            tri_points: list[int], tri_corners: list[int],
                            tri_faces: list[int], point_count: int) -> list[int]:
    """Map each triangle vertex corner to its index in `values` given USD interpolation."""
    has_idx = pv_indices is not None and len(pv_indices) > 0
    if interp == UsdGeom.Tokens.constant:
        base = int(pv_indices[0]) if has_idx else 0
        return [base] * len(tri_points)
    if interp == UsdGeom.Tokens.uniform:
        if has_idx:
            return [int(pv_indices[f]) if f < len(pv_indices) else 0 for f in tri_faces]
        return list(tri_faces)
    if interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
        if has_idx:
            return [int(pv_indices[p]) if p < len(pv_indices) else 0 for p in tri_points]
        return list(tri_points)
    if has_idx:
        return [int(pv_indices[c]) if c < len(pv_indices) else 0 for c in tri_corners]
    if tri_corners and max(tri_corners) >= val_count and val_count == point_count:
        return list(tri_points)
    return list(tri_corners)


def _uvs(prim, tri_points: list[int], tri_corners: list[int], tri_faces: list[int],
         point_count: int, time: float, uv_name: str | None = None):
    """One UV per triangle corner, respecting primvar interpolation and indexing."""
    api = UsdGeom.PrimvarsAPI(prim)
    names = ([uv_name] if uv_name else []) + [n for n in ("st", "uv", "uv0", "map1") if n != uv_name]
    primvar = None
    for name in names:
        candidate = api.GetPrimvar(name)
        if candidate and candidate.HasValue():
            primvar = candidate
            break
    if primvar is None:
        return None
    tc = Usd.TimeCode(time)
    values = _attr_val(primvar.GetAttr(), tc)
    if not values:
        return None
    pv_indices = _attr_val(primvar.GetIndicesAttr(), tc) if primvar.IsIndexed() else None
    interp = primvar.GetInterpolation()
    resolved = _resolve_corner_indices(
        interp, len(values), pv_indices, tri_points, tri_corners, tri_faces, point_count
    )
    val_count = len(values)
    out = []
    for idx in resolved:
        if 0 <= idx < val_count:
            v = values[idx]
            out.append([float(v[0]), 1.0 - float(v[1])])
        else:
            out.append([0.0, 0.0])
    return out


def _normals(local, indices):
    out = []
    for i in range(0, len(indices), 3):
        a, b, c = (local[indices[i + k]] for k in range(3))
        cross = (b - a).GetCross(c - a)
        n = cross.GetNormalized() if cross.GetLength() else Gf.Vec3d(0, 1, 0)
        out += [[float(n[0]), float(n[1]), float(n[2])]] * 3
    return out


def _read_normals(mesh: UsdGeom.Mesh, local, tri_points: list[int], tri_corners: list[int],
                  tri_faces: list[int], time: float, flip: bool = False,
                  normal_xform: Gf.Matrix4d | None = None):
    """Read authored normals (primvars:normals or mesh.GetNormalsAttr()), else flat face normals."""
    tc = Usd.TimeCode(time)
    values = None
    pv_indices = None
    interp = None

    pv = UsdGeom.PrimvarsAPI(mesh.GetPrim()).GetPrimvar("normals")
    if pv and pv.HasAuthoredValue():
        values = _attr_val(pv.GetAttr(), tc)
        if values:
            interp = pv.GetInterpolation()
            if pv.IsIndexed():
                pv_indices = _attr_val(pv.GetIndicesAttr(), tc)

    if not values:
        attr = mesh.GetNormalsAttr()
        if attr and attr.HasAuthoredValue():
            values = _attr_val(attr, tc)
            interp = mesh.GetNormalsInterpolation()

    if not values:
        return _normals(local, tri_points)

    resolved = _resolve_corner_indices(
        interp or UsdGeom.Tokens.faceVarying, len(values), pv_indices,
        tri_points, tri_corners, tri_faces, len(local)
    )
    val_count = len(values)
    sign = -1.0 if flip else 1.0
    out = []
    for idx in resolved:
        if 0 <= idx < val_count:
            v = values[idx]
            vec = Gf.Vec3d(float(v[0]), float(v[1]), float(v[2]))
            if normal_xform is not None:
                vec = normal_xform.TransformDir(vec)
            length = vec.GetLength()
            if length > 1e-12:
                vec = (vec / length) * sign
                out.append([float(vec[0]), float(vec[1]), float(vec[2])])
            else:
                out.append([0.0, 1.0, 0.0])
        else:
            out.append([0.0, 1.0, 0.0])
    return out


def validate_glb(path: Path) -> tuple[bool, list[str]]:
    """Structural glTF 2.0 / GLB validation: container, spec minItems, accessors, images, extensions."""
    problems: list[str] = []
    raw = Path(path).read_bytes()
    if len(raw) < 20:
        return False, ["file is shorter than a glTF header"]
    magic, version, total = struct.unpack("<4sII", raw[:12])
    if magic != b"glTF":
        problems.append("not a glTF file: bad magic")
    if version != 2:
        problems.append(f"glTF version {version}, expected 2")
    if total != len(raw):
        problems.append(f"header length {total} does not match the file size {len(raw)}")
    json_len, json_type = struct.unpack("<I4s", raw[12:20])
    if json_type != b"JSON":
        problems.append("first chunk is not JSON")
    if json_len % 4:
        problems.append(f"JSON chunk length {json_len} is not 4-byte aligned")
    try:
        doc = json.loads(raw[20:20 + json_len].decode("utf-8"))
    except Exception as exc:  # a broken chunk is the failure this function exists to catch
        return False, problems + [f"JSON chunk does not parse: {exc}"]

    for key in (*_OPTIONAL_TOP_ARRAYS, "scenes", "buffers"):
        if key in doc and isinstance(doc[key], list) and len(doc[key]) == 0:
            problems.append(f"top-level '{key}' array is empty (violates glTF 2.0 minItems: 1)")
    for s_idx, scene in enumerate(doc.get("scenes", [])):
        if "nodes" in scene and len(scene["nodes"]) == 0:
            problems.append(f"scene {s_idx} has an empty 'nodes' array")

    bin_offset = 20 + json_len
    bin_data_start = None
    if bin_offset < len(raw):
        try:
            bin_len, bin_type = struct.unpack("<I4s", raw[bin_offset:bin_offset + 8])
            if bin_type != b"BIN\0":
                problems.append("second chunk is not BIN")
            if bin_len % 4:
                problems.append(f"BIN chunk length {bin_len} is not 4-byte aligned")
            if bin_offset + 8 + bin_len != len(raw):
                problems.append("BIN chunk length does not match remaining file bytes")
            if doc.get("buffers"):
                buf_len = doc["buffers"][0].get("byteLength", 0)
                if buf_len <= 0:
                    problems.append("buffer byteLength must be > 0")
                if buf_len > bin_len:
                    problems.append("buffer byteLength is longer than the BIN chunk")
            bin_data_start = bin_offset + 8
        except struct.error:
            problems.append("truncated BIN chunk header")
    elif doc.get("buffers"):
        problems.append("no BIN chunk")

    views = doc.get("bufferViews", [])
    accessors = doc.get("accessors", [])
    for i, accessor in enumerate(accessors):
        bv_idx = accessor.get("bufferView")
        if bv_idx is None:
            continue
        view = views[bv_idx] if 0 <= bv_idx < len(views) else None
        if view is None:
            problems.append(f"accessor {i} points at a missing bufferView")
            continue
        width = COMPONENT_WIDTH.get(accessor.get("componentType"))
        if width is None:
            problems.append(f"accessor {i} has unknown componentType {accessor.get('componentType')}")
            continue
        arity = ARITY.get(accessor.get("type"))
        if arity is None:
            problems.append(f"accessor {i} has unknown type {accessor.get('type')}")
            continue
        acc_offset = accessor.get("byteOffset", 0)
        count = accessor["count"]
        stride = view.get("byteStride")
        need = (acc_offset + stride * (count - 1) + arity * width) if (stride and count > 0) else (
            acc_offset + count * arity * width)
        if need > view["byteLength"]:
            problems.append(f"accessor {i} needs {need} bytes, its view has {view['byteLength']}")

    for m, mesh in enumerate(doc.get("meshes", [])):
        for pr, primitive in enumerate(mesh.get("primitives", [])):
            attrs = primitive.get("attributes", {})
            counts = {name: accessors[idx]["count"] for name, idx in attrs.items()}
            if len(set(counts.values())) > 1:
                problems.append(f"mesh {m} primitive {pr}: attribute counts disagree ({counts}); "
                                "they must be parallel arrays indexed by the same indices")
            if counts and primitive.get("indices") is not None:
                idx_accessor = accessors[primitive["indices"]]
                allowed = next(iter(counts.values()))
                view = views[idx_accessor["bufferView"]]
                ctype = idx_accessor["componentType"]
                width = COMPONENT_WIDTH.get(ctype, 4)
                fmt_ch = INDEX_STRUCT_FMT.get(ctype)
                count = idx_accessor["count"]
                if bin_data_start is not None and fmt_ch is not None:
                    start = bin_data_start + view.get("byteOffset", 0) + idx_accessor.get("byteOffset", 0)
                    if start + count * width <= len(raw):
                        data = raw[start:start + count * width]
                        if max(struct.unpack("<%d%s" % (count, fmt_ch), data), default=0) >= allowed:
                            problems.append(f"mesh {m} primitive {pr}: an index exceeds the {allowed} vertices")
                if primitive.get("mode", 4) == 4 and count % 3:
                    problems.append(f"mesh {m} primitive {pr}: {count} indices is not a multiple of 3 (mode 4)")

    for i, image in enumerate(doc.get("images", [])):
        if "bufferView" in image:
            bv_idx = image["bufferView"]
            if bv_idx < 0 or bv_idx >= len(views):
                problems.append(f"image {i} points at a missing bufferView")
            elif "target" in views[bv_idx]:
                problems.append(f"image {i} bufferView {bv_idx} must not set 'target'")
        elif not image.get("uri"):
            problems.append(f"image {i} has neither a bufferView nor a uri")

    for i, texture in enumerate(doc.get("textures", [])):
        if texture.get("source", 0) >= len(doc.get("images", [])):
            problems.append(f"texture {i} points at a missing image")
        if "sampler" in texture and texture["sampler"] >= len(doc.get("samplers", [])):
            problems.append(f"texture {i} points at a missing sampler")

    for a, animation in enumerate(doc.get("animations", [])):
        channels = animation.get("channels", [])
        samplers = animation.get("samplers", [])
        if not channels:
            problems.append(f"animation {a} has no channels")
        if not samplers:
            problems.append(f"animation {a} has no samplers")
        for c, channel in enumerate(channels):
            sampler = samplers[channel["sampler"]] if channel.get("sampler", 99) < len(samplers) else None
            if sampler is None:
                problems.append(f"animation {a} channel {c} points at a missing sampler")
                continue
            if sampler["input"] >= len(accessors):
                problems.append(f"animation {a} channel {c} has a missing input accessor")
            if sampler["output"] >= len(accessors):
                problems.append(f"animation {a} channel {c} has a missing output accessor")
            if channel.get("target", {}).get("node", 0) >= len(doc.get("nodes", [])):
                problems.append(f"animation {a} channel {c} targets a missing node")
        for s, sampler in enumerate(samplers):
            if sampler["input"] >= len(accessors) or sampler["output"] >= len(accessors):
                continue
            inputs = accessors[sampler["input"]]
            outputs = accessors[sampler["output"]]
            if inputs["count"] != outputs["count"]:
                problems.append(f"animation {a} sampler {s}: {inputs['count']} times, {outputs['count']} values")
            if "min" not in inputs or "max" not in inputs:
                problems.append(f"animation {a} sampler {s}: input accessor has no min/max")
            for role, acc in (("input", inputs), ("output", outputs)):
                bv_idx = acc.get("bufferView")
                if bv_idx is not None and 0 <= bv_idx < len(views) and "target" in views[bv_idx]:
                    problems.append(
                        f"animation {a} sampler {s} {role} bufferView {bv_idx} must not set 'target'"
                    )

    declared = doc.get("extensions", {}).get("KHR_materials_variants", {}).get("variants", [])
    if "KHR_materials_variants" in doc.get("extensionsUsed", []):
        if not declared:
            problems.append("KHR_materials_variants is used but declares no variants")
        for m, mesh in enumerate(doc.get("meshes", [])):
            for pr, primitive in enumerate(mesh.get("primitives", [])):
                ext = primitive.get("extensions", {}).get("KHR_materials_variants")
                if ext is None:
                    continue
                mappings = ext.get("mappings", [])
                if not mappings:
                    problems.append(f"mesh {m} primitive {pr} has empty KHR_materials_variants mappings")
                seen: set[int] = set()
                for mapping in mappings:
                    mat_idx = mapping.get("material", -1)
                    if mat_idx < 0 or mat_idx >= len(doc.get("materials", [])):
                        problems.append(f"mesh {m} primitive {pr} maps to a missing material")
                    m_variants = mapping.get("variants", [])
                    if not m_variants:
                        problems.append(f"mesh {m} primitive {pr} has a variant mapping with no variants")
                    for variant in m_variants:
                        if variant < 0 or variant >= len(declared):
                            problems.append(
                                f"mesh {m} primitive {pr} maps to variant {variant}, only {len(declared)} declared"
                            )
                        elif variant in seen:
                            problems.append(f"mesh {m} primitive {pr} maps variant {variant} more than once")
                        seen.add(variant)
    return not problems, problems


class GltfWriter:
    name = FORMAT

    def __init__(self, report: LossReport, root: str | None = None):
        self.report = report
        self.root = root  # the root layer identifier the report was built from

    def entry_file(self, stage: Usd.Stage, out_dir: Path) -> Path:
        name = stage.GetDefaultPrim().GetName() if stage.GetDefaultPrim() else "stage"
        return Path(out_dir) / f"{name}.glb"

    def write(self, stage: Usd.Stage, inv, out_dir: Path, settings) -> ExportResult:
        if self.report is None:
            raise ValueError("the glTF writer needs its report first (principle 1)")
        if self.root is not None and self.root != stage.GetRootLayer().identifier:
            raise ValueError(f"this report was built for {self.root}, not {stage.GetRootLayer().identifier}")
        if any(item.severity == "blocks" for item in self.report.items):
            entry = self.entry_file(stage, out_dir)
            return ExportResult(FORMAT, entry, [], 0, self.report, False)

        self.inv = inv
        glb = _Glb()
        glb.doc["scenes"][0]["extras"] = _settings_extras(settings)

        proto_paths = _prototype_paths(stage)
        cameras = []
        lights = []
        for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
            if prim.IsA(UsdGeom.Mesh):
                if UsdGeom.Imageable(prim).ComputePurpose() in ("default", "render"):
                    if not any(_is_above(proto, str(prim.GetPath())) for proto in proto_paths):
                        self._mesh(glb, prim)
            elif prim.IsA(UsdGeom.PointInstancer):
                self._instancer(glb, prim)
            elif prim.IsA(UsdGeom.Camera):
                cameras.append(prim)
            elif prim.GetTypeName().endswith("Light"):
                lights.append(prim)

        want_cam = getattr(settings, "camera", None) if settings is not None else None
        if want_cam:
            cameras.sort(key=lambda c: 0 if str(c.GetPath()) == want_cam else 1)
        for cam in cameras:
            self._camera(glb, cam)
        for light in lights:
            self._light(glb, light)

        # M7: normalize stage upAxis (Z-up -> Y-up) and metersPerUnit (!= 1.0) in one root node.
        up_axis = UsdGeom.GetStageUpAxis(stage)
        mpu = float(UsdGeom.GetStageMetersPerUnit(stage)) if stage.HasAuthoredMetadata("metersPerUnit") else 1.0
        need_up = up_axis == UsdGeom.Tokens.z
        need_scale = abs(mpu - 1.0) > 1e-6
        if (need_up or need_scale) and glb.doc["scenes"][0]["nodes"]:
            children = list(glb.doc["scenes"][0]["nodes"])
            root_fields: dict = {"name": "__usd_stage_root__", "children": children}
            if need_up:
                half = math.sqrt(0.5)
                root_fields["rotation"] = [-half, 0.0, 0.0, half]
            if need_scale:
                root_fields["scale"] = [mpu, mpu, mpu]
            glb.doc["nodes"].append(root_fields)
            glb.doc["scenes"][0]["nodes"] = [len(glb.doc["nodes"]) - 1]

        if glb.variants:  # KHR_materials_variants: every look, and the player picks one
            if "KHR_materials_variants" not in glb.doc["extensionsUsed"]:
                glb.doc["extensionsUsed"].append("KHR_materials_variants")
            glb.doc.setdefault("extensions", {}).setdefault(
                "KHR_materials_variants", {})["variants"] = [{"name": n} for n in glb.variants]
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        entry = self.entry_file(stage, out_dir)
        data = glb.encode()
        entry.write_bytes(data)
        try:
            ok, problems = validate_glb(entry)
        except Exception as exc:  # a file that cannot be read is not a validated file
            ok, problems = False, [str(exc)]
        if not ok:
            raise RuntimeError("the .glb failed its own structural validation: " + "; ".join(problems))
        return ExportResult(FORMAT, entry, [entry], len(data), self.report, ok)

    # -- prims -----------------------------------------------------------------------
    def _build_primitive(self, glb: _Glb, prim, time: float, point_xform: Gf.Matrix4d | None = None,
                         with_variants: bool = True) -> dict | None:
        mesh = UsdGeom.Mesh(prim)
        tc = Usd.TimeCode(time)
        counts = _attr_val(mesh.GetFaceVertexCountsAttr(), tc)
        indices = _attr_val(mesh.GetFaceVertexIndicesAttr(), tc)
        points = _attr_val(mesh.GetPointsAttr(), tc)
        if not counts or not indices or not points:
            return None
        reverse = _attr_val(mesh.GetOrientationAttr(), tc) == UsdGeom.Tokens.leftHanded
        double_sided = bool(_attr_val(mesh.GetDoubleSidedAttr(), tc))
        tri_points, tri_corners, tri_faces = triangulate_corners(counts, indices, reverse=reverse)
        if point_xform is not None:
            local = [point_xform.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in points]
            normal_xform = point_xform.GetInverse().GetTranspose()
        else:
            local = [Gf.Vec3d(p[0], p[1], p[2]) for p in points]
            normal_xform = None
        bound = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
        look = _appearance(bound, tc)
        normals = _read_normals(mesh, local, tri_points, tri_corners, tri_faces, time,
                                flip=False, normal_xform=normal_xform)
        uv = _uvs(prim, tri_points, tri_corners, tri_faces, len(points), time, uv_name=look[7])
        positions, expanded_normals, expanded_uv, indices_out = expand_vertices(local, normals, uv, tri_points)
        attributes = {"POSITION": glb.accessor(positions, "float", "VEC3", minmax=True),
                      "NORMAL": glb.accessor(expanded_normals, "float", "VEC3")}
        if expanded_uv:
            attributes["TEXCOORD_0"] = glb.accessor(expanded_uv, "float", "VEC2")
        primitive: dict = {
            "attributes": attributes,
            "indices": glb.accessor(indices_out, "uint", "SCALAR", target=34963),
            "mode": 4,
            "material": glb.material(bound, prim.GetName(), look=look, double_sided=double_sided),
        }
        if with_variants:
            mappings = _variant_mappings(glb, prim, getattr(self, "inv", None), double_sided=double_sided)
            if mappings:
                primitive["extensions"] = {"KHR_materials_variants": {"mappings": mappings}}
        return primitive

    def _mesh(self, glb: _Glb, prim) -> None:
        t = stage_time(prim)
        if not _is_visible(prim, Usd.TimeCode(t)):
            return
        primitive = self._build_primitive(glb, prim, t, with_variants=True)
        if primitive is None:
            return
        glb.doc["meshes"].append({"name": prim.GetName(), "primitives": [primitive]})
        node = glb.node(name=str(prim.GetPath()), mesh=len(glb.doc["meshes"]) - 1,
                        **_node_fields(_trs(prim, t)))
        self._animate(glb, prim, node)

    def _instancer(self, glb: _Glb, prim) -> None:
        t = stage_time(prim)
        tc = Usd.TimeCode(t)
        if not _is_visible(prim, tc):
            return
        instancer = UsdGeom.PointInstancer(prim)
        positions = _attr_val(instancer.GetPositionsAttr(), tc)
        prototypes = instancer.GetPrototypesRel().GetTargets()
        if not positions or not prototypes:
            return
        proto_indices = _attr_val(instancer.GetProtoIndicesAttr(), tc)
        orientations = _attr_val(instancer.GetOrientationsAttr(), tc)
        scales = _attr_val(instancer.GetScalesAttr(), tc)
        invisible_ids = set(_attr_val(instancer.GetInvisibleIdsAttr(), tc) or ())
        ids = _attr_val(instancer.GetIdsAttr(), tc)

        groups: dict[int, list[int]] = {}
        for idx in range(len(positions)):
            inst_id = int(ids[idx]) if ids is not None and idx < len(ids) else idx
            if inst_id in invisible_ids:
                continue
            p_idx = int(proto_indices[idx]) if proto_indices is not None and idx < len(proto_indices) else 0
            if 0 <= p_idx < len(prototypes):
                groups.setdefault(p_idx, []).append(idx)
        if not groups:
            return

        stage = prim.GetStage()
        base_trs = _trs(prim, t)
        for p_idx, inst_list in sorted(groups.items()):
            proto = stage.GetPrimAtPath(prototypes[p_idx])
            if not proto or not proto.IsValid():
                continue
            proto_to_world = UsdGeom.Xformable(proto).ComputeLocalToWorldTransform(tc)
            world_to_proto = proto_to_world.GetInverse()
            primitives = []
            for p_mesh in Usd.PrimRange(proto, Usd.TraverseInstanceProxies()):
                if not p_mesh.IsA(UsdGeom.Mesh):
                    continue
                if UsdGeom.Imageable(p_mesh).ComputePurpose() not in ("default", "render"):
                    continue
                if p_mesh == proto:
                    rel_xform = None
                else:
                    rel_xform = UsdGeom.Xformable(p_mesh).ComputeLocalToWorldTransform(tc) * world_to_proto
                built = self._build_primitive(glb, p_mesh, t, point_xform=rel_xform, with_variants=False)
                if built is not None:
                    primitives.append(built)
            if not primitives:
                continue
            glb.doc["meshes"].append({"name": proto.GetName(), "primitives": primitives})
            mesh_idx = len(glb.doc["meshes"]) - 1

            trans_vals = [[float(positions[i][0]), float(positions[i][1]), float(positions[i][2])]
                          for i in inst_list]
            inst_attrs: dict = {
                "TRANSLATION": glb.accessor(trans_vals, "float", "VEC3", target=None),
            }
            if orientations is not None and len(orientations) > 0:
                rot_vals = []
                for i in inst_list:
                    if i < len(orientations):
                        q = orientations[i]
                        im = q.GetImaginary()
                        qx, qy, qz, qw = float(im[0]), float(im[1]), float(im[2]), float(q.GetReal())
                        length = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
                        if length > 1e-12:
                            rot_vals.append([qx / length, qy / length, qz / length, qw / length])
                        else:
                            rot_vals.append([0.0, 0.0, 0.0, 1.0])
                    else:
                        rot_vals.append([0.0, 0.0, 0.0, 1.0])
                inst_attrs["ROTATION"] = glb.accessor(rot_vals, "float", "VEC4", target=None)
            if scales is not None and len(scales) > 0:
                scale_vals = []
                for i in inst_list:
                    if i < len(scales):
                        s = scales[i]
                        scale_vals.append([float(s[0]), float(s[1]), float(s[2])])
                    else:
                        scale_vals.append([1.0, 1.0, 1.0])
                inst_attrs["SCALE"] = glb.accessor(scale_vals, "float", "VEC3", target=None)

            if "EXT_mesh_gpu_instancing" not in glb.doc["extensionsUsed"]:
                glb.doc["extensionsUsed"].append("EXT_mesh_gpu_instancing")
            node_name = str(prim.GetPath()) if len(groups) == 1 else f"{prim.GetPath()}[{p_idx}]"
            node = glb.node(name=node_name, mesh=mesh_idx, **_node_fields(base_trs),
                            extensions={"EXT_mesh_gpu_instancing": {"attributes": inst_attrs}})
            self._animate(glb, prim, node)

    def _animate(self, glb: _Glb, prim, node: int) -> None:
        """Bake a moving node's local-to-world as glTF animation channels."""
        if not _animated(prim):
            return
        stage = prim.GetStage()
        start, end = int(stage.GetStartTimeCode()), int(stage.GetEndTimeCode())
        fps = stage.GetTimeCodesPerSecond() or stage.GetFramesPerSecond() or 24.0
        step = max(1, round((end - start + 1) / 600))  # keep a long shot's file bounded
        frames = list(range(start, end + 1, step))
        if frames[-1] != end:
            frames.append(end)
        samples = [_trs(prim, frame) for frame in frames]
        times = [(frame - start) / float(fps) for frame in frames]

        input_accessor = None
        animation = glb.doc["animations"][0] if glb.doc["animations"] else None
        for path in ("translation", "rotation", "scale"):
            values = [list(sample[path]) for sample in samples]
            if path == "rotation":
                # Keep quaternion sign continuous so LINEAR interpolation takes the shortest arc.
                for i in range(1, len(values)):
                    prev, curr = values[i - 1], values[i]
                    if sum(a * b for a, b in zip(prev, curr)) < 0.0:
                        values[i] = [-c for c in curr]
            if all(_same({path: values[0]}, {path: v}) for v in values[1:]):
                continue  # this channel never moves
            if animation is None:
                glb.doc["animations"].append({"name": "take", "channels": [], "samplers": []})
                animation = glb.doc["animations"][0]
            if input_accessor is None:
                input_accessor = glb.accessor(times, "float", "SCALAR", minmax=True, target=None)
            output = glb.accessor(values, "float", "VEC4" if path == "rotation" else "VEC3", target=None)
            animation["samplers"].append({"input": input_accessor, "output": output,
                                          "interpolation": "LINEAR"})
            animation["channels"].append({"sampler": len(animation["samplers"]) - 1,
                                          "target": {"node": node, "path": path}})

    def _camera(self, glb: _Glb, prim) -> None:
        t = stage_time(prim)
        tc = Usd.TimeCode(t)
        camera = UsdGeom.Camera(prim)
        focal = float(_attr_val(camera.GetFocalLengthAttr(), tc) or 35.0)
        v_aperture = float(_attr_val(camera.GetVerticalApertureAttr(), tc) or 20.25)
        h_aperture = float(_attr_val(camera.GetHorizontalApertureAttr(), tc) or 0.0)
        clip = _attr_val(camera.GetClippingRangeAttr(), tc)
        znear = max(1e-4, float(clip[0])) if clip else 0.05
        zfar = max(znear + 1.0, float(clip[1])) if clip else 1000.0
        persp: dict = {
            "yfov": 2.0 * math.atan(v_aperture / (2.0 * max(1e-4, focal))),
            "znear": znear,
            "zfar": zfar,
        }
        if h_aperture > 0.0 and v_aperture > 0.0:
            persp["aspectRatio"] = h_aperture / v_aperture
        glb.doc["cameras"].append({"type": "perspective", "perspective": persp})
        node = glb.node(name=str(prim.GetPath()), camera=len(glb.doc["cameras"]) - 1,
                        **_node_fields(_trs(prim, t)))
        self._animate(glb, prim, node)

    def _light(self, glb: _Glb, prim) -> None:
        kind = prim.GetTypeName()
        if kind not in ("SphereLight", "DistantLight", "CylinderLight"):
            return
        t = stage_time(prim)
        tc = Usd.TimeCode(t)
        if not _is_visible(prim, tc):
            return
        lumens = _attr_val(prim.GetAttribute("inputs:intensity"), tc)
        lumens = 1.0 if lumens is None else float(lumens)
        stops = _attr_val(prim.GetAttribute("inputs:exposure"), tc)
        stops = 0.0 if stops is None else float(stops)
        c = _attr_val(prim.GetAttribute("inputs:color"), tc)
        c = (1.0, 1.0, 1.0) if c is None else c
        radiance = max(0.0, lumens * (2.0 ** stops))
        normalized = bool(_attr_val(prim.GetAttribute("inputs:normalize"), tc))
        if kind == "DistantLight":
            intensity = radiance
        elif kind == "SphereLight":
            treat_as_point = bool(_attr_val(prim.GetAttribute("inputs:treatAsPoint"), tc))
            radius = _attr_val(prim.GetAttribute("inputs:radius"), tc)
            r = 0.5 if radius is None else float(radius)
            intensity = radiance if (treat_as_point or normalized) else radiance * math.pi * r * r
        else:
            intensity = radiance
        lights = glb.doc.setdefault("extensions", {}).setdefault("KHR_lights_punctual", {"lights": []})
        lights["lights"].append({"type": "point" if kind != "DistantLight" else "directional",
                                 "color": [_clamp01(float(c[0])), _clamp01(float(c[1])), _clamp01(float(c[2]))],
                                 "intensity": float(intensity)})
        if "KHR_lights_punctual" not in glb.doc["extensionsUsed"]:
            glb.doc["extensionsUsed"].append("KHR_lights_punctual")
        node = glb.node(name=str(prim.GetPath()),
                        **_node_fields(_trs(prim, t)),
                        extensions={"KHR_lights_punctual": {"light": len(lights["lights"]) - 1}})
        self._animate(glb, prim, node)


def _settings_extras(settings) -> dict:
    """The seven /WebSettings values, as data for the player; glTF has nowhere else to put them."""
    if settings is None:
        return {}
    return {"web": {"title": settings.title, "camera": settings.camera,
                    "hudVariantSets": settings.hudVariantSets, "showProxy": settings.showProxy,
                    "backgroundBlur": settings.backgroundBlur,
                    "exposureOffset": settings.exposureOffset, "showHud": settings.showHud}}
