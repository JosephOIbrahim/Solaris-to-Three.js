"""Inventory: the facts about a stage, gathered once (blueprint 4.1).

USD supplies facts, code makes decisions (principle 2). This module only gathers. It does
not decide what a format can hold (profiles/ do) or whether a stage may go (report.py and
tools/web_census.py do). One traversal, gather(), feeds both the export's Inventory and the
web census, so the two cannot disagree about what is on the stage.

    inv = build_inventory(Usd.Stage.Open("shot.usda"))

The traversal is the census's: every active, loaded, defined prim, instance proxies
included. Pure pxr (principle 3): runs under usd-core and under hython. The animation checks
(animated_attributes, scale_animated) are separate short walks the glTF and X3D profiles call;
they sample no frames.
"""
from __future__ import annotations

import glob
import math
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from pxr import Ar, Sdf, Tf, Usd, UsdGeom, UsdLux, UsdShade, UsdVol

__all__ = [
    "Inventory", "MeshFact", "InstancerFact", "MaterialFact", "LightFact", "CameraFact",
    "VariantFact", "CompositionFact", "TextureFact", "StageFacts",
    "build_inventory", "gather", "is_absolute", "inside", "asset_location",
    "animated_attributes", "scale_animated",
]

MaterialKind = Literal["preview", "materialx", "other"]
VariantKind = Literal["geometry", "material", "mixed"]


# ---- facts (blueprint 4.1) ------------------------------------------------------------

@dataclass
class MeshFact:
    path: str
    points: int                       # point count; the first time sample when the points are animated
    has_uv: bool                      # a texCoord2f[] or float2[] primvar on the mesh itself
    subdiv_scheme: str                # resolved: an unauthored scheme reads USD's fallback, catmullClark
    purpose: str                      # computed, so inherited: default, render, proxy or guide
    material_path: str | None = None  # the material bound to the mesh itself; None when unbound


@dataclass
class InstancerFact:
    path: str
    prototype_paths: list[str]
    count: int                        # instances: entries in protoIndices


@dataclass
class MaterialFact:
    path: str
    kind: MaterialKind                # materialx: a MaterialX surface; preview: UsdPreviewSurface; other: the rest
    shader_id: str | None             # info:id of the surface shader; None when nothing is connected
    textures: list[str] = field(default_factory=list)  # asset paths its network reads, as authored


@dataclass
class LightFact:
    path: str
    type: str                         # prim type name: RectLight, DomeLight_1, ...
    intensity: float | None = None
    exposure: float | None = None
    color: tuple[float, float, float] | None = None
    texture: str | None = None        # inputs:texture:file as authored (dome, rect)


@dataclass
class CameraFact:
    path: str
    projection: str = "perspective"
    focal_length: float | None = None
    horizontal_aperture: float | None = None
    vertical_aperture: float | None = None
    clipping_range: tuple[float, float] | None = None


@dataclass
class VariantFact:
    prim_path: str
    set_name: str
    options: list[str]
    kind: VariantKind                 # from the prim specs each variant authors; see _variant_kind()


@dataclass
class CompositionFact:
    references: list[tuple[str, str]] = field(default_factory=list)  # (prim path, asset path as authored; "" = internal)
    payloads: list[tuple[str, str]] = field(default_factory=list)    # (prim path, asset path as authored)
    sublayers: list[str] = field(default_factory=list)               # as authored, from every layer the stage uses
    errors: list[str] = field(default_factory=list)                  # composition errors, in USD's words


@dataclass
class TextureFact:
    path: str          # as authored
    ext: str           # lower case, with the dot
    exists: bool       # on disk; for a <UDIM> pattern, at least one tile is
    is_udim: bool
    is_absolute: bool  # a drive, UNC or rooted path: nothing a web folder can carry


@dataclass
class Inventory:
    root_layer: str
    up_axis: str
    meters_per_unit: float
    time_range: tuple[float, float] | None
    meshes: list[MeshFact] = field(default_factory=list)
    instancers: list[InstancerFact] = field(default_factory=list)
    materials: list[MaterialFact] = field(default_factory=list)
    lights: list[LightFact] = field(default_factory=list)
    cameras: list[CameraFact] = field(default_factory=list)
    variant_sets: list[VariantFact] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    composition: CompositionFact = field(default_factory=CompositionFact)
    textures: list[TextureFact] = field(default_factory=list)


