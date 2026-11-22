"""Houdini 22 shelf tool callbacks for Web Export (Solaris to three.js).

Copyright (c) 2026 Joseph O. Ibrahim. MIT.

Thin shell behind houdini/toolbar/webexport.shelf (blueprint principle 4). Every
export and loss-report decision lives in webexport.api and webexport.hou_node;
this module only locates LOP nodes, wires the HDA, and opens/drives the panel.
"""
from __future__ import annotations

from typing import Any

import hou

HDA_TYPE = "mythandlogic::web_export::1.0"
PANEL_INTERFACE = "web_export_panel"


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


def _status(message: str, severity=None) -> None:
    """Show a quiet status-bar message when the Houdini UI is running."""
    ui = getattr(hou, "ui", None)
    if ui is None or not hasattr(ui, "setStatusMessage"):
        return
    try:
        if severity is not None:
            ui.setStatusMessage(message, severity=severity)
        else:
            ui.setStatusMessage(message)
    except Exception:
        pass


def active_lop_network(kwargs: dict[str, Any] | None = None):
    """Best Solaris LOP network: current pane's LOP pwd, selected node's parent, or /stage."""
    kwargs = kwargs or {}
    pane = kwargs.get("pane")
    if pane is not None and hasattr(pane, "pwd"):
        pwd = pane.pwd()
        if pwd is not None and pwd.childTypeCategory().name() == "Lop":
            return pwd

    for node in hou.selectedNodes():
        if _is_lop_node(node):
            return node.parent()
        if node.childTypeCategory().name() == "Lop":
            return node

    ui = getattr(hou, "ui", None)
    if ui is not None and hasattr(ui, "paneTabOfType"):
        net_pane = ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if net_pane is not None and hasattr(net_pane, "pwd"):
            pwd = net_pane.pwd()
            if pwd is not None and pwd.childTypeCategory().name() == "Lop":
                return pwd

    stage = hou.node("/stage")
    if stage is not None and stage.childTypeCategory().name() == "Lop":
        return stage

    obj = hou.node("/obj")
    if obj is not None:
        return obj.createNode("lopnet", "stage")
    return None


def find_lop_node(kwargs: dict[str, Any] | None = None):
    """Selected LOP node, else the display-flag node in the active LOP network."""
    for node in hou.selectedNodes():
        if _is_lop_node(node):
            return node
    net = active_lop_network(kwargs)
    if net is not None and hasattr(net, "displayNode"):
        disp = net.displayNode()
        if _is_lop_node(disp):
            return disp
    return None


def find_web_export_node(kwargs: dict[str, Any] | None = None):
    """Selected Web Export LOP, else a Web Export LOP in the active LOP network."""
    for node in hou.selectedNodes():
        if _is_web_export_node(node):
            return node
    net = active_lop_network(kwargs)
    if net is None:
        return None
    disp = net.displayNode() if hasattr(net, "displayNode") else None
    if _is_web_export_node(disp):
        return disp
    for child in net.children():
        if _is_web_export_node(child):
            return child
    return None


def add_web_export_node(kwargs: dict[str, Any] | None = None):
    """Create a Web Export LOP in /stage (or active LOP net), wired below the current LOP."""
    net = active_lop_network(kwargs)
    if net is None:
        _status("Web Export: could not find or create a Solaris LOP network.",
                getattr(hou.severityType, "Error", None))
        return None

    upstream = None
    for node in hou.selectedNodes():
        if _is_lop_node(node) and node.parent() == net and not _is_web_export_node(node):
            upstream = node
            break
    if upstream is None and hasattr(net, "displayNode"):
        disp = net.displayNode()
        if _is_lop_node(disp) and not _is_web_export_node(disp):
            upstream = disp

    try:
        export_node = net.createNode(HDA_TYPE, "web_export1")
    except Exception as exc:
        _status(f"Web Export: HDA {HDA_TYPE} is not installed ({exc}).",
                getattr(hou.severityType, "Error", None))
        return None

    if upstream is not None:
        export_node.setInput(0, upstream)
        if hasattr(export_node, "moveToGoodPosition"):
            export_node.moveToGoodPosition()
    elif hasattr(net, "layoutChildren"):
        net.layoutChildren([export_node])

    if hasattr(export_node, "setDisplayFlag"):
        export_node.setDisplayFlag(True)
    if hasattr(export_node, "setCurrent"):
        export_node.setCurrent(True, clear_all_selected=True)

    _status(f"Web Export: created {export_node.path()}"
            + (f" wired from {upstream.name()}." if upstream is not None else "."))
    return export_node


def open_report_panel(kwargs: dict[str, Any] | None = None, node=None, run_check: bool = False):
    """Focus an open Web Export Python Panel or open it in a floating window."""
    from webexport.panel import open_floating_panel

    target = node or find_web_export_node(kwargs) or find_lop_node(kwargs)
    panel_widget = open_floating_panel(interface_name=PANEL_INTERFACE)
    if panel_widget is not None and target is not None:
        if hasattr(panel_widget, "set_target_node"):
            panel_widget.set_target_node(target, auto_check=run_check)
    elif panel_widget is None:
        _status("Web Export: Python Panel requires the Houdini GUI session.",
                getattr(hou.severityType, "Warning", None))
    return panel_widget


def check_stage(kwargs: dict[str, Any] | None = None):
    """Run Check on the active Web Export LOP (never writes files) and show the report panel."""
    from webexport import hou_node

    node = find_web_export_node(kwargs)
    if node is not None:
        hou_node.on_check({"node": node})
        verdict = node.parm("check_message").evalAsString() if node.parm("check_message") else ""
        _status(f"Web Export Check: {verdict or 'GO'} ({node.path()}).")
        open_report_panel(kwargs, node=node, run_check=True)
        return node

    lop = find_lop_node(kwargs)
    if lop is None:
        _status("Web Export Check: select a LOP node in /stage first.",
                getattr(hou.severityType, "Warning", None))
        return None

    open_report_panel(kwargs, node=lop, run_check=True)
    _status(f"Web Export Check: inspecting {lop.path()} in the Report Panel.")
    return lop


def export_stage(kwargs: dict[str, Any] | None = None):
    """Run Export on the active Web Export LOP (report-first: writes nothing on FIX FIRST)."""
    from webexport import hou_node

    node = find_web_export_node(kwargs)
    if node is None:
        _status("Web Export: no Web Export LOP found. Add one with 'Add Web Export' first.",
                getattr(hou.severityType, "Warning", None))
        return None

    hou_node.on_export({"node": node})
    msg = node.parm("check_message").evalAsString() if node.parm("check_message") else ""
    _status(f"Web Export: finished on {node.path()}" + (f" ({msg})." if msg else "."))
    open_report_panel(kwargs, node=node, run_check=False)
    return node


def preview_viewer(kwargs: dict[str, Any] | None = None):
    """Start the local viewer server (if free) and open the browser to the exported stage."""
    from webexport import hou_node

    node = find_web_export_node(kwargs)
    if node is not None:
        hou_node.on_preview({"node": node})
        _status(f"Web Export Preview: opened viewer for {node.path()}.")
        return node

    # Fallback when no Web Export node exists yet: open the viewer root via a minimal shim.
    class _RootShim:
        def parm(self, _name):
            return None

    hou_node.on_preview({"node": _RootShim()})
    _status("Web Export Preview: opened viewer server.")
    return None
