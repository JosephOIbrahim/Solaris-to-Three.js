"""Houdini 22 PySide6 Python Panel widget for Web Export (mission M9).

Copyright (c) 2026 Joseph O. Ibrahim. MIT.

Renders side-by-side LossReports for USD Folder, glTF, X3D, and USDZ, syncs
with a selected Web Export LOP (or inspects any Solaris LOP read-only), and
drives Check, Export, and Preview via webexport.api and webexport.hou_node.
"""
from __future__ import annotations

import os
from pathlib import Path
import weakref

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - fallback for older Qt5 hosts
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from .. import api
from ..inventory import gather
from ..report import SEVERITIES, TITLES
from ..settings import WebSettings
from ..validate import web_census
from .model import (
    FORMAT_META,
    FormatCardModel,
    PALETTE,
    PanelSnapshot,
    SEVERITY_STYLE,
    VERDICT_STYLE,
    build_snapshot,
    build_stylesheet,
    format_copy_json,
    format_copy_text,
)

HDA_TYPE = "mythandlogic::web_export::1.0"
_LIVE_PANELS: "weakref.WeakSet[WebExportPanel]" = weakref.WeakSet()


def _try_hou():
    try:
        import hou
        return hou
    except ImportError:
        return None


def _is_lop_node(node) -> bool:
    if node is None:
        return False
    try:
        return node.type().category().name() == "Lop"
    except Exception:
        return False


def _is_web_export_node(node) -> bool:
    if not _is_lop_node(node):
        return False
    try:
        name = node.type().name()
        return name == HDA_TYPE or name.startswith("mythandlogic::web_export")
    except Exception:
        return False


def _icon_path() -> Path:
    return Path(__file__).resolve().parents[3] / "houdini" / "config" / "Icons" / "webexport.svg"


def _apply_pill_style(label: QtWidgets.QLabel, fg: str, bg: str) -> None:
    label.setStyleSheet(
        f"color: {fg}; background-color: {bg}; border: 1px solid {fg}; "
        "border-radius: 4px; padding: 2px 8px; font-weight: 700; font-size: 11px;"
    )


class _FormatCard(QtWidgets.QFrame):
    """Clickable verdict summary card for one web export format."""

    clicked = QtCore.Signal(str)

    def __init__(self, fmt: str, parent=None) -> None:
        super().__init__(parent)
        self.fmt = fmt
        self.setObjectName("FormatCard")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", False)

        meta = FORMAT_META.get(fmt, {"label": fmt, "ext": "", "subtitle": ""})
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(6)
        self.name_label = QtWidgets.QLabel(f"{meta['label']}  {meta['ext']}".strip())
        self.name_label.setStyleSheet(f"color: {PALETTE['text']}; font-weight: 600; font-size: 12px;")
        self.verdict_label = QtWidgets.QLabel("IDLE")
        self.verdict_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        _apply_pill_style(self.verdict_label, *VERDICT_STYLE["IDLE"])
        top.addWidget(self.name_label, 1)
        top.addWidget(self.verdict_label, 0)
        layout.addLayout(top)

        self.counts_label = QtWidgets.QLabel("x 0   ! 0   - 0")
        self.counts_label.setObjectName("MonoLabel")
        layout.addWidget(self.counts_label)

        self.export_label = QtWidgets.QLabel("not exported yet")
        self.export_label.setObjectName("MutedLabel")
        self.export_label.setWordWrap(True)
        layout.addWidget(self.export_label)

    def update_card(self, card: FormatCardModel | None) -> None:
        if card is None:
            self.verdict_label.setText("OFF")
            _apply_pill_style(self.verdict_label, *VERDICT_STYLE["IDLE"])
            self.counts_label.setText("disabled")
            self.export_label.setText("tick format to include")
            return
        self.verdict_label.setText(card.verdict)
        fg, bg = VERDICT_STYLE.get(card.verdict, VERDICT_STYLE["IDLE"])
        _apply_pill_style(self.verdict_label, fg, bg)
        self.counts_label.setText(card.counts_text())
        self.export_label.setText(card.export_badge())

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self.fmt)
        super().mousePressEvent(event)


