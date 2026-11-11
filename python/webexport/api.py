"""The one door into the Web Export core. The HDA, the terminal and Synapse all call this.

    from webexport.api import check, export
    reports = check(stage, ["usd_folder"])                       # the LossReports, nothing written
    results = export(stage, ["usd_folder"], "D:/web/hero", None)  # list[ExportResult]

export() reads the stage once (inventory.gather), builds each format's LossReport, and only then
runs that format's writer (principle 1). A report with any "blocks" item writes nothing for that
format: its ExportResult has no files, 0 bytes, validated False, and the report says why.

Every format writes into out_dir itself; each writer owns its own entry file. The USD folder is
the whole folder. settings=None keeps the stage's own /WebSettings, or the defaults.

Pure pxr (principle 3): no Houdini here; hou_node.py calls this.
"""
from __future__ import annotations

from pathlib import Path

from pxr import Usd

from .inventory import gather
from .profiles.usd_folder import UsdFolderProfile
from .report import LossReport
from .settings import WebSettings, read_settings
from .validate import web_census
from .writers.base import ExportResult
from .writers.usd_folder import UsdFolderWriter

FORMATS = ("usd_folder",)  # v0.2. glTF (M7) and X3D (M8) join here.
REPO = Path(__file__).resolve().parents[2]
PORT = 5180  # server/serve.mjs


def _formats(formats) -> list[str]:
    formats = [formats] if isinstance(formats, str) else list(formats)
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise ValueError(f"unknown format(s) {unknown}; this version writes {list(FORMATS)}")
    return formats


def _assess(stage: Usd.Stage, fmt: str, facts, jev_on: bool):
    """(report, writer) for one format, the report built before the writer exists."""
    if fmt == "usd_folder":
        profile = UsdFolderProfile(stage=stage, facts=facts, jev_on=jev_on)
        report = LossReport.from_items(fmt, profile.assess(facts.inventory), jev_note=profile.jev_note)
        return report, UsdFolderWriter(report=report, plan=profile.plan)
    raise ValueError(f"unknown format {fmt!r}")


def check(stage: Usd.Stage, formats=FORMATS, jev_on: bool = True) -> list[LossReport]:
    """The LossReport for each format. Writes nothing."""
    formats = _formats(formats)
    facts = gather(stage, track_types=web_census.UNTESTED_TYPES)
    return [_assess(stage, fmt, facts, jev_on)[0] for fmt in formats]


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

    A format whose report blocks writes nothing. An out_dir that overlaps the source folder raises
    ValueError before anything is written: the export never writes into its source.
    """
    formats = _formats(formats)
    out_dir = Path(out_dir)
    facts = gather(stage, track_types=web_census.UNTESTED_TYPES)  # the one traversal
    if settings is None:
        settings = read_settings(stage)
    results = []
    for fmt in formats:
        report, writer = _assess(stage, fmt, facts, jev_on)
        if any(i.severity == "blocks" for i in report.items):  # principle 1: nothing is written
            results.append(ExportResult(fmt, writer.entry_file(stage, out_dir), [], 0, report, False))
            continue
        result = writer.write(stage, facts.inventory, out_dir, settings)
        result.preview_url = preview_url(result.entry_file) if result.files else None
        results.append(result)
    return results
