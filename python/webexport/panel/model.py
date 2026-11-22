"""Pure-Python view-model, formatting, and house palette for the Web Export Python Panel.

Copyright (c) 2026 Joseph O. Ibrahim. MIT.

Zero Houdini (`hou`) and zero Qt (`PySide6`) imports here so CI can test the entire
presentation model under plain `usd-core` (blueprint principles 3 and 5).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Iterable

from ..report import LossItem, LossReport, SEVERITIES, TITLES, Verdict

# House palette derived from docs/assets/logo-mark-dark.svg and docs/assets/src/lockup.html.
PALETTE: dict[str, str] = {
    "ground": "#1C1B19",      # ink
    "panel": "#23211E",       # elevated surface
    "surface": "#2B2926",     # cards and inputs
    "surface_hi": "#34312D",  # hover / selected card
    "border": "#3E3A35",      # hairline dividers
    "text": "#DAD6CF",        # primary parchment text
    "muted": "#A79A90",       # secondary stone text
    "vermilion": "#E25B3F",   # brand sun / FIX FIRST / blocks
    "vermilion_bg": "#3A211C",
    "ochre": "#D99B38",       # GO, WITH NOTES / changes
    "ochre_bg": "#362B1A",
    "moss": "#6A9B5B",        # GO / clean
    "moss_bg": "#1F2F1D",
    "stone": "#8D8A84",       # notes / neutral / idle
    "stone_bg": "#2B2926",
}

FORMAT_META: dict[str, dict[str, str]] = {
    "usd_folder": {"label": "USD Folder", "ext": ".usda", "subtitle": "Full Solaris stage + /WebSettings"},
    "gltf": {"label": "glTF", "ext": ".glb", "subtitle": "Single binary, PBR + variants + GPU instancing"},
    "x3d": {"label": "X3D", "ext": ".x3d", "subtitle": "X3D 4.0 XML, PhysicalMaterial + Switch"},
    "usdz": {"label": "USDZ", "ext": ".usdz", "subtitle": "Self-contained archive for Quick Look"},
}

VERDICT_RANK: dict[str, int] = {
    "IDLE": -1,
    "GO": 0,
    "GO, WITH NOTES": 1,
    "FIX FIRST": 2,
}

VERDICT_STYLE: dict[str, tuple[str, str]] = {
    "GO": (PALETTE["moss"], PALETTE["moss_bg"]),
    "GO, WITH NOTES": (PALETTE["ochre"], PALETTE["ochre_bg"]),
    "FIX FIRST": (PALETTE["vermilion"], PALETTE["vermilion_bg"]),
    "IDLE": (PALETTE["stone"], PALETTE["stone_bg"]),
}

SEVERITY_STYLE: dict[str, tuple[str, str, str]] = {
    "blocks": ("x", PALETTE["vermilion"], PALETTE["vermilion_bg"]),
    "changes": ("!", PALETTE["ochre"], PALETTE["ochre_bg"]),
    "note": ("-", PALETTE["stone"], PALETTE["stone_bg"]),
}


@dataclass(frozen=True)
class StageSummary:
    """Compact high-signal facts strip for the currently inspected Solaris stage."""

    up_axis: str = "Y"
    meters_per_unit: float = 1.0
    meshes: int = 0
    instancers: int = 0
    instances: int = 0
    materials: int = 0
    materialx: int = 0
    preview_materials: int = 0
    lights: int = 0
    cameras: int = 0
    variant_sets: int = 0
    textures: int = 0
    volumes: int = 0
    time_range: tuple[float, float] | None = None

    def one_line(self) -> str:
        parts = [
            f"up {self.up_axis}",
            f"{self.meters_per_unit:g} m/unit",
            f"{self.meshes} mesh{'es' if self.meshes != 1 else ''}",
        ]
        if self.instances:
            parts.append(f"{self.instances} instances")
        parts.append(f"{self.materials} mat{'s' if self.materials != 1 else ''}")
        parts.append(f"{self.lights} light{'s' if self.lights != 1 else ''}")
        parts.append(f"{self.cameras} cam{'s' if self.cameras != 1 else ''}")
        if self.variant_sets:
            parts.append(f"{self.variant_sets} variant set{'s' if self.variant_sets != 1 else ''}")
        if self.textures:
            parts.append(f"{self.textures} texture{'s' if self.textures != 1 else ''}")
        if self.volumes:
            parts.append(f"{self.volumes} volume{'s' if self.volumes != 1 else ''}")
        if self.time_range is not None:
            start, end = self.time_range
            parts.append(f"frames {start:g}–{end:g}")
        return "  ·  ".join(parts)


@dataclass
class FormatCardModel:
    """Presentation model for one format card in the 4-column summary strip."""

    format: str
    label: str
    ext: str
    subtitle: str
    verdict: str
    blocks: int = 0
    changes: int = 0
    notes: int = 0
    jev_note: str = ""
    items: list[LossItem] = field(default_factory=list)
    exported_files: int = 0
    exported_bytes: int = 0
    validated: bool | None = None
    entry_file: str | None = None
    preview_url: str | None = None

    def counts_text(self) -> str:
        return f"x {self.blocks}   ! {self.changes}   - {self.notes}"

    def export_badge(self) -> str:
        if self.validated is None:
            return "not exported yet"
        if self.exported_files == 0:
            return "not written (blocked)"
        state = "validated" if self.validated else "check failed"
        return f"{self.exported_files} file{'s' if self.exported_files != 1 else ''} · {format_bytes(self.exported_bytes)} · {state}"


@dataclass(frozen=True)
class MatrixRow:
    """One feature compared side-by-side across all inspected formats."""

    feature: str
    worst_severity: str
    cells: dict[str, tuple[str, str]]  # fmt -> (severity_or_"ok", summary_text)


@dataclass
class PanelSnapshot:
    """Complete immutable-like snapshot rendered by WebExportPanel."""

    node_path: str = ""
    is_web_export_node: bool = False
    overall_verdict: str = "IDLE"
    stage_summary: StageSummary | None = None
    cards: list[FormatCardModel] = field(default_factory=list)
    matrix: list[MatrixRow] = field(default_factory=list)
    reports: list[LossReport] = field(default_factory=list)
    headline: str = ""
    error: str = ""
    preview_url: str | None = None
    updated_at: str = ""


def format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def overall_verdict(reports: Iterable[LossReport]) -> str:
    """Worst verdict across all active formats: FIX FIRST > GO, WITH NOTES > GO > IDLE."""
    reports = list(reports)
    if not reports:
        return "IDLE"
    return max((r.verdict for r in reports), key=lambda v: VERDICT_RANK.get(v, -1))


def build_stage_summary(inv) -> StageSummary | None:
    """Project an Inventory dataclass into a compact StageSummary."""
    if inv is None:
        return None
    return StageSummary(
        up_axis=str(getattr(inv, "up_axis", "Y") or "Y"),
        meters_per_unit=float(getattr(inv, "meters_per_unit", 1.0) or 1.0),
        meshes=len(inv.meshes),
        instancers=len(inv.instancers),
        instances=sum(i.count for i in inv.instancers),
        materials=len(inv.materials),
        materialx=sum(1 for m in inv.materials if m.kind == "materialx"),
        preview_materials=sum(1 for m in inv.materials if m.kind == "preview"),
        lights=len(inv.lights),
        cameras=len(inv.cameras),
        variant_sets=len(inv.variant_sets),
        textures=len(inv.textures),
        volumes=len(inv.volumes),
        time_range=inv.time_range,
    )


def build_format_cards(reports: Iterable[LossReport], results: Iterable[Any] | None = None) -> list[FormatCardModel]:
    """Turn LossReports (and optional ExportResults) into per-format UI cards."""
    by_fmt = {r.format: r for r in (results or ())}
    cards: list[FormatCardModel] = []
    for rep in reports:
        meta = FORMAT_META.get(rep.format, {"label": rep.format, "ext": "", "subtitle": ""})
        res = by_fmt.get(rep.format)
        cards.append(
            FormatCardModel(
                format=rep.format,
                label=meta["label"],
                ext=meta["ext"],
                subtitle=meta["subtitle"],
                verdict=rep.verdict,
                blocks=sum(1 for i in rep.items if i.severity == "blocks"),
                changes=sum(1 for i in rep.items if i.severity == "changes"),
                notes=sum(1 for i in rep.items if i.severity == "note"),
                jev_note=rep.jev_note,
                items=list(rep.items),
                exported_files=len(res.files) if res is not None else 0,
                exported_bytes=int(res.bytes) if res is not None else 0,
                validated=bool(res.validated) if res is not None else None,
                entry_file=str(res.entry_file) if (res is not None and res.entry_file) else None,
                preview_url=res.preview_url if res is not None else None,
            )
        )
    return cards


def build_comparison_matrix(reports: Iterable[LossReport]) -> list[MatrixRow]:
    """Build a feature-by-format matrix sorted worst severity first."""
    reports = list(reports)
    if not reports:
        return []
    sev_order = {s: idx for idx, s in enumerate(SEVERITIES)}
    per_fmt: dict[str, dict[str, LossItem]] = {}
    features: set[str] = set()
    for rep in reports:
        bucket: dict[str, LossItem] = {}
        for item in rep.items:
            features.add(item.feature)
            prev = bucket.get(item.feature)
            if prev is None or sev_order[item.severity] < sev_order[prev.severity]:
                bucket[item.feature] = item
        per_fmt[rep.format] = bucket

    rows: list[MatrixRow] = []
    for feature in sorted(features):
        cells: dict[str, tuple[str, str]] = {}
        worst = "note"
        worst_rank = 99
        for rep in reports:
            item = per_fmt[rep.format].get(feature)
            if item is None:
                cells[rep.format] = ("ok", "kept")
            else:
                rank = sev_order[item.severity]
                if rank < worst_rank:
                    worst_rank = rank
                    worst = item.severity
                mark = SEVERITY_STYLE[item.severity][0]
                detail = item.becomes or TITLES[item.severity].lower()
                cells[rep.format] = (item.severity, f"{mark} ({item.count}) {detail}")
        rows.append(MatrixRow(feature=feature, worst_severity=worst, cells=cells))

    rows.sort(key=lambda r: (sev_order.get(r.worst_severity, 9), r.feature))
    return rows


def build_snapshot(
    node_path: str = "",
    is_web_export_node: bool = False,
    inv=None,
    reports: Iterable[LossReport] = (),
    results: Iterable[Any] | None = None,
    headline: str = "",
    error: str = "",
) -> PanelSnapshot:
    """Construct a full PanelSnapshot from core webexport outputs."""
    reports_list = list(reports)
    results_list = list(results) if results is not None else None
    cards = build_format_cards(reports_list, results_list)
    preview = next((c.preview_url for c in cards if c.preview_url), None)
    verdict = "FIX FIRST" if error else overall_verdict(reports_list)
    return PanelSnapshot(
        node_path=node_path,
        is_web_export_node=is_web_export_node,
        overall_verdict=verdict,
        stage_summary=build_stage_summary(inv),
        cards=cards,
        matrix=build_comparison_matrix(reports_list),
        reports=reports_list,
        headline=headline,
        error=error,
        preview_url=preview,
        updated_at=datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    )


def format_copy_text(snapshot: PanelSnapshot) -> str:
    """Plain-text report suitable for clipboard copy or the Raw tab."""
    blocks: list[str] = []
    if snapshot.headline:
        blocks.append(snapshot.headline)
    if snapshot.error:
        blocks.append(f"ERROR: {snapshot.error}")
    if snapshot.stage_summary is not None:
        blocks.append(f"STAGE  {snapshot.node_path or '(none)'}\n  {snapshot.stage_summary.one_line()}")
    for rep in snapshot.reports:
        blocks.append(rep.render_text())
    return "\n\n".join(blocks) if blocks else "No report yet. Select a LOP node and click Check."


def format_copy_json(snapshot: PanelSnapshot) -> str:
    """Machine-readable JSON payload for clipboard copy, tickets, or Synapse."""
    payload = {
        "node_path": snapshot.node_path,
        "overall_verdict": snapshot.overall_verdict,
        "headline": snapshot.headline,
        "error": snapshot.error,
        "preview_url": snapshot.preview_url,
        "updated_at": snapshot.updated_at,
        "stage_summary": asdict(snapshot.stage_summary) if snapshot.stage_summary is not None else None,
        "reports": [r.to_json() for r in snapshot.reports],
    }
    return json.dumps(payload, indent=2)


def build_stylesheet() -> str:
    """Qt6 stylesheet styled to match Solaris-to-Three.js's dark ink + vermilion visual system."""
    p = PALETTE
    return f"""
    QWidget#WebExportPanel {{
        background-color: {p['ground']};
        color: {p['text']};
        font-family: "Segoe UI", "Inter", sans-serif;
        font-size: 12px;
    }}
    QFrame#HeaderBar, QFrame#ControlsBar, QFrame#FactsBar {{
        background-color: {p['panel']};
        border: 1px solid {p['border']};
        border-radius: 6px;
    }}
    QFrame#FormatCard {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 6px;
    }}
    QFrame#FormatCard[selected="true"] {{
        background-color: {p['surface_hi']};
        border: 1px solid {p['vermilion']};
    }}
    QLabel#TitleLabel {{
        font-size: 14px;
        font-weight: 600;
        color: {p['text']};
        letter-spacing: 0.4px;
    }}
    QLabel#MutedLabel {{
        color: {p['muted']};
        font-size: 11px;
    }}
    QLabel#MonoLabel {{
        font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
        font-size: 11px;
        color: {p['muted']};
    }}
    QPushButton {{
        background-color: {p['surface']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 4px;
        padding: 5px 11px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {p['surface_hi']};
        border-color: {p['muted']};
    }}
    QPushButton:pressed {{
        background-color: {p['panel']};
    }}
    QPushButton:disabled {{
        color: {p['stone']};
        border-color: {p['panel']};
    }}
    QPushButton#PrimaryButton {{
        background-color: {p['vermilion']};
        color: #FFFFFF;
        border: 1px solid {p['vermilion']};
        font-weight: 600;
    }}
    QPushButton#PrimaryButton:hover {{
        background-color: #EC6D52;
    }}
    QLineEdit, QDoubleSpinBox, QPlainTextEdit {{
        background-color: {p['ground']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 4px;
        padding: 4px 6px;
        selection-background-color: {p['vermilion']};
    }}
    QPlainTextEdit {{
        font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
        font-size: 11px;
    }}
    QCheckBox {{
        color: {p['text']};
        spacing: 6px;
    }}
    QTabWidget::pane {{
        border: 1px solid {p['border']};
        border-radius: 6px;
        background-color: {p['panel']};
    }}
    QTabBar::tab {{
        background-color: {p['ground']};
        color: {p['muted']};
        border: 1px solid {p['border']};
        border-bottom: none;
        padding: 6px 14px;
        margin-right: 2px;
        border-top-left-radius: 5px;
        border-top-right-radius: 5px;
    }}
    QTabBar::tab:selected {{
        background-color: {p['panel']};
        color: {p['text']};
        font-weight: 600;
    }}
    QTableWidget, QTreeWidget {{
        background-color: {p['ground']};
        alternate-background-color: {p['panel']};
        color: {p['text']};
        border: none;
        gridline-color: {p['border']};
        selection-background-color: {p['surface_hi']};
    }}
    QHeaderView::section {{
        background-color: {p['surface']};
        color: {p['muted']};
        border: none;
        border-right: 1px solid {p['border']};
        border-bottom: 1px solid {p['border']};
        padding: 5px 8px;
        font-weight: 600;
        font-size: 11px;
    }}
    """