@dataclass
class StageFacts:
    """Everything gather() learns in one traversal.

    inventory is the export's view (blueprint 4.1). The other fields are the web census's own
    facts, in the shapes tools/web_census.py has always returned; its census() only renames them.
    """
    inventory: Inventory
    prims: int = 0
    shaders: dict = field(default_factory=dict)         # info:id -> {"count", "example", "inputs"}
    asset_rows: list = field(default_factory=list)      # each asset path on a shader or light: path, resolved, prim, dome, ext
    lights_by_type: dict = field(default_factory=dict)  # prim type -> [prim paths]
    tracked: dict = field(default_factory=dict)         # prim type -> [prim paths], for the types gather() was asked to track
    purposes: dict = field(default_factory=dict)        # "proxy", "guide" -> imageable prims with that purpose
    no_uv: list = field(default_factory=list)           # default/render meshes bound to MaterialX, with no UV primvar
    unbound: list = field(default_factory=list)         # default/render meshes with no material on the mesh or a subset
    layers: dict = field(default_factory=dict)          # file extension -> [layer identifiers]
    outside: list = field(default_factory=list)         # files outside the root layer's folder, sorted, once each
    errors: list = field(default_factory=list)          # composition errors


# ---- paths ----------------------------------------------------------------------------

def is_absolute(p):
    return os.path.isabs(p) or bool(re.match(r"^[A-Za-z]:[\\/]", p)) or p.startswith(("\\\\", "/"))


def _norm(p):
    return os.path.normcase(os.path.realpath(p))


def inside(p, folder):
    """True if path p sits in folder or below it (folder already normalised)."""
    p = _norm(p)
    return p == folder or p.startswith(folder.rstrip(os.sep) + os.sep)


def asset_location(attr, ap):
    """Where an asset path points on disk: USD's resolved path, or, when it doesn't resolve
    (a missing file or a <UDIM> pattern), the path anchored to the layer that authored it."""
    if ap.resolvedPath:
        return ap.resolvedPath
    for spec in attr.GetPropertyStack(Usd.TimeCode.Default()):
        if spec.layer and not spec.layer.anonymous and spec.layer.realPath:
            where = spec.layer.ComputeAbsolutePath(ap.path)
            # A bare "tex/a.png" is a USD search path and comes back unanchored; anchor it to the layer.
            if not os.path.isabs(where):
                where = os.path.join(os.path.dirname(spec.layer.realPath), where)
            return where
    return None


_UDIM = re.compile(r"<udim>", re.IGNORECASE)


def _udim_tiles_exist(pattern):
    """True when at least one tile of a <UDIM> pattern (1001 and up) is on disk."""
    return bool(glob.glob("[1-9][0-9][0-9][0-9]".join(glob.escape(p) for p in _UDIM.split(pattern))))


# ---- values ---------------------------------------------------------------------------

def _value(attr):
    """An attribute's value: its first time sample when animated, else its default or fallback."""
    if not attr:
        return None
    if attr.GetNumTimeSamples():
        return attr.Get(Usd.TimeCode.EarliestTime())
    return attr.Get()


def _float(v):
    return None if v is None else float(v)


def _count(attr):
    v = _value(attr)
    return 0 if v is None else len(v)


def _light_fact(prim, path, type_name):
    color = _value(prim.GetAttribute("inputs:color"))
    tex = _value(prim.GetAttribute("inputs:texture:file"))
    return LightFact(path, type_name,
                     intensity=_float(_value(prim.GetAttribute("inputs:intensity"))),
                     exposure=_float(_value(prim.GetAttribute("inputs:exposure"))),
                     color=None if color is None else tuple(float(c) for c in color),
                     texture=(tex.path or None) if isinstance(tex, Sdf.AssetPath) else None)


def _camera_fact(prim, path):
    cam = UsdGeom.Camera(prim)
    clip = _value(cam.GetClippingRangeAttr())
    return CameraFact(path,
                      projection=str(_value(cam.GetProjectionAttr()) or "perspective"),
                      focal_length=_float(_value(cam.GetFocalLengthAttr())),
                      horizontal_aperture=_float(_value(cam.GetHorizontalApertureAttr())),
                      vertical_aperture=_float(_value(cam.GetVerticalApertureAttr())),
                      clipping_range=None if clip is None else (float(clip[0]), float(clip[1])))


# ---- materials ------------------------------------------------------------------------

def _surface_source(mat, context):
    """The shader a material's surface output reaches for one render context ("" = universal)."""
    try:
        return mat.ComputeSurfaceSource(context)[0]
    except TypeError:  # USD builds whose binding takes a list of contexts
        return mat.ComputeSurfaceSource([context])[0]


