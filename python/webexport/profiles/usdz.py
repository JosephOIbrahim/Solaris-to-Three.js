"""USDZ profile: what a stage keeps and loses on its way into one .usdz archive.

Blueprint 4.2, 4.6 (the usdz column) and the v0.5 line, task U1. A .usdz is a USD stage in a
zip, so almost everything survives: subdivision, instancing, variant sets, references and
payloads, cameras, UsdPreviewSurface, and every light prim travel as authored. The honest
losses are what Apple's Quick Look (the common previewer) does not read, not what the archive
cannot hold:

    blocks    the same packaging blocks as the USD folder: a composition error, a layer format
              USD cannot read, a .rat or .tx texture, a UDIM set, a missing file, a $VAR path,
              an unsaved layer, a root layer that cannot take /WebSettings
    changes   MaterialX networks (Quick Look reads little: the UsdPreviewSurface if one is
              bound, else a flat colour), area and environment lights (kept, but Quick Look
              does not light with them), volumes (kept, not drawn), a non-surface shader
    note      everything that survives whole: packed references and payloads, variant sets,
              instancing, subdivision, UsdPreviewSurface, cameras, punctual lights, packed
              textures; and that the viewer's browser route cannot open a single .usdz

The blocks are read from UsdFolderProfile, which owns the census mapping and the writer's
plan, so the archive's package rules are stated once. The viewer-only findings that profile
also carries (how the browser draws lights, proxies, shaders) are dropped here: they describe
the browser, not the archive.
"""
from __future__ import annotations

from pxr import Usd

from .. import textures
from ..inventory import Inventory
from ..report import LossItem, LossReport
from ..writers.usd_folder import FORMAT as FOLDER_FORMAT
from ..writers.usd_folder import FolderPlan
from .usd_folder import UsdFolderProfile

FORMAT = "usdz"
PACKED_PATHS = "made relative and packed into the .usdz"

# Findings from the USD folder profile that describe the browser viewer, not the archive. A
# USDZ keeps the data; the Quick Look limits are reported below in the archive's own words.
BROWSER_ONLY = {
    "materialx_no_uv", "dome_texture", "unbound_mesh", "approximated_light", "ignored_light",
    "untested_prim_type", "renderer_only_shader", "unrecognised_shader", "proxy_guide_prims",
    "untested_materialx_node", "rect_light", "disk_light", "dome_light",
}
# Lights Quick Look does not light with. The prim and its values are in the archive all the same.
QUICKLOOK_IGNORES = {"RectLight", "DiskLight", "DomeLight", "DomeLight_1", "GeometryLight", "MeshLight"}
PUNCTUAL = {"SphereLight", "DistantLight", "CylinderLight"}


def _item(severity, feature, paths, becomes, fix=None) -> LossItem:
    paths = [p for p in paths if p]
    return LossItem(severity, feature, len(paths), paths[:3], becomes, fix)


def _kept(inv: Inventory) -> list[LossItem]:
    """One note per thing the archive carries whole (blueprint 4.6, usdz column)."""
    items = []
    comp = inv.composition
    arcs = [p for p, _ in comp.references] + [p for p, _ in comp.payloads]
    if arcs:
        items.append(_item("note", "composition_packed", arcs,
                           "packed into the archive; the references and payloads survive"))
    variants = [f"{v.prim_path}:{v.set_name}" for v in inv.variant_sets]
    if variants:
        items.append(_item("note", "variant_set", variants,
                           "kept: every option of every variant set is in the archive"))
    if inv.instancers:
        items.append(_item("note", "instancing", [i.path for i in inv.instancers],
                           "kept: the PointInstancer travels in the archive"))
    subd = [m.path for m in inv.meshes if m.subdiv_scheme not in ("none", "")]
    if subd:
        items.append(_item("note", "subdivision", subd,
                           "kept: the subdivision scheme travels in the archive"))
    preview = [m.path for m in inv.materials if m.kind == "preview"]
    if preview:
        items.append(_item("note", "preview_surface", preview, "kept: UsdPreviewSurface is read as authored"))
    if inv.cameras:
        items.append(_item("note", "camera", [c.path for c in inv.cameras], "kept"))
    lights = [l.path for l in inv.lights if l.type in PUNCTUAL]
    if lights:
        items.append(_item("note", "punctual_light", lights, "kept"))
    packed = [t.path for t in inv.textures if t.exists and not t.is_udim and textures.flag(t.path) is None]
    if packed:
        items.append(_item("note", "texture_packed", packed, "packed into the archive"))
    return items


