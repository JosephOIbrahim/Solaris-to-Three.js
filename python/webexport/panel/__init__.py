"""Web Export Python Panel package for Houdini 22 Solaris (mission M9).

Copyright (c) 2026 Joseph O. Ibrahim. MIT.

`model.py` is pure Python (zero `hou` or Qt imports) so CI tests the presentation
layer without Houdini. `widget.py` loads PySide6 lazily when running inside Houdini 22.
"""
from __future__ import annotations

from .model import (
    FORMAT_META,
    FormatCardModel,
    MatrixRow,
    PALETTE,
    PanelSnapshot,
    SEVERITY_STYLE,
    StageSummary,
    VERDICT_STYLE,
    build_comparison_matrix,
    build_format_cards,
    build_snapshot,
    build_stage_summary,
    build_stylesheet,
    format_bytes,
    format_copy_json,
    format_copy_text,
    overall_verdict,
)

__all__ = [
    "FORMAT_META",
    "FormatCardModel",
    "MatrixRow",
    "PALETTE",
    "PanelSnapshot",
    "SEVERITY_STYLE",
    "StageSummary",
    "VERDICT_STYLE",
    "build_comparison_matrix",
    "build_format_cards",
    "build_snapshot",
    "build_stage_summary",
    "build_stylesheet",
    "format_bytes",
    "format_copy_json",
    "format_copy_text",
    "overall_verdict",
    "create_panel",
    "open_floating_panel",
]


def create_panel(pane_tab=None):
    from .widget import create_panel as _create_panel
    return _create_panel(pane_tab=pane_tab)


def open_floating_panel(interface_name: str = "web_export_panel"):
    from .widget import open_floating_panel as _open_floating_panel
    return _open_floating_panel(interface_name=interface_name)
