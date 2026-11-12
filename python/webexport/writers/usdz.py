"""USDZ writer: one .usdz archive the stage and all its files travel inside.

Blueprint 4.4, 4.6 (the usdz column) and the v0.5 line, task U1. The writer does not
reimplement the copy/rewrite work: it runs the USD folder writer into a staging folder inside
out_dir, then packs that folder's root layer with UsdUtils.CreateNewUsdzPackage and removes
the staging folder. The .usdz is the one file the caller keeps.

    UsdzWriter(report=report, plan=profile.plan, folder_report=profile.folder_report,
               root=stage.GetRootLayer().identifier).write(stage, inv, out_dir, settings)

validate_usdz() re-opens the archive with Usd.Stage.Open and checks that /WebSettings is in
its root layer; ExportResult.validated is that answer, and a failure is reported as a
"validation" item, never dropped.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from pxr import Usd, UsdUtils

from ..report import LossItem, LossReport
from ..settings import SETTINGS_PATH
from .base import ExportResult
from .usd_folder import UsdFolderWriter

FORMAT = "usdz"
STAGING = ".usdz_staging"  # the USD folder is built here, packed, then removed


def validate_usdz(path: str | Path) -> tuple[bool, list[str]]:
    """The archive opens with Usd.Stage.Open and /WebSettings is in its root layer."""
    entry = Path(path)
    if not entry.is_file():
        return False, [f"{entry} was not written"]
    try:
        stage = Usd.Stage.Open(str(entry))
    except Exception as exc:  # a corrupt zip is the failure this function exists to catch
        return False, [f"{entry.name} does not open: {exc}"]
    if not stage:
        return False, [f"{entry.name} does not open"]
    problems = []
    prim = stage.GetPrimAtPath(SETTINGS_PATH)
    if not (prim and prim.GetTypeName() == "Scope" and stage.GetRootLayer().GetPrimAtPath(SETTINGS_PATH)):
        problems.append(f"{SETTINGS_PATH} is not a Scope in the archive's root layer")
    return not problems, problems


class UsdzWriter:
    """Writes the .usdz (blueprint 4.4) from the USD folder writer's staging output.

    Give it the report the profile made, so nothing is assessed twice. ``plan`` and
    ``folder_report`` are the USD folder's, from the same profile; without them the internal
    writer works them out before it writes. ``root`` is the identifier of the root layer the
    report was built from: a report for one stage must never authorise writing another
    (decision report-stage-binding).
    """
    name = FORMAT

    def __init__(self, report: LossReport | None = None, plan=None, folder_report: LossReport | None = None,
                 root: str | None = None) -> None:
        if report is not None and root is None:
            raise ValueError("a supplied report must name the root layer identifier it was built from; "
                             "pass root=stage.GetRootLayer().identifier")
        self.report, self.plan, self.folder_report, self.root = report, plan, folder_report, root

    def _check_stage(self, stage: Usd.Stage) -> None:
        got = stage.GetRootLayer().identifier
        if self.root is not None and got != self.root:
            raise ValueError(f"the report was built for {self.root}, but this writer was handed {got}; "
                             "build the report and the plan from the stage you write")

    def entry_file(self, stage: Usd.Stage, out_dir: Path) -> Path:
        name = stage.GetDefaultPrim().GetName() if stage.GetDefaultPrim() else "stage"
        return Path(out_dir) / f"{name}.usdz"

    def _folder_report(self, stage: Usd.Stage, inv) -> LossReport:
        """The package rules the internal USD folder writer must see before it writes."""
        if self.folder_report is not None:
            return self.folder_report
        from ..profiles.usdz import UsdzProfile  # here, not at the top: keeps the two modules one-way
        profile = UsdzProfile(stage=stage, plan=self.plan, jev_on=False)
        profile.assess(inv)
        self.plan = profile.plan
        return profile.folder_report

    def write(self, stage: Usd.Stage, inv, out_dir: Path, settings) -> ExportResult:
        if self.report is None:
            raise ValueError("the USDZ writer needs its report first (principle 1)")
        self._check_stage(stage)
        out_dir = Path(out_dir)
        entry = self.entry_file(stage, out_dir)
        if any(item.severity == "blocks" for item in self.report.items):
            return ExportResult(FORMAT, entry, [], 0, self.report, False)  # writes nothing, not even the folder

        folder_report = self._folder_report(stage, inv)
        staging = out_dir / STAGING
        if staging.exists():  # a previous run's staging folder is this writer's to replace
            shutil.rmtree(staging)
        try:
            folder = UsdFolderWriter(report=folder_report, plan=self.plan, root=self.root).write(
                stage, inv, staging, settings)
            if not folder.files:  # the package rules blocked after all: nothing to pack
                extra = [i for i in folder.report.items if i.severity == "blocks"]
                report = LossReport.from_items(FORMAT, list(self.report.items) + extra,
                                               jev_note=self.report.jev_note)
                return ExportResult(FORMAT, entry, [], 0, report, False)
            try:
                entry.unlink(missing_ok=True)  # replace any earlier archive in one step
                packed = UsdUtils.CreateNewUsdzPackage(str(folder.entry_file), str(entry))
                if not packed:
                    raise RuntimeError(f"UsdUtils.CreateNewUsdzPackage could not pack "
                                       f"{folder.entry_file} into {entry}")
            except Exception:
                entry.unlink(missing_ok=True)  # never leave a half-written archive behind
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        ok, problems = validate_usdz(entry)
        report = self.report
        if problems:
            report = LossReport.from_items(FORMAT, list(report.items) + [
                LossItem("changes", "validation", len(problems), problems[:3],
                         "the .usdz failed its own check", "fix the finding, then export again")],
                jev_note=report.jev_note)
        return ExportResult(FORMAT, entry, [entry], entry.stat().st_size, report, ok)
