"""The one door into the Web Export core. The HDA, the terminal and Synapse all call this.

    from webexport.api import check, export
    reports = check(stage, ["usd_folder"])                       # the LossReports, nothing written
    results = export(stage, ["usd_folder"], "D:/web/hero", None)  # list[ExportResult]

export() reads the stage once (inventory.gather), builds every format's LossReport, and only then
runs the writers (principle 1). A report with any "blocks" item writes nothing for that format:
its ExportResult has no files, 0 bytes, validated False, and the report says why.

Every report is built before the first writer runs: a writer that reads variants by selecting
them (glTF and X3D both round-trip the selection while they read) can leave the source root
layer dirty, and a report built after that would wrongly call the stage unsaved.

An anonymous root layer (a Houdini LOP stage) is flattened and staged to a temporary file first,
because the writer plans from files on disk; see _materialized().

Every format writes into out_dir itself; each writer owns its own entry file. The USD folder is
the whole folder. settings=None keeps the stage's own /WebSettings, or the defaults.

Pure pxr (principle 3): no Houdini here; hou_node.py calls this.
"""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pxr import Usd, UsdUtils

from .inventory import gather
from .profiles.gltf import GltfProfile
from .profiles.usd_folder import UsdFolderProfile
from .profiles.usdz import UsdzProfile
from .profiles.x3d import X3dProfile
from .report import LossItem, LossReport
from .settings import WebSettings, read_settings
from .validate import web_census
from .writers.base import ExportResult
from .writers.gltf import GltfWriter
from .writers.usd_folder import UsdFolderWriter
from .writers.usdz import UsdzWriter
from .writers.x3d import X3dWriter

FORMATS = ("usd_folder", "gltf", "x3d", "usdz")  # splats after that.
REPO = Path(__file__).resolve().parents[2]
PORT = 5180  # server/serve.mjs
HOUDINI_LAYER_INFO = "/HoudiniLayerInfo"  # Houdini session data; never part of a web export
PREVIEW_MISSING = "no preview: only an export under stages/ is served"
PREVIEW_FIX = "export under stages/ to get a preview link"


def _formats(formats) -> list[str]:
    formats = [formats] if isinstance(formats, str) else list(formats)
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise ValueError(f"unknown format(s) {unknown}; this version writes {list(FORMATS)}")
    return formats


def _stage_name(stage: Usd.Stage) -> str:
    """The file name for a flattened anonymous root: the default prim's name, else "stage"."""
    default = stage.GetDefaultPrim()
    return f"{default.GetName()}.usda" if default else "stage.usda"


@contextmanager
def _materialized(stage: Usd.Stage) -> Iterator[Usd.Stage]:
    """The stage itself, or a file-backed copy when its root layer is anonymous.

    A Houdini LOP stage's root layer is in memory, and the USD-folder writer plans from files on
    disk. FlattenLayerStack merges the root and every sublayer (the LOP session, viewport, purpose
    and solos layers included) and anchors file-relative asset paths to the files they resolve to,
    so the writer can copy them in. A path relative to an anonymous layer has no folder to anchor
    to; it stays relative and the writer reports it missing, never silently dropped.

    /HoudiniLayerInfo is stripped: it is Houdini's, and it stays out of what is written. The
    temporary folder is removed when the caller is done; nothing is written to the export folder
    before the report says it may be (principle 1).
    """
    if not stage.GetRootLayer().anonymous:
        yield stage
        return
    tmp = Path(tempfile.mkdtemp(prefix="webexport_lop_"))
    try:
        flat = UsdUtils.FlattenLayerStack(stage)
        staged = tmp / _stage_name(stage)
        flat.Export(str(staged))
        live = Usd.Stage.Open(str(staged))
        if live.GetPrimAtPath(HOUDINI_LAYER_INFO):
            live.RemovePrim(HOUDINI_LAYER_INFO)
            live.GetRootLayer().Save()
        yield live
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _with_items(report: LossReport, items: list[LossItem]) -> LossReport:
    """A copy of report with items added, its verdict recomputed by the rule."""
    return LossReport.from_items(report.format, list(report.items) + list(items), jev_note=report.jev_note)