def _shader_id(shader):
    return (shader.GetShaderId() or None) if shader else None


def _surface(mat):
    """(kind, info:id of the surface shader). A mtlx surface output makes it MaterialX, as the
    census reads it. The mtlx context falls back to the universal output, so it never decides alone."""
    if mat.GetSurfaceOutput("mtlx"):
        return "materialx", _shader_id(_surface_source(mat, "mtlx"))
    sid = _shader_id(_surface_source(mat, ""))
    if sid == "UsdPreviewSurface":
        return "preview", sid
    if sid and sid.startswith("ND_"):  # a MaterialX node wired to the universal output
        return "materialx", sid
    for out in mat.GetSurfaceOutputs():  # renderer outputs, such as outputs:kma:surface or outputs:ri:surface
        context = out.GetBaseName().rpartition(":")[0]
        if context and context != "mtlx":
            other = _shader_id(_surface_source(mat, context))
            if other:
                return "other", other
    return "other", sid


def _network(mat_prim):
    """Prim paths in a material's network: everything under the material, and every shader or
    node graph its connections reach, wherever it lives."""
    under = list(Usd.PrimRange(mat_prim, Usd.TraverseInstanceProxies()))
    seen = {p.GetPath() for p in under}
    order = [str(p.GetPath()) for p in under]
    stack = [p for p in under if p.IsA(UsdShade.NodeGraph) or p.IsA(UsdShade.Shader)]
    while stack:
        api = UsdShade.ConnectableAPI(stack.pop())
        for port in list(api.GetInputs()) + list(api.GetOutputs()):
            for source in UsdShade.ConnectableAPI.GetConnectedSources(port)[0]:
                q = source.source.GetPrim()
                if q and q.GetPath() not in seen:
                    seen.add(q.GetPath())
                    order.append(str(q.GetPath()))
                    if q.IsA(UsdShade.NodeGraph) or q.IsA(UsdShade.Shader):
                        stack.append(q)
    return order


# ---- variant sets ---------------------------------------------------------------------
#
# A variant set's kind comes from what its variants author, read from the layers, so variants
# that aren't selected count too. Each prim spec inside a variant that authors something (a
# property, a type, an arc, any metadata) touches one side:
#   material  a Material, NodeGraph or Shader (or a prim inside a Material), or a material
#             binding on any prim: a variant that only rebinds meshes is a look, the case
#             glTF's KHR_materials_variants exists for
#   geometry  any other imageable prim: meshes, xforms, scopes, instancers, volumes
#   other     lights, cameras, untyped prims: neither, so they make a set "mixed"
# Specs that only hold children (a bare `over "mtl"`) author nothing and are skipped.

_TF_SHADING = (Tf.Type.Find(UsdShade.NodeGraph), Tf.Type.Find(UsdShade.Shader))  # Material is a NodeGraph
_TF_IMAGEABLE = Tf.Type.Find(UsdGeom.Imageable)
_TF_CAMERA = Tf.Type.Find(UsdGeom.Camera)


def _is_binding(name):
    return name == "material:binding" or name.startswith("material:binding:")


def _binding_api_only(spec):
    """True when the spec's only metadata applies MaterialBindingAPI (what a rebind authors)."""
    op = spec.GetInfo("apiSchemas")
    items = [i for kind in ("explicitItems", "addedItems", "prependedItems", "appendedItems", "deletedItems")
             for i in (getattr(op, kind, None) or [])]
    return bool(items) and all(str(i).startswith("MaterialBindingAPI") for i in items)


def _prim_side(prim):
    if prim.IsA(UsdShade.NodeGraph) or prim.IsA(UsdShade.Shader):
        return "material"
    p = prim.GetParent()
    while p and not p.IsPseudoRoot():
        if p.IsA(UsdShade.Material):
            return "material"
        p = p.GetParent()
    if prim.HasAPI(UsdLux.LightAPI) or prim.IsA(UsdGeom.Camera):
        return "other"
    return "geometry" if prim.IsA(UsdGeom.Imageable) else "other"


def _type_side(type_name):
    t = Usd.SchemaRegistry.GetTypeFromSchemaTypeName(type_name) if type_name else Tf.Type.Unknown
    if t == Tf.Type.Unknown:
        return "other"
    if any(t.IsA(s) for s in _TF_SHADING):
        return "material"
    defn = Usd.SchemaRegistry().FindConcretePrimDefinition(type_name)
    if t.IsA(_TF_CAMERA) or (defn and "LightAPI" in defn.GetAppliedAPISchemas()):
        return "other"
    return "geometry" if t.IsA(_TF_IMAGEABLE) else "other"