class WebExportPanel(QtWidgets.QWidget):
    """Side-by-side loss report inspector and export control panel for Houdini 22 Solaris."""

    def __init__(self, pane_tab=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("WebExportPanel")
        self.setWindowTitle("Web Export")
        self.setStyleSheet(build_stylesheet())

        self._pane_tab = pane_tab
        self._node = None
        self._snapshot = PanelSnapshot()
        self._active_format_filter: str | None = None
        self._syncing_parms = False
        self._cards: dict[str, _FormatCard] = {}
        self._fmt_checks: dict[str, QtWidgets.QCheckBox] = {}

        self._build_ui()
        _LIVE_PANELS.add(self)
        self.use_selected_node(auto_check=False)

    # ---- UI construction ----------------------------------------------------------

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addWidget(self._build_header())
        root.addWidget(self._build_controls())
        root.addWidget(self._build_facts_bar())
        root.addWidget(self._build_cards_strip())
        root.addWidget(self._build_tabs(), 1)
        root.addWidget(self._build_footer())

    def _build_header(self) -> QtWidgets.QFrame:
        bar = QtWidgets.QFrame()
        bar.setObjectName("HeaderBar")
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        icon_file = _icon_path()
        if icon_file.is_file():
            logo = QtWidgets.QLabel()
            pix = QtGui.QPixmap(str(icon_file)).scaled(
                22, 22,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            logo.setPixmap(pix)
            row.addWidget(logo)

        title_col = QtWidgets.QVBoxLayout()
        title_col.setSpacing(1)
        title = QtWidgets.QLabel("WEB EXPORT")
        title.setObjectName("TitleLabel")
        self.node_label = QtWidgets.QLabel("No LOP node selected")
        self.node_label.setObjectName("MonoLabel")
        title_col.addWidget(title)
        title_col.addWidget(self.node_label)
        row.addLayout(title_col, 1)

        self.overall_pill = QtWidgets.QLabel("IDLE")
        _apply_pill_style(self.overall_pill, *VERDICT_STYLE["IDLE"])
        row.addWidget(self.overall_pill)

        self.btn_selected = QtWidgets.QPushButton("Use Selected")
        self.btn_selected.setToolTip("Bind panel to the currently selected LOP node in /stage")
        self.btn_selected.clicked.connect(lambda: self.use_selected_node(auto_check=True))
        row.addWidget(self.btn_selected)

        self.btn_add = QtWidgets.QPushButton("+ Web Export")
        self.btn_add.setToolTip("Create and wire a Web Export LOP node below the current LOP")
        self.btn_add.clicked.connect(self.add_web_export_node)
        row.addWidget(self.btn_add)

        self.btn_check = QtWidgets.QPushButton("Check")
        self.btn_check.setToolTip("Run read-only loss reports across all selected formats (never writes)")
        self.btn_check.clicked.connect(self.run_check)
        row.addWidget(self.btn_check)

        self.btn_export = QtWidgets.QPushButton("Export")
        self.btn_export.setObjectName("PrimaryButton")
        self.btn_export.setToolTip("Write selected formats when nothing blocks (FIX FIRST writes nothing)")
        self.btn_export.clicked.connect(self.run_export)
        row.addWidget(self.btn_export)

        self.btn_preview = QtWidgets.QPushButton("Preview")
        self.btn_preview.setToolTip("Start local COOP/COEP server and open the browser viewer")
        self.btn_preview.clicked.connect(self.run_preview)
        row.addWidget(self.btn_preview)

        return bar

    def _build_controls(self) -> QtWidgets.QFrame:
        box = QtWidgets.QFrame()
        box.setObjectName("ControlsBar")
        col = QtWidgets.QVBoxLayout(box)
        col.setContentsMargins(10, 8, 10, 8)
        col.setSpacing(6)

        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(10)
        fmt_lbl = QtWidgets.QLabel("Formats:")
        fmt_lbl.setObjectName("MutedLabel")
        row1.addWidget(fmt_lbl)
        for fmt in api.FORMATS:
            meta = FORMAT_META[fmt]
            cb = QtWidgets.QCheckBox(meta["label"])
            cb.setChecked(True)
            cb.setToolTip(meta["subtitle"])
            cb.toggled.connect(self._on_controls_changed)
            self._fmt_checks[fmt] = cb
            row1.addWidget(cb)

        row1.addStretch(1)
        self.cb_jev = QtWidgets.QCheckBox("Use Jev")
        self.cb_jev.setChecked(True)
        self.cb_jev.setToolTip("Ask TypeSafe Jev about unfamiliar shader nodes when TYPESAFE_API_KEY is set")
        row1.addWidget(self.cb_jev)

        self.btn_settings_toggle = QtWidgets.QPushButton("/WebSettings ▸")
        self.btn_settings_toggle.setCheckable(True)
        self.btn_settings_toggle.toggled.connect(self._toggle_web_settings)
        row1.addWidget(self.btn_settings_toggle)
        col.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(6)
        out_lbl = QtWidgets.QLabel("Output Folder:")
        out_lbl.setObjectName("MutedLabel")
        self.outdir_edit = QtWidgets.QLineEdit("$HIP/web_export")
        self.outdir_edit.editingFinished.connect(self._on_controls_changed)
        btn_browse = QtWidgets.QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_outdir)
        row2.addWidget(out_lbl)
        row2.addWidget(self.outdir_edit, 1)
        row2.addWidget(btn_browse)
        col.addLayout(row2)

        # Collapsible /WebSettings drawer
        self.ws_container = QtWidgets.QWidget()
        self.ws_container.setVisible(False)
        grid = QtWidgets.QGridLayout(self.ws_container)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        self.ws_title = QtWidgets.QLineEdit()
        self.ws_title.setPlaceholderText("Stage file name (default)")
        self.ws_camera = QtWidgets.QLineEdit()
        self.ws_camera.setPlaceholderText("First camera on stage (default)")
        self.ws_hudvs = QtWidgets.QLineEdit()
        self.ws_hudvs.setPlaceholderText("e.g. look, model")
        self.ws_hudvs_limit = QtWidgets.QCheckBox("Limit HUD Variants")
        self.ws_showproxy = QtWidgets.QCheckBox("Show Proxy")
        self.ws_showhud = QtWidgets.QCheckBox("Show HUD")
        self.ws_showhud.setChecked(True)
        self.ws_bgblur = QtWidgets.QDoubleSpinBox()
        self.ws_bgblur.setRange(0.0, 1.0)
        self.ws_bgblur.setSingleStep(0.05)
        self.ws_bgblur.setValue(0.35)
        self.ws_exposure = QtWidgets.QDoubleSpinBox()
        self.ws_exposure.setRange(-5.0, 5.0)
        self.ws_exposure.setSingleStep(0.25)
        self.ws_exposure.setValue(0.0)

        for w in (self.ws_title, self.ws_camera, self.ws_hudvs):
            w.editingFinished.connect(self._on_controls_changed)
        for cb in (self.ws_hudvs_limit, self.ws_showproxy, self.ws_showhud):
            cb.toggled.connect(self._on_controls_changed)
        for sp in (self.ws_bgblur, self.ws_exposure):
            sp.valueChanged.connect(self._on_controls_changed)

        grid.addWidget(QtWidgets.QLabel("Title"), 0, 0)
        grid.addWidget(self.ws_title, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Camera Prim"), 0, 2)
        grid.addWidget(self.ws_camera, 0, 3)

        grid.addWidget(QtWidgets.QLabel("HUD Variant Sets"), 1, 0)
        grid.addWidget(self.ws_hudvs, 1, 1)
        grid.addWidget(self.ws_hudvs_limit, 1, 2)
        toggles = QtWidgets.QHBoxLayout()
        toggles.addWidget(self.ws_showhud)
        toggles.addWidget(self.ws_showproxy)
        grid.addLayout(toggles, 1, 3)

        grid.addWidget(QtWidgets.QLabel("Background Blur"), 2, 0)
        grid.addWidget(self.ws_bgblur, 2, 1)
        grid.addWidget(QtWidgets.QLabel("Exposure Offset"), 2, 2)
        grid.addWidget(self.ws_exposure, 2, 3)

        col.addWidget(self.ws_container)
        return box

    def _build_facts_bar(self) -> QtWidgets.QFrame:
        bar = QtWidgets.QFrame()
        bar.setObjectName("FactsBar")
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(10, 5, 10, 5)
        self.facts_label = QtWidgets.QLabel("Select a Solaris LOP node and click Check to inspect the stage.")
        self.facts_label.setObjectName("MonoLabel")
        self.facts_label.setWordWrap(True)
        row.addWidget(self.facts_label, 1)
        return bar

    def _build_cards_strip(self) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for fmt in api.FORMATS:
            card = _FormatCard(fmt)
            card.clicked.connect(self._on_card_clicked)
            self._cards[fmt] = card
            row.addWidget(card, 1)
        return holder

    def _build_tabs(self) -> QtWidgets.QTabWidget:
        self.tabs = QtWidgets.QTabWidget()

        # Tab 1: Side-by-Side Comparison Matrix
        self.matrix_table = QtWidgets.QTableWidget(0, 1 + len(api.FORMATS))
        self.matrix_table.setAlternatingRowColors(True)
        self.matrix_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matrix_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.matrix_table.setWordWrap(True)
        self.matrix_table.verticalHeader().setVisible(False)
        headers = ["Feature"] + [FORMAT_META[f]["label"] for f in api.FORMATS]
        self.matrix_table.setHorizontalHeaderLabels(headers)
        self.tabs.addTab(self.matrix_table, "Side-by-Side Matrix")

        # Tab 2: Detailed Findings Tree
        findings_wrap = QtWidgets.QWidget()
        f_col = QtWidgets.QVBoxLayout(findings_wrap)
        f_col.setContentsMargins(6, 6, 6, 6)
        f_col.setSpacing(6)

        filter_row = QtWidgets.QHBoxLayout()
        self.filter_label = QtWidgets.QLabel("Showing: All formats")
        self.filter_label.setObjectName("MutedLabel")
        btn_all = QtWidgets.QPushButton("Show All")
        btn_all.clicked.connect(self._clear_format_filter)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Filter findings or prim paths…")
        self.search_edit.textChanged.connect(lambda _t: self._populate_findings_tree())
        filter_row.addWidget(self.filter_label)
        filter_row.addWidget(btn_all)
        filter_row.addStretch(1)
        filter_row.addWidget(self.search_edit, 1)
        f_col.addLayout(filter_row)

        self.findings_tree = QtWidgets.QTreeWidget()
        self.findings_tree.setHeaderLabels(["Severity / Feature", "Format", "Count", "Becomes", "Fix / Example"])
        self.findings_tree.setAlternatingRowColors(True)
        self.findings_tree.setRootIsDecorated(True)
        self.findings_tree.itemDoubleClicked.connect(self._on_finding_double_clicked)
        f_col.addWidget(self.findings_tree, 1)
        self.tabs.addTab(findings_wrap, "Findings Detail")

        # Tab 3: Raw Text Report
        self.raw_text = QtWidgets.QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.tabs.addTab(self.raw_text, "Text Report")

        # Tab 4: Structured JSON
        self.raw_json = QtWidgets.QPlainTextEdit()
        self.raw_json.setReadOnly(True)
        self.tabs.addTab(self.raw_json, "JSON")

        return self.tabs

    def _build_footer(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)
        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setObjectName("MutedLabel")
        row.addWidget(self.status_label, 1)

        btn_copy_txt = QtWidgets.QPushButton("Copy Text")
        btn_copy_txt.clicked.connect(lambda: self._copy_to_clipboard(format_copy_text(self._snapshot), "report text"))
        btn_copy_json = QtWidgets.QPushButton("Copy JSON")
        btn_copy_json.clicked.connect(lambda: self._copy_to_clipboard(format_copy_json(self._snapshot), "report JSON"))
        row.addWidget(btn_copy_txt)
        row.addWidget(btn_copy_json)
        return bar

    # ---- Houdini 22 Python Panel lifecycle ----------------------------------------

    def on_activate(self, pane_tab=None) -> None:
        if pane_tab is not None:
            self._pane_tab = pane_tab
            if hasattr(pane_tab, "currentNode"):
                current = pane_tab.currentNode()
                if _is_lop_node(current) and current != self._node:
                    self.set_target_node(current, auto_check=False)

    def on_deactivate(self) -> None:
        pass

    def on_destroy(self) -> None:
        self._node = None
        self._pane_tab = None

    def on_node_path_changed(self, node) -> None:
        if _is_lop_node(node):
            self.set_target_node(node, auto_check=False)

    # ---- Node binding & parm synchronization --------------------------------------

    def set_target_node(self, node, auto_check: bool = False) -> None:
        self._node = node if _is_lop_node(node) else None
        if self._node is None:
            self.node_label.setText("No LOP node selected")
            self.btn_export.setEnabled(True)
            return

        is_we = _is_web_export_node(self._node)
        kind_tag = "Web Export LOP" if is_we else f"LOP ({self._node.type().name()})"
        self.node_label.setText(f"{self._node.path()}  [{kind_tag}]")
        if is_we:
            self._load_from_node_parms(self._node)
        if auto_check:
            self.run_check()

    def use_selected_node(self, auto_check: bool = True) -> None:
        hou = _try_hou()
        if hou is None:
            return
        selected = [n for n in hou.selectedNodes() if _is_lop_node(n)]
        target = next((n for n in selected if _is_web_export_node(n)), None)
        if target is None and selected:
            target = selected[0]
        if target is None:
            stage = hou.node("/stage")
            if stage is not None and hasattr(stage, "displayNode"):
                disp = stage.displayNode()
                if _is_lop_node(disp):
                    target = disp
        if target is not None:
            self.set_target_node(target, auto_check=auto_check)
        elif auto_check:
            self._set_status("Select a LOP node in /stage first.")

    def _load_from_node_parms(self, node) -> None:
        self._syncing_parms = True
        try:
            parm_map = {"usd_folder": "fmt_usd", "gltf": "fmt_gltf", "x3d": "fmt_x3d", "usdz": "fmt_usdz"}
            for fmt, pname in parm_map.items():
                p = node.parm(pname)
                if p is not None:
                    self._fmt_checks[fmt].setChecked(bool(p.eval()))
            p_out = node.parm("outdir")
            if p_out is not None:
                self.outdir_edit.setText(p_out.unexpandedString() if hasattr(p_out, "unexpandedString") else p_out.evalAsString())
            for attr, pname in (
                (self.ws_title, "web_title"),
                (self.ws_camera, "web_camera"),
                (self.ws_hudvs, "web_hudvs"),
            ):
                p = node.parm(pname)
                if p is not None:
                    attr.setText(p.evalAsString())
            for cb, pname in (
                (self.ws_hudvs_limit, "web_hudvs_limit"),
                (self.ws_showproxy, "web_showproxy"),
                (self.ws_showhud, "web_showhud"),
            ):
                p = node.parm(pname)
                if p is not None:
                    cb.setChecked(bool(p.eval()))
            for sp, pname in ((self.ws_bgblur, "web_bgblur"), (self.ws_exposure, "web_exposure")):
                p = node.parm(pname)
                if p is not None:
                    sp.setValue(float(p.eval()))
        finally:
            self._syncing_parms = False

    def _push_to_node_parms(self, node) -> None:
        if node is None or not _is_web_export_node(node):
            return
        parm_map = {"usd_folder": "fmt_usd", "gltf": "fmt_gltf", "x3d": "fmt_x3d", "usdz": "fmt_usdz"}
        for fmt, pname in parm_map.items():
            p = node.parm(pname)
            if p is not None:
                p.set(1 if self._fmt_checks[fmt].isChecked() else 0)
        for pname, val in (
            ("outdir", self.outdir_edit.text().strip() or "$HIP/web_export"),
            ("web_title", self.ws_title.text()),
            ("web_camera", self.ws_camera.text()),
            ("web_hudvs", self.ws_hudvs.text()),
            ("web_hudvs_limit", 1 if self.ws_hudvs_limit.isChecked() else 0),
            ("web_showproxy", 1 if self.ws_showproxy.isChecked() else 0),
            ("web_showhud", 1 if self.ws_showhud.isChecked() else 0),
            ("web_bgblur", float(self.ws_bgblur.value())),
            ("web_exposure", float(self.ws_exposure.value())),
        ):
            p = node.parm(pname)
            if p is not None:
                p.set(val)

    def _on_controls_changed(self) -> None:
        if self._syncing_parms:
            return
        self._push_to_node_parms(self._node)

    def _selected_formats(self) -> list[str]:
        chosen = [fmt for fmt in api.FORMATS if self._fmt_checks[fmt].isChecked()]
        return chosen or ["usd_folder"]

    def _current_settings(self) -> WebSettings:
        if self.ws_hudvs_limit.isChecked():
            raw = self.ws_hudvs.text()
            hud_sets = [s.strip() for s in raw.replace(",", " ").split() if s.strip()] or None
        else:
            hud_sets = None
        return WebSettings(
            title=self.ws_title.text().strip() or None,
            camera=self.ws_camera.text().strip() or None,
            hudVariantSets=hud_sets,
            showProxy=self.ws_showproxy.isChecked(),
            backgroundBlur=float(self.ws_bgblur.value()),
            exposureOffset=float(self.ws_exposure.value()),
            showHud=self.ws_showhud.isChecked(),
        )

    def _resolved_outdir(self) -> str:
        raw = self.outdir_edit.text().strip() or "$HIP/web_export"
        hou = _try_hou()
        if hou is not None and hasattr(hou, "text") and hasattr(hou.text, "expandString"):
            return hou.text.expandString(raw)
        return os.path.expandvars(raw)

    def _active_stage(self):
        """Read input 0 for a Web Export LOP (never self.stage()), else node.stage()."""
        if self._node is None:
            return None
        if _is_web_export_node(self._node):
            from .. import hou_node
            return hou_node.incoming_stage(self._node)
        return self._node.stage()

    # ---- Core actions -------------------------------------------------------------

    def add_web_export_node(self) -> None:
        hou = _try_hou()
        if hou is None:
            self._set_status("Adding a Web Export node requires Houdini.")
            return
        try:
            import webexport_shelf
            node = webexport_shelf.add_web_export_node()
            if node is not None:
                self.set_target_node(node, auto_check=True)
        except Exception as exc:
            self._set_status(f"Could not create Web Export node: {exc}")

    def run_check(self) -> None:
        stage = self._active_stage()
        node_path = self._node.path() if self._node is not None else ""
        if stage is None:
            msg = ("Nothing is connected to input 0 of the Web Export node."
                   if _is_web_export_node(self._node)
                   else "Select a Solaris LOP node in /stage with a cooked stage.")
            self.render_snapshot(build_snapshot(node_path=node_path, error=msg))
            return

        formats = self._selected_formats()
        jev_on = self.cb_jev.isChecked()
        try:
            facts = gather(stage, track_types=web_census.UNTESTED_TYPES)
            reports = api.check(stage, formats, jev_on=jev_on)
            if _is_web_export_node(self._node):
                from .. import hou_node
                hou_node.publish(self._node, reports)
                hou_node._refresh_badge(self._node)
            snap = build_snapshot(
                node_path=node_path,
                is_web_export_node=_is_web_export_node(self._node),
                inv=facts.inventory,
                reports=reports,
                headline=f"Check complete for {len(reports)} format(s). Nothing was written.",
            )
            self.render_snapshot(snap)
        except Exception as exc:
            if _is_web_export_node(self._node):
                from .. import hou_node
                hou_node._publish_error(self._node, "Check", exc)
            self.render_snapshot(build_snapshot(node_path=node_path, error=str(exc)))

    def run_export(self) -> None:
        stage = self._active_stage()
        node_path = self._node.path() if self._node is not None else ""
        if stage is None:
            self.render_snapshot(build_snapshot(
                node_path=node_path,
                error="No Solaris stage available to export. Select a wired LOP node first.",
            ))
            return

        self._push_to_node_parms(self._node)
        out_dir = self._resolved_outdir()
        formats = self._selected_formats()
        settings = self._current_settings()
        jev_on = self.cb_jev.isChecked()
        try:
            facts = gather(stage, track_types=web_census.UNTESTED_TYPES)
            results = api.export(stage, formats, out_dir, settings, jev_on=jev_on)
            wrote = [r for r in results if r.files]
            headline = (
                f"Exported {sum(len(r.files) for r in wrote)} file(s) to {out_dir}."
                if wrote else "Nothing was written (see FIX FIRST findings below)."
            )
            if _is_web_export_node(self._node):
                from .. import hou_node
                hou_node.publish(self._node, [r.report for r in results], headline)
                hou_node._refresh_badge(self._node)
            snap = build_snapshot(
                node_path=node_path,
                is_web_export_node=_is_web_export_node(self._node),
                inv=facts.inventory,
                reports=[r.report for r in results],
                results=results,
                headline=headline,
            )
            self.render_snapshot(snap)
        except Exception as exc:
            if _is_web_export_node(self._node):
                from .. import hou_node
                hou_node._publish_error(self._node, "Export", exc)
            self.render_snapshot(build_snapshot(node_path=node_path, error=str(exc)))

    def run_preview(self) -> None:
        try:
            from .. import hou_node
            if _is_web_export_node(self._node):
                hou_node.on_preview({"node": self._node})
                self._set_status(f"Preview launched for {self._node.path()}.")
                return

            outdir = self._resolved_outdir()

            class _OutDirParm:
                def evalAsString(self):
                    return outdir

            class _ShimNode:
                def parm(self, name):
                    return _OutDirParm() if name == "outdir" else None

            hou_node.on_preview({"node": _ShimNode()})
            self._set_status(f"Preview launched (output folder: {outdir}).")
        except Exception as exc:
            self._set_status(f"Preview failed: {exc}")

    # ---- Rendering snapshot -------------------------------------------------------

    def render_snapshot(self, snapshot: PanelSnapshot) -> None:
        """Render a PanelSnapshot across all cards, matrix, findings tree, and raw views."""
        self._snapshot = snapshot
        fg, bg = VERDICT_STYLE.get(snapshot.overall_verdict, VERDICT_STYLE["IDLE"])
        self.overall_pill.setText(snapshot.overall_verdict)
        _apply_pill_style(self.overall_pill, fg, bg)

        if snapshot.stage_summary is not None:
            self.facts_label.setText(snapshot.stage_summary.one_line())
        elif snapshot.error:
            self.facts_label.setText(f"Error: {snapshot.error}")
        else:
            self.facts_label.setText("No stage facts yet.")

        cards_by_fmt = {c.format: c for c in snapshot.cards}
        for fmt, widget in self._cards.items():
            widget.update_card(cards_by_fmt.get(fmt))
            widget.set_selected(self._active_format_filter == fmt)

        self._populate_matrix()
        self._populate_findings_tree()
        self.raw_text.setPlainText(format_copy_text(snapshot))
        self.raw_json.setPlainText(format_copy_json(snapshot))

        status = snapshot.error or snapshot.headline or "Ready."
        if snapshot.updated_at:
            status = f"{status}  ({snapshot.updated_at})"
        self._set_status(status)

    def _populate_matrix(self) -> None:
        reports = self._snapshot.reports
        fmt_cols = [r.format for r in reports] if reports else list(api.FORMATS)
        self.matrix_table.setColumnCount(1 + len(fmt_cols))
        self.matrix_table.setHorizontalHeaderLabels(
            ["Feature"] + [FORMAT_META.get(f, {"label": f})["label"] for f in fmt_cols]
        )
        rows = self._snapshot.matrix
        self.matrix_table.setRowCount(len(rows))
        for r_idx, row in enumerate(rows):
            feat_item = QtWidgets.QTableWidgetItem(row.feature)
            feat_item.setFlags(feat_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            self.matrix_table.setItem(r_idx, 0, feat_item)
            for c_idx, fmt in enumerate(fmt_cols, start=1):
                sev, text = row.cells.get(fmt, ("ok", "kept"))
                cell = QtWidgets.QTableWidgetItem(text)
                if sev == "ok":
                    cell.setForeground(QtGui.QColor(PALETTE["moss"]))
                else:
                    _, fg, bg = SEVERITY_STYLE[sev]
                    cell.setForeground(QtGui.QColor(fg))
                    cell.setBackground(QtGui.QColor(bg))
                cell.setToolTip(text)
                self.matrix_table.setItem(r_idx, c_idx, cell)
        header = self.matrix_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        for c_idx in range(1, 1 + len(fmt_cols)):
            header.setSectionResizeMode(c_idx, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.matrix_table.resizeRowsToContents()

    def _populate_findings_tree(self) -> None:
        self.findings_tree.clear()
        query = self.search_edit.text().strip().lower()
        fmt_filter = self._active_format_filter
        self.filter_label.setText(
            f"Showing: {FORMAT_META[fmt_filter]['label']}" if fmt_filter else "Showing: All formats"
        )

        grouped: dict[str, list[tuple[str, object]]] = {sev: [] for sev in SEVERITIES}
        for rep in self._snapshot.reports:
            if fmt_filter and rep.format != fmt_filter:
                continue
            for item in rep.items:
                haystack = " ".join([
                    rep.format, item.feature, item.becomes, item.fix or "", " ".join(item.examples)
                ]).lower()
                if query and query not in haystack:
                    continue
                grouped[item.severity].append((rep.format, item))

        for sev in SEVERITIES:
            entries = grouped[sev]
            if not entries:
                continue
            mark, fg, _ = SEVERITY_STYLE[sev]
            top = QtWidgets.QTreeWidgetItem([f"{mark}  {TITLES[sev]} ({len(entries)})", "", "", "", ""])
            top.setForeground(0, QtGui.QColor(fg))
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            self.findings_tree.addTopLevelItem(top)

            for fmt, item in entries:
                fmt_label = FORMAT_META.get(fmt, {"label": fmt})["label"]
                fix_or_ex = item.fix or (item.examples[0] if item.examples else "")
                row = QtWidgets.QTreeWidgetItem([
                    item.feature, fmt_label, str(item.count), item.becomes, fix_or_ex,
                ])
                row.setForeground(0, QtGui.QColor(fg))
                top.addChild(row)
                for ex in item.examples:
                    ex_row = QtWidgets.QTreeWidgetItem(["", "", "", "example", str(ex)])
                    ex_row.setData(4, QtCore.Qt.ItemDataRole.UserRole, str(ex))
                    ex_row.setForeground(4, QtGui.QColor(PALETTE["muted"]))
                    row.addChild(ex_row)
            top.setExpanded(True)

        for col in range(4):
            self.findings_tree.resizeColumnToContents(col)

    # ---- Interactive helpers ------------------------------------------------------

    def _on_card_clicked(self, fmt: str) -> None:
        self._active_format_filter = None if self._active_format_filter == fmt else fmt
        for f, widget in self._cards.items():
            widget.set_selected(self._active_format_filter == f)
        self._populate_findings_tree()
        self.tabs.setCurrentIndex(1)

    def _clear_format_filter(self) -> None:
        self._active_format_filter = None
        for widget in self._cards.values():
            widget.set_selected(False)
        self._populate_findings_tree()

    def _toggle_web_settings(self, checked: bool) -> None:
        self.ws_container.setVisible(checked)
        self.btn_settings_toggle.setText("/WebSettings ▾" if checked else "/WebSettings ▸")

    def _browse_outdir(self) -> None:
        start = self._resolved_outdir()
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Web Export Output Folder", start)
        if chosen:
            self.outdir_edit.setText(chosen)
            self._on_controls_changed()

    def _on_finding_double_clicked(self, item: QtWidgets.QTreeWidgetItem, _col: int) -> None:
        path = item.data(4, QtCore.Qt.ItemDataRole.UserRole)
        if not path:
            return
        self._copy_to_clipboard(str(path), f"path {path}")
        # If it is a USD prim path inside Solaris, select it in the Scene Graph.
        hou = _try_hou()
        if hou is not None and str(path).startswith("/") and hasattr(hou.ui, "curDesktop"):
            try:
                desktop = hou.ui.curDesktop()
                for tab in desktop.paneTabs():
                    if tab.type() == hou.paneTabType.SceneGraphTree and hasattr(tab, "setSelection"):
                        tab.setSelection([str(path)])
                        self._set_status(f"Selected {path} in Scene Graph (and copied to clipboard).")
                        return
            except Exception:
                pass

    def _copy_to_clipboard(self, text: str, label: str) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.clipboard().setText(text)
            self._set_status(f"Copied {label} to clipboard.")

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)


def create_panel(pane_tab=None) -> WebExportPanel:
    """Entry point called by houdini/python_panels/webexport.pypanel."""
    return WebExportPanel(pane_tab=pane_tab)


def open_floating_panel(interface_name: str = "web_export_panel") -> WebExportPanel | None:
    """Focus an existing Web Export Python Panel tab or open a floating Houdini Python Panel."""
    hou = _try_hou()
    if hou is None or not hasattr(hou, "ui") or not hasattr(hou.ui, "curDesktop"):
        return None
    desktop = hou.ui.curDesktop()
    if desktop is None:
        return None

    for tab in desktop.paneTabs():
        if tab.type() == hou.paneTabType.PythonPanel:
            iface = tab.activeInterface()
            if iface is not None and iface.name() == interface_name:
                tab.setIsCurrentTab()
                widget = tab.activeInterfaceRootWidget() if hasattr(tab, "activeInterfaceRootWidget") else None
                return widget

    panel = desktop.createFloatingPanel(hou.paneTabType.PythonPanel)
    panel.setSize((980, 720))
    panel.setName("Web Export")
    tab = panel.paneTabs()[0]
    if hasattr(tab, "showToolbar"):
        tab.showToolbar(False)
    iface = hou.pypanel.interfaces().get(interface_name)
    if iface is not None:
        tab.setActiveInterface(iface)
        return tab.activeInterfaceRootWidget() if hasattr(tab, "activeInterfaceRootWidget") else None
    return next(iter(_LIVE_PANELS), None)
