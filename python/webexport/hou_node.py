"""hou_node.py: the only module in webexport that imports hou.

The Web Export HDA's buttons call these functions, and nothing else lives here. Every
decision about what an export means is in webexport.api (blueprint principles 3 and 4).
The functions read the node's parameters, call the API, and write the answer back into
the node's own parameters, so the report is data first and text second (principle 5).

    on_check(kwargs)    Check: the report only. Writes nothing, ever.
    on_export(kwargs)   Export: the report, then the folder when nothing blocks.
    on_preview(kwargs)  Preview: start the repo's server on a free port and open it.

The in-chain badge reads three hidden parameters this module writes: `check_verdict`
(0 GO, 1 GO WITH NOTES, 2 FIX FIRST), `check_message` (the headline) and `check_enable`.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import webbrowser

import hou  # the one place this is allowed

PORT = int(os.environ.get("WEBEXPORT_PORT", "5180"))  # 5180 is the repo default; tests use their own
VERDICT_CODE = {"GO": 0, "GO, WITH NOTES": 1, "FIX FIRST": 2}


def _repo_root(node) -> str:
    """WEBEXPORT_ROOT from the package, the node's own module path, or the installed copy."""
    root = hou.getenv("WEBEXPORT_ROOT")
    if root:
        return root
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../python
    return os.path.dirname(here)


def incoming_stage(node):
    """The stage on input 0. Read it there, never from node.stage(): the badge can error the asset."""
    conns = node.inputConnections()
    if not conns:
        return None
    c = conns[0]
    return c.inputNode().stage(output_index=c.outputIndex())


def web_settings(node):
    """The seven /WebSettings values, as the core's WebSettings."""
    from webexport.settings import WebSettings
    limit_parm = node.parm("web_hudvs_limit")
    if limit_parm is not None and not limit_parm.eval():
        hud_sets = None
    else:
        hub = node.parm("web_hudvs")
        raw = hub.evalAsString() if hub else ""
        hud_sets = [s.strip() for s in raw.replace(",", " ").split()] or None
    return WebSettings(
        title=(node.parm("web_title").evalAsString() or None) if node.parm("web_title") else None,
        camera=(node.parm("web_camera").evalAsString() or None) if node.parm("web_camera") else None,
        hudVariantSets=hud_sets,
        showProxy=bool(node.parm("web_showproxy").eval()) if node.parm("web_showproxy") else False,
        backgroundBlur=float(node.parm("web_bgblur").eval()) if node.parm("web_bgblur") else 0.35,
        exposureOffset=float(node.parm("web_exposure").eval()) if node.parm("web_exposure") else 0.0,
        showHud=bool(node.parm("web_showhud").eval()) if node.parm("web_showhud") else True,
    )


def _put(node, parm_name, value):
    p = node.parmparent().parm(parm_name) if hasattr(node, "parmparent") else None
    p = p or node.parm(parm_name)
    if p:
        p.set(value)


def publish(node, reports, headline=""):
    """Write the report text and the hidden verdict the badge reads."""
    text = "\n\n".join(r.render_text() for r in reports) if reports else "No stage to report on."
    if headline:
        text = headline + "\n\n" + text
    _put(node, "report", text)
    worst = max((VERDICT_CODE.get(r.verdict, 0) for r in reports), default=0)
    _put(node, "check_verdict", worst)
    _put(node, "check_message", next((f"{r.format}: {r.verdict}" for r in reports
                                      if VERDICT_CODE.get(r.verdict, 0) == worst), ""))
    _put(node, "check_enable", 1 if worst else 0)


# the asset's format toggles, and the formats the core can write today
FORMAT_PARMS = {"usd_folder": "fmt_usd", "gltf": "fmt_gltf", "x3d": "fmt_x3d", "usdz": "fmt_usdz"}


def formats_for(node) -> list[str]:
    """The formats whose toggles are on, in the core's order. At least the USD folder."""
    from webexport.api import FORMATS
    chosen = [fmt for fmt in FORMATS
              if node.parm(FORMAT_PARMS[fmt]) and node.parm(FORMAT_PARMS[fmt]).eval()]
    return chosen or ["usd_folder"]


def _refresh_badge(node):
    """Cook once, so the in-chain badge reports now rather than at some later cook."""
    try:
        node.cook(force=True)
    except hou.OperationFailed:
        pass  # the badge failing the cook with FIX FIRST is the point


def _out_dir(node) -> str:
    """Resolved export directory. evalAsString() already expands Houdini variables."""
    parm = node.parm("outdir")
    if parm is not None:
        return parm.evalAsString()
    return hou.text.expandString("$HIP/web_export")


def _publish_error(node, action: str, exc: Exception):
    """Record an unexpected failure loudly on the node so a stale GO verdict never remains."""
    msg = f"Error: {exc}"
    _put(node, "report", f"{action}: {msg}")
    _put(node, "check_verdict", VERDICT_CODE["FIX FIRST"])
    _put(node, "check_message", msg)
    _put(node, "check_enable", 1)
    _refresh_badge(node)