def _spec_side(spec):
    """For a prim only an unselected variant defines, so it isn't on the stage: go by the types
    the layer gives it and its parents."""
    s = spec
    while s:
        if s.typeName:
            side = _type_side(s.typeName)
            if s is spec or side == "material":  # a parent's type only says "inside a Material"
                return side
        parent = s.path.GetParentPath()
        if parent.isEmpty or parent == Sdf.Path.absoluteRootPath:
            break
        s = s.layer.GetPrimAtPath(parent)
    return "other"


def _specs_under(spec):
    """A prim spec, its namespace children, and the variants nested anywhere in them."""
    stack = [spec]
    while stack:
        s = stack.pop()
        yield s
        stack.extend(s.nameChildren.values())
        for vset in s.variantSets.values():
            stack.extend(v.primSpec for v in vset.variants.values())


def _sides(stage, owner, base, spec):
    """The sides one prim spec inside a variant touches; empty when it authors nothing."""
    meta = set(spec.ListInfoKeys()) - {"specifier"}  # a typed def lists typeName; a bare container lists nothing
    binding_meta = meta == {"apiSchemas"} and _binding_api_only(spec)
    names = [p.name for p in spec.properties]
    out = {"material"} if binding_meta or any(_is_binding(n) for n in names) else set()
    if (meta and not binding_meta) or not all(_is_binding(n) for n in names):
        here = spec.path.StripAllVariantSelections()
        target = owner if here == base else owner.AppendPath(here.MakeRelativePath(base))
        prim = stage.GetPrimAtPath(target)
        out.add(_prim_side(prim) if prim else _spec_side(spec))
    return out


def _variant_kind(prim, set_name):
    sides = set()
    stage, owner = prim.GetStage(), prim.GetPath()
    for spec in prim.GetPrimStack():
        if set_name not in spec.variantSets.keys():
            continue
        base = spec.path.StripAllVariantSelections()
        for variant in spec.variantSets[set_name].variants.values():
            for sub in _specs_under(variant.primSpec):
                sides |= _sides(stage, owner, base, sub)
    if sides == {"material"}:
        return "material"
    if sides == {"geometry"}:
        return "geometry"
    return "mixed"  # both, lights or cameras, or nothing authored at all


# ---- composition ----------------------------------------------------------------------

def _arcs(prim, path, references, payloads, seen):
    for spec in prim.GetPrimStack():
        for kind, items, out in (("r", spec.referenceList, references), ("p", spec.payloadList, payloads)):
            for arc in items.GetAddedOrExplicitItems():
                key = (kind, path, arc.assetPath)
                if key not in seen:
                    seen.add(key)
                    out.append((path, arc.assetPath))


# ---- the one traversal ----------------------------------------------------------------

