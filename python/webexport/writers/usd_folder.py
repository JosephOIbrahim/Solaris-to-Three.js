"""USD folder writer: the stage as one self-contained folder the browser viewer can open.

Blueprint 4.4, mission M2. The folder holds the root layer, every sublayer, reference and
payload layer the stage uses (unselected variants included) and every file they point at,
each copied in, with every asset path rewritten to a relative "./" or "../" path that stays
inside the folder. Then /WebSettings is authored into the output root layer.

Layout. Files under the root layer's folder keep their place. Files outside it go under one
subfolder, external/, keeping their layout below the folder they share. With files on two
drives it is external/<drive>/... A source folder that already has its own external/ pushes
the outside files to external_2/.

Two steps, so the LossReport exists before anything is written (principle 1):

    plan = plan_folder(stage.GetRootLayer())    # reads only; the profile reports from it
    UsdFolderWriter(report=report, plan=plan, root=stage.GetRootLayer().identifier).write(
        stage, inv, out_dir, settings)

Built on UsdUtils.ComputeAllDependencies, file copies and UsdUtils.ModifyAssetPaths on the
copies, never UsdUtils.LocalizeAsset: on usd-core 26.5 that wrote a dangling 0/wood.png for a
missing texture, and search-path style paths such as 1/wood.rat that desktop USD finds by
searching and a browser, anchoring to the layer's URL, does not.
"""
from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from pxr import Ar, Sdf, Usd, UsdUtils

from .. import textures
from ..inventory import _norm, inside, is_absolute
from ..report import LossItem, LossReport
from ..settings import SETTINGS_PATH, read_settings, write_settings
from ..validate import validate_folder, web_census
from .base import ExportResult

FORMAT = "usd_folder"
OUTSIDE = "external"                     # the subfolder for files from outside the root layer's folder
USD_LAYERS = textures.USD_LAYERS         # layers whose asset paths are rewritten; a .usdz travels as it is
_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")  # http:, omniverse:, file: ... (a drive letter is one character)

# What a Problem's kind means. Each one is a "blocks" item in the report.
PROBLEMS = {
    "unsaved": "an in-memory layer, or one with unsaved edits",
    "root_format": "a root layer the writer can't edit, such as a .usdz, so /WebSettings has nowhere to go",
    "layer_format": "a layer format the browser's USD can't read",
    "missing": "a path that resolves to no file",
    "absolute": "an absolute path that resolves to no file",
    "var": "a $VAR path, which the export does not expand",
    "udim": "a UDIM pattern",
    "texture_format": "a .rat or .tx texture",
    "uri": "a URI, which the export does not fetch",
    "package": "a path into a package, which the export does not unpack",
}


@dataclass
class Placed:
    """One file to copy into the export folder."""
    source: str     # the real path on disk
    dest: str       # where it goes, relative to the export folder, forward slashes
    rewrite: bool   # a USD layer: its asset paths are rewritten after the copy


@dataclass
class Problem:
    """Something the writer can't place in the folder."""
    kind: str       # a key of PROBLEMS
    path: str       # as authored; a layer's identifier for "unsaved" and "layer_format"
    where: str      # where it points, anchored to its layer, when known
    layer: str      # identifier of the layer that authors it


@dataclass
class FolderPlan:
    """What the writer will do, worked out without writing anything."""
    root: str = ""                                                    # the root layer's file name in the folder
    files: list[Placed] = field(default_factory=list)                 # the root first
    rewrites: dict[str, dict[str, str]] = field(default_factory=dict)  # source layer key -> {authored: new path}
    copied_in: dict[str, dict[str, str]] = field(default_factory=dict)  # "absolute" | "outside" -> {source: authored}
    problems: list[Problem] = field(default_factory=list)
    outside: str = OUTSIDE                                            # the subfolder name actually used


def _relative(target: str, base: str) -> str:
    """target as seen from folder base (both inside the export folder), always "./" or "../"."""
    rel = posixpath.relpath(target, base or ".")
    return rel if rel.startswith("../") else "./" + rel