def on_check(kwargs):
    """The report only. This function never writes a file."""
    from webexport import api
    node = kwargs["node"]
    stage = incoming_stage(node)
    if stage is None:
        publish(node, [], "Check: nothing is connected to the input.")
        return
    try:
        reports = api.check(stage, formats_for(node))
    except Exception as exc:
        _publish_error(node, "Check", exc)
        return
    publish(node, reports)
    _refresh_badge(node)


def on_export(kwargs):
    """Write the folder when nothing blocks, then show the result in the same report."""
    from webexport import api
    node = kwargs["node"]
    stage = incoming_stage(node)
    if stage is None:
        publish(node, [], "Export: nothing is connected to the input.")
        return
    out_dir = _out_dir(node)
    try:
        results = api.export(stage, formats_for(node), out_dir, web_settings(node))
    except Exception as exc:
        _publish_error(node, "Export", exc)
        return
    wrote = [r for r in results if r.files]
    headline = (f"Export: wrote {sum(len(r.files) for r in wrote)} file(s) to {out_dir}."
                if wrote else "Export: nothing was written (see the report).")
    publish(node, [r.report for r in results], headline)
    _refresh_badge(node)


def port_free(port=PORT) -> bool:
    """Bind, not connect: connect waits two seconds on a refused port on this machine."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def badge(node):
    """The in-chain Python Script LOP's cook: render the hidden verdict as a node message.

    FIX FIRST raises hou.NodeError, which is how Houdini fails a cook with a clean
    message, so a blocking export is loud on the asset itself. GO, WITH NOTES raises
    hou.NodeWarning. GO says nothing. The code lives here, not in the HDA (principle 4).
    """
    parent = node.parent()
    code = parent.parm("check_verdict").eval() if parent.parm("check_verdict") else 0
    message = parent.parm("check_message").evalAsString() if parent.parm("check_message") else ""
    if code >= VERDICT_CODE["FIX FIRST"]:
        raise hou.NodeError(message or "FIX FIRST: this stage would not load, or would look wrong, on the web.")
    if code == VERDICT_CODE["GO, WITH NOTES"]:
        raise hou.NodeWarning(message or "GO, WITH NOTES: the web export will look different from Karma.")


def _preview_url_for(node, root: str) -> str:
    """Deep-link to the exported USD entry under <repo>/stages/, else the viewer root."""
    base = f"http://127.0.0.1:{PORT}/"
    out_dir = _out_dir(node)
    if not out_dir or not os.path.isdir(out_dir):
        return base
    order = (".usda", ".usdc", ".usd")
    entries = sorted(
        (p for p in Path(out_dir).iterdir() if p.is_file() and p.suffix.lower() in order),
        key=lambda p: (order.index(p.suffix.lower()), p.name),
    )
    if not entries:
        return base
    try:
        from webexport import api
        url = api.preview_url(entries[0])
        if url:
            return url.replace(f":{api.PORT}/", f":{PORT}/", 1)
    except ImportError:
        pass
    try:
        rel = entries[0].resolve().relative_to((Path(root) / "stages").resolve())
        return f"{base}?stage=/stages/{rel.as_posix()}"
    except ValueError:
        return base


def on_preview(kwargs):
    """Start the viewer server if the port is free, then open it. Never blocks Houdini."""
    node = kwargs["node"]
    root = _repo_root(node)
    server = os.path.join(root, "server", "serve.mjs")
    no_browser = bool(kwargs.get("webexport_no_browser")) or os.environ.get("WEBEXPORT_NO_BROWSER") == "1"
    url = _preview_url_for(node, root)
    if not port_free(PORT):
        if not no_browser:
            webbrowser.open(url)
        _put(node, "report", f"Preview: {url} is already being served.")
        return
    if not os.path.exists(server):
        _put(node, "report", f"Preview: no server at {server}. Set WEBEXPORT_ROOT to the repo.")
        return
    argv = node_and_module_argv()
    if not (shutil.which(argv[0]) or os.path.exists(argv[0])):
        _put(node, "report", f"Preview: Node.js ({argv[0]}) was not found. Install Node.js 22+ or add it to PATH.")
        return
    env = dict(os.environ, PORT=str(PORT))  # serve.mjs reads PORT; WEBEXPORT_PORT only picks it here
    popen_kwargs = dict(
        cwd=root, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen([argv[0], server], **popen_kwargs)
    if not no_browser:
        webbrowser.open(url)
    _put(node, "report", f"Preview: started {server} on {url}.")


def node_and_module_argv():
    """node beside Houdini's own runtime, so Preview does not depend on PATH."""
    name = "node.exe" if sys.platform == "win32" else "node"
    exe = os.path.join(os.path.dirname(sys.executable), name)
    return [exe if os.path.exists(exe) else "node"]