def _quicklook(inv: Inventory) -> list[LossItem]:
    """What Quick Look does not read, though the archive holds it."""
    items = []
    materialx = [m.path for m in inv.materials if m.kind == "materialx"]
    if materialx:
        items.append(_item("changes", "materialx", materialx,
                           "kept, but Quick Look reads little of a MaterialX network: "
                           "the UsdPreviewSurface if one is bound, else a flat colour",
                           "bind a UsdPreviewSurface for Quick Look, or accept the approximation"))
    other = [m.path for m in inv.materials if m.kind == "other"]
    if other:
        items.append(_item("changes", "shader_not_read", other,
                           "kept, but Quick Look draws UsdPreviewSurface only; this falls back "
                           "to the default surface",
                           "bind a UsdPreviewSurface for Quick Look"))
    ignored = [f"{l.type} {l.path}" for l in inv.lights if l.type in QUICKLOOK_IGNORES]
    if ignored:
        items.append(_item("changes", "light_not_read", ignored,
                           "kept in the archive; Quick Look does not light with area or "
                           "environment lights",
                           "bake its effect into the stage if Quick Look matters"))
    if inv.volumes:
        items.append(_item("changes", "volume", inv.volumes,
                           "kept in the archive; Quick Look does not draw volumes"))
    return items


class UsdzProfile:
    """What a stage keeps, changes and loses in a .usdz (blueprint 4.2).

    Pass the live stage, the StageFacts already gathered and (optionally) the writer's plan;
    without them assess() opens inv.root_layer itself. After assess(), ``plan``,
    ``folder_report`` (the package rules for the writer inside this one), ``census``,
    ``decision`` and ``jev_note`` hold what it used.
    """
    name = FORMAT
    extensions = (".usdz",)

    def __init__(self, stage: Usd.Stage | None = None, facts=None,
                 plan: FolderPlan | None = None, jev_on: bool = True) -> None:
        self.stage, self.facts, self.plan, self.jev_on = stage, facts, plan, jev_on
        self.census: dict | None = None
        self.decision: tuple | None = None
        self.folder_report: LossReport | None = None  # the package report the USD folder writer is given
        self.jev_note = ""

    def assess(self, inv: Inventory) -> list[LossItem]:
        # BROWSER_ONLY drops all shader/Jev findings, so never spend a redundant Jev call for USDZ.
        folder = UsdFolderProfile(stage=self.stage, facts=self.facts, plan=self.plan, jev_on=False)
        folder_items = folder.assess(inv)
        self.plan = folder.plan
        self.census, self.decision, self.jev_note = folder.census, folder.decision, ""

        kept = []
        for item in folder_items:
            if item.feature in BROWSER_ONLY:
                continue
            if item.severity == "note" and item.feature in ("absolute_path", "outside_file"):
                # The USD folder profile says "copied in"; a .usdz packs the same files.
                item = LossItem("note", item.feature, item.count, list(item.examples),
                                PACKED_PATHS, item.fix)
            kept.append(item)

        self.folder_report = LossReport.from_items(FOLDER_FORMAT, kept, jev_note=self.jev_note)
        items = kept + _quicklook(inv) + _kept(inv)
        items.append(LossItem("note", "preview", 1, [],
                              "a .usdz is one archive; the viewer's browser route cannot open it",
                              "preview it in Quick Look or a USD app, or export the USD folder"))
        order = {"blocks": 0, "changes": 1, "note": 2}
        return sorted(items, key=lambda i: order[i.severity])