def _drive_label(drive: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", drive).strip("_") or "root"


def plan_folder(root: Sdf.Layer, resolver_context=None) -> FolderPlan:
    """Work out the folder: every file, where it goes, every path rewrite, and what can't be placed."""
    plan = FolderPlan()
    if root.anonymous or not root.realPath:
        plan.problems.append(Problem("unsaved", root.identifier, "", root.identifier))
        return plan
    root_real = os.path.realpath(root.realPath)
    root_dir = os.path.dirname(root_real)
    folder = _norm(root_dir)
    plan.root = os.path.basename(root_real)
    root_key = _norm(root_real)
    if os.path.splitext(root_real)[1].lower() not in USD_LAYERS:
        plan.problems.append(Problem("root_format", root.identifier, root_real, root.identifier))

    sources: dict[str, list] = {}  # key -> [real path, rewrite]

    def add(real: str, rewrite: bool = False) -> str:
        key = _norm(real)
        entry = sources.setdefault(key, [real, False])
        entry[1] = entry[1] or rewrite
        return key

    if resolver_context is not None:
        with Ar.ResolverContextBinder(resolver_context):
            layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(root.identifier))
    else:
        layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(root.identifier))
    to_read = []
    for layer in layers:
        if Ar.IsPackageRelativePath(layer.identifier):
            continue  # inner layer of a .usdz package: outer package is handled as an asset
        if layer.anonymous or not layer.realPath:
            plan.problems.append(Problem("unsaved", layer.identifier, "", layer.identifier))
            continue
        real = os.path.realpath(layer.realPath)
        if layer.dirty:  # the writer copies files, so edits only in memory would be lost
            plan.problems.append(Problem("unsaved", layer.identifier, real, layer.identifier))
        ext = os.path.splitext(real)[1].lower()
        if ext not in web_census.WEB_LAYERS:
            plan.problems.append(Problem("layer_format", layer.identifier, real, layer.identifier))
        add(real, rewrite=ext in USD_LAYERS)
        if ext in USD_LAYERS:
            to_read.append((layer, real))

    uses: dict[str, dict[str, str]] = {}  # layer key -> {authored: target key}
    for layer, real in to_read:
        table = uses.setdefault(_norm(real), {})
        for path in textures.authored_paths(layer):
            key = _place(plan, layer, path, folder, add)
            if key:
                table[path] = key

    # Safety nets: anything ComputeAllDependencies found that the walk above did not.
    for asset in assets:
        if not Ar.IsPackageRelativePath(asset) and os.path.isfile(asset):
            add(os.path.realpath(asset))
    known = {p.path for p in plan.problems} | {p.where for p in plan.problems}
    known |= {_norm(w) for w in known if w}
    for path in unresolved:
        if path not in known and _norm(path) not in known:
            plan.problems.append(Problem("missing", path, path, root.identifier))

    # Destinations: inside keeps its place, outside goes under one subfolder.
    dest: dict[str, str] = {}
    outside = []
    for key, (real, _rewrite) in sources.items():
        if key == root_key:
            dest[key] = plan.root
        elif inside(real, folder):
            dest[key] = os.path.relpath(real, root_dir).replace(os.sep, "/")
        else:
            outside.append((key, real))
    taken = {d.split("/", 1)[0].lower() for d in dest.values()}
    n = 2
    while plan.outside.lower() in taken:
        plan.outside, n = f"{OUTSIDE}_{n}", n + 1
    by_drive: dict[str, list] = {}
    for key, real in outside:
        by_drive.setdefault(os.path.splitdrive(real)[0].lower(), []).append((key, real))
    used_drive_labels: set[str] = set()
    for drive, group in sorted(by_drive.items()):
        base = os.path.commonpath([os.path.dirname(real) for _key, real in group])
        if len(by_drive) == 1:
            prefix = plan.outside
        else:
            label = _drive_label(drive)
            cand, idx = label, 2
            while cand.lower() in used_drive_labels:
                cand, idx = f"{label}_{idx}", idx + 1
            used_drive_labels.add(cand.lower())
            prefix = f"{plan.outside}/{cand}"
        for key, real in group:
            dest[key] = f"{prefix}/{os.path.relpath(real, base).replace(os.sep, '/')}"

    plan.files = [Placed(sources[k][0], dest[k], sources[k][1])
                  for k in sorted(sources, key=lambda k: (k != root_key, dest[k].lower()))]
    for key, table in uses.items():
        base = posixpath.dirname(dest[key])
        plan.rewrites[key] = {path: _relative(dest[target], base) for path, target in table.items()}
    return plan


def _place(plan: FolderPlan, layer: Sdf.Layer, path: str, folder: str, add) -> str | None:
    """The key of the file an authored path means, or None with a Problem recorded."""
    def problem(kind, where=""):
        plan.problems.append(Problem(kind, path, where, layer.identifier))

    if _URI.match(path):
        return problem("uri")
    if Ar.IsPackageRelativePath(path):
        return problem("package")
    if "$" in path:
        return problem("var")
    flagged = textures.flag(path)
    if flagged == "udim":
        return problem("udim", textures.anchor(layer, path))
    real = textures.locate(layer, path)
    if real is None or not os.path.isfile(real):
        return problem("absolute" if is_absolute(path) else "missing", textures.anchor(layer, path))
    if flagged:
        problem(flagged, real)  # placed all the same; a blocked report means it is never written
    key = add(real)
    reason = "absolute" if is_absolute(path) else None if inside(real, folder) else "outside"
    if reason:
        plan.copied_in.setdefault(reason, {}).setdefault(key, path)
    return key


def _fresh(path: Path) -> Sdf.Layer:
    """The layer at path as it is on disk now. Sdf keeps open layers in a registry, so a layer this
    process opened before (a previous export into the same folder) would come back stale."""
    layer = Sdf.Layer.Find(str(path))
    if layer:
        layer.Reload(force=True)
        return layer
    return Sdf.Layer.FindOrOpen(str(path))


def _with_items(report: LossReport, items: list[LossItem]) -> LossReport:
    """A copy of report with items added, its verdict recomputed by the rule."""
    if not items:
        return report
    return LossReport.from_items(report.format, list(report.items) + list(items), jev_note=report.jev_note)


