#!/usr/bin/env python
"""Preflight diagnostics for launching Solaris-to-Three.js from Houdini 22.

Copyright (c) 2026 Joseph O. Ibrahim. MIT.

Usage:
    python scripts/doctor.py                    # human-readable preflight report
    python scripts/doctor.py --json             # machine-readable JSON output
    python scripts/doctor.py --prefs-dir <dir>  # check a custom Houdini prefs folder

Exit code 0 when all critical launch checks pass; exit code 1 otherwise.
Runs under plain Python 3.12+ or Houdini 22's hython.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import install_package  # noqa: E402

REQUIRED_HOUDINI_ASSETS = (
    "houdini/otls/web_export.hda",
    "houdini/toolbar/webexport.shelf",
    "houdini/python_panels/webexport.pypanel",
    "houdini/MainMenuCommon.xml",
    "houdini/config/Icons/webexport.svg",
    "houdini/scripts/python/webexport_shelf.py",
)

XML_ASSETS = (
    "houdini/toolbar/webexport.shelf",
    "houdini/python_panels/webexport.pypanel",
    "houdini/MainMenuCommon.xml",
    "houdini/config/Icons/webexport.svg",
)


def _check_python() -> dict:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    return {
        "name": "python",
        "ok": ok,
        "severity": "error" if not ok else "ok",
        "detail": f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})",
        "fix": None if ok else "Use Python 3.11+ (3.12 recommended) or Houdini 22 hython.",
    }


def _check_pxr() -> dict:
    try:
        from pxr import Usd
        ver = Usd.GetVersion()
        return {
            "name": "openusd",
            "ok": True,
            "severity": "ok",
            "detail": f"OpenUSD {ver[0]}.{ver[1]:02d}.{ver[2]} (pxr importable)",
            "fix": None,
        }
    except Exception as exc:
        return {
            "name": "openusd",
            "ok": False,
            "severity": "error",
            "detail": f"pxr not importable ({exc})",
            "fix": 'Install OpenUSD: pip install "usd-core==26.5", or run under Houdini 22 hython.',
        }


def _check_node() -> dict:
    exe = shutil.which("node")
    if not exe:
        return {
            "name": "node",
            "ok": False,
            "severity": "error",
            "detail": "node not found on PATH",
            "fix": "Install Node.js 22+ and ensure 'node' is on PATH for the Preview server.",
        }
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
        raw = out.stdout.strip()
        major = int(raw.lstrip("v").split(".", 1)[0])
        ok = major >= 22
        return {
            "name": "node",
            "ok": ok,
            "severity": "ok" if ok else "error",
            "detail": f"Node.js {raw} ({exe})",
            "fix": None if ok else "Upgrade Node.js to v22+ (see .nvmrc).",
        }
    except Exception as exc:
        return {
            "name": "node",
            "ok": False,
            "severity": "error",
            "detail": f"could not query node version ({exc})",
            "fix": "Reinstall Node.js 22+.",
        }


def _check_viewer_deps(root: Path) -> dict:
    has_three = (root / "node_modules" / "three" / "build" / "three.module.js").is_file()
    has_needle = (root / "node_modules" / "@needle-tools" / "usd" / "package.json").is_file()
    if has_three and has_needle:
        return {
            "name": "viewer_deps",
            "ok": True,
            "severity": "ok",
            "detail": "three.js + @needle-tools/usd installed in node_modules (all viewer routes ready)",
            "fix": None,
        }
    if has_three:
        return {
            "name": "viewer_deps",
            "ok": True,
            "severity": "warn",
            "detail": "three.js installed (MIT glTF/X3D/?route=js ready; Needle Hydra route not installed)",
            "fix": "Run `npm ci` in the repo root if you want the local Needle Hydra WASM route.",
        }
    return {
        "name": "viewer_deps",
        "ok": False,
        "severity": "warn",
        "detail": "node_modules not installed (Houdini export works, local browser preview needs npm ci)",
        "fix": f"Run `npm ci` in {root.as_posix()}.",
    }


def _check_houdini_assets(root: Path) -> dict:
    missing = [rel for rel in REQUIRED_HOUDINI_ASSETS if not (root / rel).is_file()]
    bad_xml = []
    for rel in XML_ASSETS:
        p = root / rel
        if p.is_file():
            try:
                ET.parse(p)
            except ET.ParseError as exc:
                bad_xml.append(f"{rel}: {exc}")
    if missing or bad_xml:
        parts = []
        if missing:
            parts.append("missing: " + ", ".join(missing))
        if bad_xml:
            parts.append("invalid XML: " + "; ".join(bad_xml))
        return {
            "name": "houdini_assets",
            "ok": False,
            "severity": "error",
            "detail": "; ".join(parts),
            "fix": "Restore or rebuild the missing Houdini assets from the repo.",
        }
    return {
        "name": "houdini_assets",
        "ok": True,
        "severity": "ok",
        "detail": f"All {len(REQUIRED_HOUDINI_ASSETS)} Houdini 22 assets present (HDA, shelf, panel, menu, icon, callbacks)",
        "fix": None,
    }


def _check_package_install(root: Path, prefs_dir: Path | None) -> dict:
    prefs = prefs_dir if prefs_dir is not None else install_package.default_prefs_dir()
    target = install_package.target_path(prefs)
    problems = install_package.verify_target(target, root)
    if problems:
        return {
            "name": "houdini_package",
            "ok": False,
            "severity": "error",
            "detail": f"{target.as_posix()}: " + "; ".join(problems),
            "fix": "Run `python scripts/install_package.py` and restart Houdini 22.",
        }
    return {
        "name": "houdini_package",
        "ok": True,
        "severity": "ok",
        "detail": f"Installed and verified: {target.as_posix()}",
        "fix": None,
    }


def _check_port() -> dict:
    port = int(os.environ.get("WEBEXPORT_PORT", "5180"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            state = f"port {port} is free (Preview will start server/serve.mjs automatically)"
        except OSError:
            state = f"port {port} is already serving (Preview will reuse http://127.0.0.1:{port}/)"
    return {
        "name": "preview_port",
        "ok": True,
        "severity": "ok",
        "detail": state,
        "fix": None,
    }


def run_checks(root: Path | None = None, prefs_dir: Path | None = None) -> dict:
    """Run all preflight checks and return a structured report dict."""
    repo = (root or install_package.repo_root()).resolve()
    version = "unknown"
    pkg_json = repo / "package.json"
    if pkg_json.is_file():
        try:
            version = json.loads(pkg_json.read_text(encoding="utf-8")).get("version", "unknown")
        except Exception:
            pass
    checks = [
        _check_python(),
        _check_pxr(),
        _check_node(),
        _check_houdini_assets(repo),
        _check_package_install(repo, prefs_dir),
        _check_viewer_deps(repo),
        _check_port(),
    ]
    hard_fail = any(c["severity"] == "error" for c in checks)
    return {
        "repo": repo.as_posix(),
        "version": version,
        "ok": not hard_fail,
        "checks": checks,
    }


def render_report(report: dict) -> str:
    lines = [
        f"WEB EXPORT PREFLIGHT   v{report['version']}",
        "-" * 72,
        f"Status: {'READY FOR HOUDINI 22' if report['ok'] else 'FIX ITEMS BELOW'}",
        f"Repo:   {report['repo']}",
        "",
    ]
    marks = {"ok": "ok  ", "warn": "warn", "error": "FAIL"}
    for c in report["checks"]:
        lines.append(f"  {marks.get(c['severity'], 'info')}  {c['name']:<16} {c['detail']}")
        if c.get("fix"):
            lines.append(f"        fix: {c['fix']}")
    lines.append("-" * 72)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Preflight check for launching Solaris-to-Three.js from Houdini 22.")
    ap.add_argument("--prefs-dir", default=None, help="Houdini 22 user prefs folder to verify.")
    ap.add_argument("--json", action="store_true", help="Print JSON instead of human-readable text.")
    ap.add_argument("--strict", action="store_true", help="Treat warnings (such as missing node_modules) as failures.")
    args = ap.parse_args(argv)

    prefs = Path(args.prefs_dir) if args.prefs_dir else None
    report = run_checks(prefs_dir=prefs)
    if args.strict and any(c["severity"] != "ok" for c in report["checks"]):
        report["ok"] = False

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
