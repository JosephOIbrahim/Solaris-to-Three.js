"""glTF writer: one .glb with a JSON chunk and a BIN chunk, packed with struct.

Blueprint 4.4, mission M7, and the route chosen in docs/decisions/0001-gltf-writer.md: pure
Python from pxr, no glTF library, no numpy. The spike in scripts/spike_gltf_writer.py proved
the container and the hard parts; this is the production version of the same mechanics.

What it writes: meshes (quads triangulated, purpose default and render), world transforms baked
into vertices, UsdPreviewSurface to pbrMetallicRoughness, base colour textures embedded as PNG
or JPEG, one camera, KHR_lights_punctual for sphere and distant lights, and EXT_mesh_gpu_instancing
for PointInstancers. The seven /WebSettings values travel in `extras`, so the player can read them.

What it does not write, and the profile reports instead: subdivision limit surfaces, geometry
variants other than the selected one, material variants, area and dome lights, volumes, and
animation beyond the current time sample.

validate_glb() re-opens what was written and checks the container structurally; the M7 unit test
and ExportResult.validated both use it.
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

from ..report import LossReport
from ..textures import copy  # noqa: F401  (kept for parity with the USD writer's imports)
from .base import ExportResult

FORMAT = "gltf"
COMPONENT = {"float": 5126, "uint": 5125}
ARITY = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


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
        self.variants: list[str] = []  # KHR_materials_variants names, in the order they are met

    def view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        offset = len(self.bin)
        self.bin += data
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        self.doc["bufferViews"].append(view)
        return len(self.doc["bufferViews"]) - 1

    def accessor(self, values, kind: str, gtype: str, minmax: bool = False,
                 target: int = 34962) -> int:
        fmt = {"float": "f", "uint": "I"}[kind]
        flat = list(values) if gtype == "SCALAR" else [c for v in values for c in v]
        data = struct.pack("<%d%s" % (len(flat), fmt), *flat)
        accessor = {"bufferView": self.view(data, target), "componentType": COMPONENT[kind],
                    "count": len(values), "type": gtype}
        if minmax and values:
            if gtype == "SCALAR":  # animation times: plain floats, min/max are required by the spec
                accessor["min"], accessor["max"] = [min(values)], [max(values)]
            else:
                accessor["min"] = [min(v[i] for v in values) for i in range(ARITY[gtype])]
                accessor["max"] = [max(v[i] for v in values) for i in range(ARITY[gtype])]
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
        if str(path) in self._images:
            return self._images[str(path)]
        view = self.view(path.read_bytes())
        self.doc["images"].append({"mimeType": mime, "bufferView": view, "name": path.name})
        self.doc["samplers"].append({"magFilter": 9729, "minFilter": 9987,
                                     "wrapS": 10497, "wrapT": 10497})
        self.doc["textures"].append({"sampler": len(self.doc["samplers"]) - 1,
                                     "source": len(self.doc["images"]) - 1})
        index = len(self.doc["textures"]) - 1
        self._images[str(path)] = index
        return index

    def material(self, material: UsdShade.Material | None, label: str, tag: str = "",
                 look=None) -> int:
        key = (str(material.GetPath()) if material else f"default:{label}") + (f"|{tag}" if tag else "")
        if key in self._materials:
            return self._materials[key]
        base, metal, rough, texture = look if look is not None else _appearance(material)
        pbr = {"baseColorFactor": [base[0], base[1], base[2], 1.0],
               "metallicFactor": metal, "roughnessFactor": rough}
        entry = {"name": label, "pbrMetallicRoughness": pbr}
        if texture:
            index = self.image(texture)
            if index is not None:
                pbr["baseColorTexture"] = {"index": index}
        self.doc["materials"].append(entry)
        self._materials[key] = len(self.doc["materials"]) - 1
        return self._materials[key]

    def encode(self) -> bytes:
        self.doc["buffers"] = [{"byteLength": len(self.bin)}]
        chunk = json.dumps(self.doc, separators=(",", ":")).encode()
        chunk += b" " * ((4 - len(chunk) % 4) % 4)
        body = bytes(self.bin) + b"\0" * ((4 - len(self.bin) % 4) % 4)
        total = 12 + 8 + len(chunk) + 8 + len(body)
        return (b"glTF" + struct.pack("<II", 2, total)
                + struct.pack("<I4s", len(chunk), b"JSON") + chunk
                + struct.pack("<I4s", len(body), b"BIN\0") + body)


def _appearance(material):
    """(base colour, metallic, roughness, texture path) as the material reads right now.

    Pure: nothing is added to the file, so a caller can compare two variants looks before
    deciding whether the primitive needs a KHR_materials_variants mapping at all.
    """
    base, metal, rough, texture = (0.8, 0.8, 0.8), 0.0, 0.5, None
    if material:
        surface = _surface_source(material)
        if surface:
            for name in ("diffuseColor", "base_color", "metallic", "metalness",
                         "roughness", "specular_roughness"):
                inp = surface.GetInput(name)
                value = inp.Get() if inp else None
                if value is None:
                    continue
                if name in ("diffuseColor", "base_color"):
                    base = (float(value[0]), float(value[1]), float(value[2]))
                elif name in ("metallic", "metalness"):
                    metal = float(value)
                else:
                    rough = float(value)
            texture = _base_colour_texture(surface)
    return base, metal, rough, texture


def _surface_source(material):
    """The shader output that shades this material, whichever render context carries it.

    A UsdPreviewSurface material answers "universal". A MaterialX material from Karma answers
    "mtlx" only: its output is `mtlx:surface`, and asking the wrong context returns nothing,
    which used to leave every MaterialX material at the default grey.
    """
    for context in ("universal", "mtlx"):
        try:
            source = material.ComputeSurfaceSource(context)[0]
        except Exception:  # an unusable context is not an error: try the next one
            source = None
        if source and source.GetPrim().IsValid():
            return source
    return None


def _base_colour_texture(surface) -> Path | None:
    """The PNG or JPEG behind diffuseColor or base_color, if one is connected."""
    for name in ("diffuseColor", "base_color"):
        inp = surface.GetInput(name)
        source = inp.GetConnectedSource() if inp else None
        if not source:
            continue
        shader = UsdShade.Shader(source[0].GetPrim())
        for candidate in (shader, *[UsdShade.Shader(r.GetTargets()[0].GetPrim())
                                    for r in shader.GetInputs()
                                    if r.GetConnectedSource()]):
            file_input = candidate.GetInput("file")
            asset = file_input.Get() if file_input else None
            if asset and asset.path:
                resolved = asset.resolvedPath or asset.path
                path = Path(resolved)
                if path.suffix.lower() in IMAGE_TYPES:
                    return path
    return None


def triangulate(counts, indices) -> list[int]:
    out, cursor = [], 0
    for count in counts:
        face = indices[cursor:cursor + count]
        cursor += count
        for i in range(1, count - 1):
            out += [face[0], face[i], face[i + 1]]
    return out


def _is_above(parent: str, child: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def _variant_mappings(glb: _Glb, prim, inv) -> list[dict]:
    """One mapping per option of every material variant set above this prim.

    The look of a material can be authored inside a variant, so the same material prim renders
    differently per option. Each option is selected in turn, the material is read under it, and
    the primitive is mapped to that option's material index. The selection is put back.
    """
    if inv is None:
        return []
    sets = [v for v in inv.variant_sets
            if v.kind == "material" and _is_above(v.prim_path, prim.GetPath().pathString)]
    mappings: list[dict] = []
    stage = prim.GetStage()
    for fact in sets:
        owner = stage.GetPrimAtPath(fact.prim_path)
        if not owner:
            continue
        vset = owner.GetVariantSets().GetVariantSet(fact.set_name)
        original = vset.GetVariantSelection()
        looks = []
        try:
            for option in fact.options:  # read every look before writing any of them
                vset.SetVariantSelection(option)
                bound = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
                looks.append((option, bound, _appearance(bound)))
        finally:
            vset.SetVariantSelection(original)
        if len({repr(look[2]) for look in looks}) == 1:
            continue  # this prim looks the same in every variant: nothing to map
        for option, bound, look in looks:
            if option not in glb.variants:
                glb.variants.append(option)
            index = glb.material(bound, prim.GetName(), tag=f"{fact.set_name}={option}", look=look)
            mappings.append({"material": index, "variants": [glb.variants.index(option)]})
    return mappings


def stage_time(prim) -> float:
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


def _node_fields(trs: dict) -> dict:
    """Only the channels that are not identity, so a static node stays small."""
    fields = {}
    if any(abs(v) > 1e-9 for v in trs["translation"]):
        fields["translation"] = trs["translation"]
    if abs(trs["rotation"][3] - 1.0) > 1e-9 or any(abs(v) > 1e-9 for v in trs["rotation"][:3]):
        fields["rotation"] = trs["rotation"]
    if any(abs(v - 1.0) > 1e-9 for v in trs["scale"]):
        fields["scale"] = trs["scale"]
    return fields


def _animated(prim) -> bool:
    """True when the prim's local-to-world differs across the stage's time range."""
    stage = prim.GetStage()
    start, end = stage.GetStartTimeCode(), stage.GetEndTimeCode()
    if start is None or end is None or end <= start:
        return False
    probes = [_trs(prim, start), _trs(prim, (start + end) / 2.0), _trs(prim, end)]
    return not (_same(probes[0], probes[1]) and _same(probes[1], probes[2]))


def _world_points(prim, points):
    xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    return [xform.Transform(Gf.Vec3d(p)) for p in points]


def _uvs(prim, indices, point_count):
    primvar = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
    if not primvar or not primvar.HasValue():
        return None
    values, corners = primvar.Get(), primvar.GetIndices()
    per_point = [None] * point_count
    if corners:
        for corner, index in enumerate(corners):
            if index < point_count:
                per_point[index] = values[corner]
    else:
        per_point = list(values)
    return [[p[0], 1.0 - p[1]] if p else [0.0, 0.0] for p in (per_point[i] for i in indices)]


def _normals(world, indices):
    out = []
    for i in range(0, len(indices), 3):
        a, b, c = (world[indices[i + k]] for k in range(3))
        cross = (b - a).GetCross(c - a)
        n = cross.GetNormalized() if cross.GetLength() else Gf.Vec3d(0, 1, 0)
        out += [[n[0], n[1], n[2]]] * 3
    return out


def validate_glb(path: Path) -> tuple[bool, list[str]]:
    """The structural checks the M7 test makes: container, accessors, images."""
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
    try:
        doc = json.loads(raw[20:20 + json_len])
    except Exception as exc:  # a broken chunk is the failure this function exists to catch
        return False, problems + [f"JSON chunk does not parse: {exc}"]
    try:
        bin_len, bin_type = struct.unpack("<I4s", raw[20 + json_len:28 + json_len])
        if bin_type != b"BIN\0":
            problems.append("second chunk is not BIN")
        if doc.get("buffers") and doc["buffers"][0]["byteLength"] > bin_len:
            problems.append("buffer byteLength is longer than the BIN chunk")
    except struct.error:
        problems.append("no BIN chunk")
    views = doc.get("bufferViews", [])
    for i, accessor in enumerate(doc.get("accessors", [])):
        view = views[accessor["bufferView"]] if accessor["bufferView"] < len(views) else None
        if view is None:
            problems.append(f"accessor {i} points at a missing bufferView")
            continue
        width = 4 if accessor["componentType"] in (5125, 5126) else 2
        need = accessor["count"] * ARITY[accessor["type"]] * width
        if need > view["byteLength"]:
            problems.append(f"accessor {i} needs {need} bytes, its view has {view['byteLength']}")
    for i, image in enumerate(doc.get("images", [])):
        if "bufferView" in image:
            if image["bufferView"] >= len(views):
                problems.append(f"image {i} points at a missing bufferView")
        elif not image.get("uri"):
            problems.append(f"image {i} has neither a bufferView nor a uri")
    for i, texture in enumerate(doc.get("textures", [])):
        if texture.get("source", 0) >= len(doc.get("images", [])):
            problems.append(f"texture {i} points at a missing image")
    for a, animation in enumerate(doc.get("animations", [])):
        for c, channel in enumerate(animation.get("channels", [])):
            sampler = animation["samplers"][channel["sampler"]] if channel.get("sampler", 99) < len(animation.get("samplers", [])) else None
            if sampler is None:
                problems.append(f"animation {a} channel {c} points at a missing sampler")
                continue
            if sampler["input"] >= len(doc.get("accessors", [])):
                problems.append(f"animation {a} channel {c} has a missing input accessor")
            if sampler["output"] >= len(doc.get("accessors", [])):
                problems.append(f"animation {a} channel {c} has a missing output accessor")
            if channel.get("target", {}).get("node", 0) >= len(doc.get("nodes", [])):
                problems.append(f"animation {a} channel {c} targets a missing node")
        for s, sampler in enumerate(animation.get("samplers", [])):
            inputs = doc["accessors"][sampler["input"]]
            outputs = doc["accessors"][sampler["output"]]
            if inputs["count"] != outputs["count"]:
                problems.append(f"animation {a} sampler {s}: {inputs['count']} times, {outputs['count']} values")
            if "min" not in inputs or "max" not in inputs:
                problems.append(f"animation {a} sampler {s}: input accessor has no min/max")
    declared = doc.get("extensions", {}).get("KHR_materials_variants", {}).get("variants", [])
    if "KHR_materials_variants" in doc.get("extensionsUsed", []):
        if not declared:
            problems.append("KHR_materials_variants is used but declares no variants")
        for m, mesh in enumerate(doc.get("meshes", [])):
            for pr, primitive in enumerate(mesh.get("primitives", [])):
                mappings = primitive.get("extensions", {}).get("KHR_materials_variants", {}).get("mappings", [])
                seen = set()
                for mapping in mappings:
                    if mapping.get("material", 0) >= len(doc.get("materials", [])):
                        problems.append(f"mesh {m} primitive {pr} maps to a missing material")
                    for variant in mapping.get("variants", []):
                        if variant >= len(declared):
                            problems.append(f"mesh {m} primitive {pr} maps to variant {variant}, only {len(declared)} declared")
                        seen.add(variant)
                if mappings and len(seen) != len(declared):
                    problems.append(f"mesh {m} primitive {pr} covers {len(seen)} of {len(declared)} variants")
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
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(prim).ComputePurpose() in ("default", "render"):
                self._mesh(glb, prim)
            elif prim.IsA(UsdGeom.PointInstancer):
                self._instancer(glb, prim)
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Camera):
                self._camera(glb, prim)
                break
        for prim in stage.Traverse():
            if prim.GetTypeName().endswith("Light"):
                self._light(glb, prim)

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
    def _mesh(self, glb: _Glb, prim) -> None:
        mesh = UsdGeom.Mesh(prim)
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        points = mesh.GetPointsAttr().Get()
        if not counts or not indices or not points:
            return
        tris = triangulate(counts, indices)
        local = [Gf.Vec3d(p[0], p[1], p[2]) for p in points]
        positions = [[p[0], p[1], p[2]] for p in local]
        attributes = {"POSITION": glb.accessor(positions, "float", "VEC3", minmax=True),
                      "NORMAL": glb.accessor(_normals(local, tris), "float", "VEC3")}
        uv = _uvs(prim, tris, len(points))
        if uv:
            attributes["TEXCOORD_0"] = glb.accessor(uv, "float", "VEC2")
        bound = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
        primitive = {"attributes": attributes,
                     "indices": glb.accessor(tris, "uint", "SCALAR", target=34963),
                     "mode": 4, "material": glb.material(bound, prim.GetName())}
        mappings = _variant_mappings(glb, prim, getattr(self, "inv", None))
        if mappings:
            primitive["extensions"] = {"KHR_materials_variants": {"mappings": mappings}}
        glb.doc["meshes"].append({"name": prim.GetName(), "primitives": [primitive]})
        node = glb.node(name=str(prim.GetPath()), mesh=len(glb.doc["meshes"]) - 1,
                        **_node_fields(_trs(prim, stage_time(prim))))
        self._animate(glb, prim, node)

    def _instancer(self, glb: _Glb, prim) -> None:
        instancer = UsdGeom.PointInstancer(prim)
        positions = instancer.GetPositionsAttr().Get()
        prototypes = instancer.GetPrototypesRel().GetTargets()
        if not positions or not prototypes:
            return
        proto = prim.GetStage().GetPrimAtPath(prototypes[0])
        mesh = UsdGeom.Mesh(proto)
        points, counts, indices = (mesh.GetPointsAttr().Get(), mesh.GetFaceVertexCountsAttr().Get(),
                                   mesh.GetFaceVertexIndicesAttr().Get())
        if not points or not counts or not indices:
            return
        tris = triangulate(counts, indices)
        verts = [[p[0], p[1], p[2]] for p in points]
        attributes = {"POSITION": glb.accessor(verts, "float", "VEC3", minmax=True),
                      "NORMAL": glb.accessor(_normals([Gf.Vec3d(*v) for v in verts], tris),
                                             "float", "VEC3")}
        bound = UsdShade.MaterialBindingAPI(proto).ComputeBoundMaterial()[0]
        glb.doc["meshes"].append({"name": proto.GetName(), "primitives": [{
            "attributes": attributes, "indices": glb.accessor(tris, "uint", "SCALAR", target=34963),
            "mode": 4, "material": glb.material(bound, proto.GetName())}]})
        local = [[float(p[0]), float(p[1]), float(p[2])] for p in positions]
        if "EXT_mesh_gpu_instancing" not in glb.doc["extensionsUsed"]:
            glb.doc["extensionsUsed"].append("EXT_mesh_gpu_instancing")
        node = glb.node(name=str(prim.GetPath()), mesh=len(glb.doc["meshes"]) - 1,
                        **_node_fields(_trs(prim, stage_time(prim))),
                        extensions={"EXT_mesh_gpu_instancing": {
                            "attributes": {"TRANSLATION": glb.accessor(local, "float", "VEC3")}}})
        self._animate(glb, prim, node)

    def _animate(self, glb: _Glb, prim, node: int) -> None:
        """Bake a moving node's local-to-world as glTF animation channels.

        One sampler per channel (translation, rotation, scale), sampled at every timecode in the
        stage's range, in seconds. Interpolation is LINEAR: the source is a sampled transform,
        so a linear blend between frames is what USD itself does between samples.
        """
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

        animation = glb.doc["animations"][0] if glb.doc["animations"] else None
        if animation is None:
            glb.doc["animations"].append({"name": "take", "channels": [], "samplers": []})
            animation = glb.doc["animations"][0]
        input_accessor = None
        for path in ("translation", "rotation", "scale"):
            values = [sample[path] for sample in samples]
            if all(abs(v - w) <= 1e-9 for v in values[0] for w in values[0]) and \
               all(_same({path: values[0]}, {path: v}) for v in values):
                continue  # this channel never moves
            if input_accessor is None:
                input_accessor = glb.accessor(times, "float", "SCALAR", minmax=True)
            output = glb.accessor(values, "float", "VEC4" if path == "rotation" else "VEC3")
            animation["samplers"].append({"input": input_accessor, "output": output,
                                          "interpolation": "LINEAR"})
            animation["channels"].append({"sampler": len(animation["samplers"]) - 1,
                                          "target": {"node": node, "path": path}})

    def _camera(self, glb: _Glb, prim) -> None:
        camera = UsdGeom.Camera(prim)
        focal = camera.GetFocalLengthAttr().Get() or 35.0
        aperture = camera.GetVerticalApertureAttr().Get() or 20.25
        clip = camera.GetClippingRangeAttr().Get()
        glb.doc["cameras"].append({"type": "perspective", "perspective": {
            "yfov": 2 * math.atan(aperture / (2 * focal)),
            "znear": float(clip[0]) if clip else 0.05,
            "zfar": float(clip[1]) if clip else 1000.0}})
        glb.node(name=str(prim.GetPath()), camera=len(glb.doc["cameras"]) - 1)

    def _light(self, glb: _Glb, prim) -> None:
        kind = prim.GetTypeName()
        if kind not in ("SphereLight", "DistantLight", "CylinderLight"):
            return
        intensity = prim.GetAttribute("inputs:intensity")
        exposure = prim.GetAttribute("inputs:exposure")
        lumens = (intensity.Get() if intensity and intensity.HasAuthoredValue() else 1.0)
        stops = (exposure.Get() if exposure and exposure.HasAuthoredValue() else 0.0)
        colour = prim.GetAttribute("inputs:color")
        c = colour.Get() if colour and colour.HasAuthoredValue() else (1.0, 1.0, 1.0)
        lights = glb.doc.setdefault("extensions", {}).setdefault("KHR_lights_punctual", {"lights": []})
        lights["lights"].append({"type": "point" if kind != "DistantLight" else "directional",
                                 "color": [float(c[0]), float(c[1]), float(c[2])],
                                 "intensity": 4 * math.pi * float(lumens) * (2 ** float(stops))})
        if "KHR_lights_punctual" not in glb.doc["extensionsUsed"]:
            glb.doc["extensionsUsed"].append("KHR_lights_punctual")
        glb.node(name=str(prim.GetPath()),
                 extensions={"KHR_lights_punctual": {"light": len(lights["lights"]) - 1}})


def _settings_extras(settings) -> dict:
    """The seven /WebSettings values, as data for the player; glTF has nowhere else to put them."""
    if settings is None:
        return {}
    return {"web": {"title": settings.title, "camera": settings.camera,
                    "hudVariantSets": settings.hudVariantSets, "showProxy": settings.showProxy,
                    "backgroundBlur": settings.backgroundBlur,
                    "exposureOffset": settings.exposureOffset, "showHud": settings.showHud}}