def validation_items(validation) -> list[LossItem]:
    """Every problem validate_folder() found, as a LossItem, so validated=False always has a reason.

    The census reading FIX FIRST on the written folder is not one of Validation.problems, so it gets
    one catch-all item. Severity "changes", feature "validation" (decision validation-reporting).
    """
    items = [LossItem("changes", "validation", 1, [], problem, "fix the finding, then export again")
             for problem in validation.problems]
    if not validation.ok and not items:
        verdict = validation.verdict or "the folder does not open"
        items.append(LossItem("changes", "validation", 1, [validation.entry_file.name],
                              f"the web census on the written folder says {verdict}",
                              "fix the census findings, then export again"))
    return items


def check_target(stage: Usd.Stage, plan: FolderPlan, out_dir: Path) -> None:
    """Refuse an export folder that overlaps the source: never write into the source folder."""
    out = _norm(str(out_dir))
    root = stage.GetRootLayer()
    if root.realPath:
        src = os.path.dirname(os.path.realpath(root.realPath))
        if inside(str(out_dir), _norm(src)) or inside(src, out):
            raise ValueError(f"The export folder {out_dir} overlaps the source folder {src}. "
                             "Pick a separate folder: the export never writes into its source.")
    for placed in plan.files:
        if inside(placed.source, out):
            raise ValueError(f"The export folder {out_dir} holds {placed.source}, a file the stage uses. "
                             "Pick a separate folder: the export never writes over its source.")


class UsdFolderWriter:
    """Writes the USD folder (blueprint 4.4). Give it the report and plan the profile made, so
    nothing is worked out twice; without them it makes its own before writing anything.

    root is the identifier of the stage's root layer the report and plan were built from. When it
    is given, write() refuses a stage whose root layer differs: a report for one stage must never
    authorise writing another (decision report-stage-binding).
    """
    name = FORMAT

    def __init__(self, report: LossReport | None = None, plan: FolderPlan | None = None,
                 jev_on: bool = True, root: str | None = None) -> None:
        if report is not None and root is None:
            raise ValueError("a supplied report must name the root layer identifier it was built from; "
                             "pass root=stage.GetRootLayer().identifier")
        self.report, self.plan, self.jev_on, self.root = report, plan, jev_on, root

    def _check_stage(self, stage: Usd.Stage) -> None:
        got = stage.GetRootLayer().identifier
        if self.root is not None and got != self.root:
            raise ValueError(f"the report was built for {self.root}, but this writer was handed {got}; "
                             "build the report and the plan from the stage you write")

    def entry_file(self, stage: Usd.Stage, out_dir: Path) -> Path:
        self._check_stage(stage)
        plan = self.plan or plan_folder(stage.GetRootLayer())
        return Path(out_dir) / (plan.root or f"{Path(out_dir).name}.usda")

    def write(self, stage: Usd.Stage, inv, out_dir: Path, settings) -> ExportResult:
        self._check_stage(stage)
        out_dir = Path(out_dir)
        plan = self.plan or plan_folder(stage.GetRootLayer(), stage.GetPathResolverContext())
        report = self.report
        if report is None:  # principle 1: the report exists before anything is written
            from ..profiles.usd_folder import UsdFolderProfile  # here, not at the top: the profile imports this module
            profile = UsdFolderProfile(stage=stage, plan=plan, jev_on=self.jev_on)
            report = LossReport.from_items(FORMAT, profile.assess(inv), jev_note=profile.jev_note)
        entry = out_dir / (plan.root or f"{out_dir.name}.usda")
        if any(i.severity == "blocks" for i in report.items):
            return ExportResult(FORMAT, entry, [], 0, report, False)  # writes nothing, not even the folder
        if plan.problems:  # the profile turns every problem into a block; anything else is a bug, so stop
            raise RuntimeError(f"{len(plan.problems)} file(s) can't be placed but the report doesn't block: "
                               f"{plan.problems[0]}")
        check_target(stage, plan, out_dir)
        if settings is None:
            settings = read_settings(stage)

        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for placed in plan.files[1:] + plan.files[:1]:  # the root last, so it opens on finished files
            dst = out_dir / placed.dest
            textures.copy(placed.source, dst)
            if placed.rewrite:
                layer = _fresh(dst)
                table = plan.rewrites.get(_norm(placed.source), {})
                UsdUtils.ModifyAssetPaths(layer, lambda p, t=table: t.get(p, p), keepEmptyPathsInArrays=True)
                if placed.dest == plan.root:
                    mask = Usd.StagePopulationMask([Sdf.Path(SETTINGS_PATH)])
                    write_settings(Usd.Stage.OpenMasked(layer, mask, Usd.Stage.LoadNone), settings)
                layer.Save()
            written.append(dst)
        written = written[-1:] + written[:-1]  # the entry file first
        validation = validate_folder(entry)
        report = _with_items(report, validation_items(validation))  # principle 9: never a silent validated=False
        return ExportResult(FORMAT, entry, written, sum(f.stat().st_size for f in written), report,
                            validation.ok)
