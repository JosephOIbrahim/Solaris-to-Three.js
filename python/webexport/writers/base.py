"""Writer contract and ExportResult (blueprint 4.4). No writer runs before its LossReport exists."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..report import LossReport

if TYPE_CHECKING:
    from pxr import Usd

    from ..inventory import Inventory  # inventory.py (M1); named, never imported here
    from ..settings import WebSettings


@dataclass
class ExportResult:
    format: str
    entry_file: Path
    files: list[Path]
    bytes: int
    report: LossReport
    validated: bool
    preview_url: str | None = None


@runtime_checkable
class Writer(Protocol):
    name: str

    def write(self, stage: "Usd.Stage", inv: "Inventory", out_dir: Path,
              settings: "WebSettings") -> ExportResult: ...