def gather(stage: Usd.Stage, track_types: Iterable[str] = ()) -> StageFacts:
    """Read the stage once; return the Inventory and the census's facts together.

    track_types: prim type names whose paths go into StageFacts.tracked. The census asks for
    the types its viewer has never been tested with.
    """
    track_types = frozenset(track_types)
    root = stage.GetRootLayer()
    inv = Inventory(
        root_layer=root.identifier,
        up_axis=str(UsdGeom.GetStageUpAxis(stage)),
        meters_per_unit=float(UsdGeom.GetStageMetersPerUnit(stage)),
        time_range=((float(stage.GetStartTimeCode()), float(stage.GetEndTimeCode()))
                    if stage.HasAuthoredTimeCodeRange() else None),
    )
    comp = inv.composition

    # The export folder is the root layer's folder. The server can only reach files under it,
    # so a layer or texture outside it (a "../" path) loads in Houdini and 404s in the browser.
    # An in-memory stage has no folder, so nothing is outside it.
    root_dir = _norm(os.path.dirname(root.realPath)) if root.realPath else None
    outside = []
    shaders, rows, lights, tracked = {}, [], {}, {}
    no_uv, unbound, purposes, prims = [], [], {"proxy": 0, "guide": 0}, 0
    assets_by_prim, textures, material_prims, arcs_seen = {}, {}, [], set()

    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        prims += 1
        path = str(prim.GetPath())
        t = prim.GetTypeName()
        if t in track_types:
            tracked.setdefault(t, []).append(path)
        purpose = None
        if prim.IsA(UsdGeom.Imageable):
            purpose = UsdGeom.Imageable(prim).ComputePurpose()
            if purpose in purposes:
                purposes[purpose] += 1
        if prim.IsA(UsdShade.Shader):
            sh = UsdShade.Shader(prim)
            sid = sh.GetShaderId() or ("sourceAsset" if sh.GetImplementationSource() == "sourceAsset" else "(none)")
            e = shaders.setdefault(sid, {"count": 0, "example": path,
                                         "inputs": sorted(i.GetBaseName() for i in sh.GetInputs())[:24]})
            e["count"] += 1
        is_light = prim.HasAPI(UsdLux.LightAPI)
        if is_light or t.endswith("Light"):
            lights.setdefault(t, []).append(path)
            inv.lights.append(_light_fact(prim, path, t))
        if prim.IsA(UsdShade.Shader) or is_light:
            for a in prim.GetAttributes():
                if a.GetTypeName() not in (Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray):
                    continue
                v = a.Get()
                for ap in ([v] if isinstance(v, Sdf.AssetPath) else list(v or [])):
                    if isinstance(ap, Sdf.AssetPath) and ap.path:
                        rows.append({"path": ap.path, "resolved": bool(ap.resolvedPath), "prim": path,
                                     "dome": t.startswith("DomeLight"), "ext": os.path.splitext(ap.path)[1].lower()})
                        where = None
                        # Absolute and $VAR paths have their own fix-first line; don't count them twice.
                        if not is_absolute(ap.path) and "$" not in ap.path:
                            where = asset_location(a, ap)
                            if root_dir and where and not inside(where, root_dir):
                                outside.append(ap.path)
                        assets_by_prim.setdefault(path, []).append(ap.path)
                        # One TextureFact per file: the same relative path from two folders is two files.
                        key = (ap.path, where or ap.resolvedPath)
                        if key not in textures:
                            udim = "<udim>" in ap.path.lower()
                            exists = (_udim_tiles_exist(where or asset_location(a, ap) or ap.path) if udim
                                      else bool(ap.resolvedPath))
                            textures[key] = TextureFact(ap.path, os.path.splitext(ap.path)[1].lower(), exists,
                                                        udim, is_absolute(ap.path))
        if prim.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(prim)
            bound = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
            pv = UsdGeom.PrimvarsAPI(prim).GetPrimvars()
            has_uv = any(p.GetTypeName() in (Sdf.ValueTypeNames.TexCoord2fArray, Sdf.ValueTypeNames.Float2Array)
                         for p in pv)
            inv.meshes.append(MeshFact(path, _count(mesh.GetPointsAttr()), has_uv,
                                       str(_value(mesh.GetSubdivisionSchemeAttr())), purpose,
                                       str(bound.GetPath()) if bound else None))
            if purpose in ("default", "render"):
                mats = [bound]
                mats += [UsdShade.MaterialBindingAPI(s.GetPrim()).ComputeBoundMaterial()[0]
                         for s in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(prim))]
                mats = [m for m in mats if m]
                if not mats:
                    unbound.append(path)
                elif any(m.GetSurfaceOutput("mtlx") for m in mats):
                    if not has_uv:
                        no_uv.append(path)
        if prim.IsA(UsdGeom.PointInstancer):
            pi = UsdGeom.PointInstancer(prim)
            inv.instancers.append(InstancerFact(path, [str(p) for p in pi.GetPrototypesRel().GetTargets()],
                                                _count(pi.GetProtoIndicesAttr())))
        if prim.IsA(UsdGeom.Camera):
            inv.cameras.append(_camera_fact(prim, path))
        if prim.IsA(UsdVol.Volume):
            inv.volumes.append(path)
        if prim.IsA(UsdShade.Material):
            material_prims.append(prim)
        if prim.HasVariantSets():
            for name in prim.GetVariantSets().GetNames():
                inv.variant_sets.append(VariantFact(path, name, list(prim.GetVariantSet(name).GetVariantNames()),
                                                    _variant_kind(prim, name)))
        if prim.HasAuthoredReferences() or prim.HasAuthoredPayloads():
            _arcs(prim, path, comp.references, comp.payloads, arcs_seen)

    for mp in material_prims:
        kind, sid = _surface(UsdShade.Material(mp))
        texs = []
        for p in _network(mp):
            for x in assets_by_prim.get(p, ()):
                if x not in texs:
                    texs.append(x)
        inv.materials.append(MaterialFact(str(mp.GetPath()), kind, sid, texs))
    inv.textures = list(textures.values())

    layers = {}
    for layer in stage.GetUsedLayers():
        if layer.anonymous:
            continue
        identifier = layer.identifier.split(":SDF_FORMAT_ARGS:")[0]
        # A layer inside a package (.usdz[inner.usda]) takes the inner layer's extension; the
        # outer package's ".usdz" is not the format the browser has to read.
        if Ar.IsPackageRelativePath(identifier):
            identifier = Ar.SplitPackageRelativePathOuter(identifier)[1]
        ext = os.path.splitext(identifier)[1].lower()
        layers.setdefault(ext, []).append(layer.identifier)
        if root_dir and layer.realPath and not inside(layer.realPath, root_dir):
            try:  # shown as written on disk (realpath, not normcase, which lowercases on Windows)
                outside.append(os.path.relpath(os.path.realpath(layer.realPath),
                                               os.path.realpath(os.path.dirname(stage.GetRootLayer().realPath)))
                               .replace(os.sep, "/"))
            except ValueError:  # another drive on Windows: no relative path exists
                outside.append(layer.realPath)
    for layer in sorted((l for l in stage.GetUsedLayers() if not l.anonymous), key=lambda l: l.identifier):
        comp.sublayers += [p for p in layer.subLayerPaths if p not in comp.sublayers]
    errors = [str(e) for e in getattr(stage, "GetCompositionErrors", lambda: [])()]
    comp.errors = list(errors)

    return StageFacts(inventory=inv, prims=prims, shaders=shaders, asset_rows=rows, lights_by_type=lights,
                      tracked=tracked, purposes=purposes, no_uv=no_uv, unbound=unbound, layers=layers,
                      outside=sorted(set(outside)), errors=errors)