def _assess(stage: Usd.Stage, fmt: str, facts, jev_on: bool):
    """(report, writer) for one format, the report built before the writer exists."""
    if fmt == "x3d":
        profile = X3dProfile(facts.inventory)
        report = LossReport.from_items(fmt, profile.assess(facts.inventory))
        return report, X3dWriter(report=report, root=stage.GetRootLayer().identifier)
    if fmt == "gltf":
        profile = GltfProfile(facts.inventory)
        report = LossReport.from_items(fmt, profile.assess(facts.inventory))
        return report, GltfWriter(report=report, root=stage.GetRootLayer().identifier)
    if fmt == "usd_folder":
        profile = UsdFolderProfile(stage=stage, facts=facts, jev_on=jev_on)
        report = LossReport.from_items(fmt, profile.assess(facts.inventory), jev_note=profile.jev_note)
        writer = UsdFolderWriter(report=report, plan=profile.plan, root=stage.GetRootLayer().identifier)
        return report, writer
    if fmt == "usdz":
        profile = UsdzProfile(stage=stage, facts=facts, jev_on=jev_on)
        report = LossReport.from_items(fmt, profile.assess(facts.inventory), jev_note=profile.jev_note)
        writer = UsdzWriter(report=report, plan=profile.plan, folder_report=profile.folder_report,
                            root=stage.GetRootLayer().identifier)
        return report, writer
    raise ValueError(f"unknown format {fmt!r}")


def check(stage: Usd.Stage, formats=FORMATS, jev_on: bool = True) -> list[LossReport]:
    """The LossReport for each format. Writes nothing to the export folder."""
    formats = _formats(formats)
    with _materialized(stage) as live:
        facts = gather(live, track_types=web_census.UNTESTED_TYPES)
        return [_assess(live, fmt, facts, jev_on)[0] for fmt in formats]


def preview_url(entry_file: Path) -> str | None:
    """The viewer URL for an export under the repo's stages/ folder, which the server serves."""
    try:
        rel = Path(entry_file).resolve().relative_to((REPO / "stages").resolve())
    except ValueError:
        return None
    return f"http://127.0.0.1:{PORT}/?stage=/stages/{rel.as_posix()}"


def export(stage: Usd.Stage, formats, out_dir, settings: WebSettings | None = None,
           jev_on: bool = True) -> list[ExportResult]:
    """Write the stage to each format in out_dir. One ExportResult per format, in order.

    An anonymous root layer (a Houdini LOP stage) is flattened and staged to a temporary file
    first; everything is then assessed and written from that file. A format whose report blocks
    writes nothing. An out_dir that overlaps the source folder raises ValueError before anything
    is written: the export never writes into its source.
    """
    formats = _formats(formats)
    out_dir = Path(out_dir)
    with _materialized(stage) as live:
        facts = gather(live, track_types=web_census.UNTESTED_TYPES)  # the one traversal
        if settings is None:
            settings = read_settings(live)
        # Every report before any writer: a writer's read-only variant round-trip can dirty the
        # source root, and a later report must still describe the stage as it arrived.
        prepared = [_assess(live, fmt, facts, jev_on) for fmt in formats]
        results = []
        for fmt, (report, writer) in zip(formats, prepared):
            if any(i.severity == "blocks" for i in report.items):  # principle 1: nothing is written
                results.append(ExportResult(fmt, writer.entry_file(live, out_dir), [], 0, report, False))
                continue
            result = writer.write(live, facts.inventory, out_dir, settings)
            url = (preview_url(result.entry_file)
                   if result.files and fmt == "usd_folder" else None)
            if result.files and fmt == "usd_folder" and url is None:  # say why there is no preview
                result.report = _with_items(result.report, [
                    LossItem("note", "preview_url", 1, [result.entry_file.name], PREVIEW_MISSING, PREVIEW_FIX)])
            result.preview_url = url
            results.append(result)
        return results