# ---- animation the writers do not bake --------------------------------------------------
#
# The glTF and X3D writers bake a prim's local-to-world transform only. A time-varying
# attribute outside that transform (mesh points, a shader input, visibility) is written at its
# current time sample, so both profiles report it. ValueMightBeTimeVarying() is USD's own cheap
# check: no frame is sampled, and it is conservative (true for time samples or a value clip).

NON_TRANSFORM_ATTRIBUTES = ("points", "positions", "visibility")


def _animated_attribute(prim) -> bool:
    for name in NON_TRANSFORM_ATTRIBUTES:
        attr = prim.GetAttribute(name)
        if attr and attr.ValueMightBeTimeVarying():
            return True
    if prim.IsA(UsdShade.Shader):
        return any(a.GetName().startswith("inputs:") and a.ValueMightBeTimeVarying()
                   for a in prim.GetAttributes())
    return False


def animated_attributes(stage: Usd.Stage) -> list[str]:
    """Prim paths with a time-varying attribute that the writers do not bake (not a transform).

    One walk of the composed stage, no per-frame work: ValueMightBeTimeVarying() is the check.
    """
    found, seen = [], set()
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        path = str(prim.GetPath())
        if path not in seen and _animated_attribute(prim):
            seen.add(path)
            found.append(path)
    return found


def _world_scale(prim, time: float) -> tuple[float, float, float]:
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode(time))
    return tuple(math.sqrt(sum(matrix.GetColumn(i)[row] ** 2 for row in range(3)))
                 for i in range(3))


def scale_animated(stage: Usd.Stage) -> list[str]:
    """Prim paths whose local-to-world scale differs across the stage's range.

    The writers' own start/middle/end probes, applied to the scale column lengths (the same
    extraction the X3D writer uses). This is the scale change X3D has no interpolator for: it
    would be left at the start pose, so the profile names it rather than dropping it silently.
    """
    start, end = stage.GetStartTimeCode(), stage.GetEndTimeCode()
    if start is None or end is None or end <= start:
        return []
    times = (start, (start + end) / 2.0, end)
    found, seen = [], set()
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Xformable):
            continue
        path = str(prim.GetPath())
        if path in seen:
            continue
        scales = [_world_scale(prim, time) for time in times]
        if any(abs(scales[0][axis] - scales[2][axis]) > 1e-6
               or abs(scales[1][axis] - scales[2][axis]) > 1e-6 for axis in range(3)):
            seen.add(path)
            found.append(path)
    return found


def build_inventory(stage: Usd.Stage) -> Inventory:
    """The facts a format profile needs (blueprint 4.1), from one traversal of the stage."""
    return gather(stage).inventory
