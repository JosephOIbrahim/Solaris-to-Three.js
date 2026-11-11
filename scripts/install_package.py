#!/usr/bin/env python
"""Install the Web Export Houdini package into your Houdini user prefs.

Writes a resolved <prefs>/packages/webexport.json with absolute paths that
point at THIS repo, so ``import webexport`` works inside Houdini.

Adapted from SYNAPSE scripts/install_synapse_package.py (source commit
36148779, MIT licence; that file's own line reads "Copyright (c) 2025-2026
Joseph Ibrahim", quoted here exactly as it appears). The pref-dir rules,
dry-run flow and read-only verify follow that file. This repository's own
code is Copyright (c) 2026 Joseph O. Ibrahim, MIT.

Usage:
    python scripts/install_package.py --dry-run              # show, write nothing
    python scripts/install_package.py                        # install
    python scripts/install_package.py --verify               # read-only check, exit 0 or 1
    python scripts/install_package.py --prefs-dir D:/houdini22.0

Prefs folder: --prefs-dir, else $HOUDINI_USER_PREF_DIR, else
<Documents>/houdini22.0 (the real Documents folder, OneDrive-redirected or not).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PACKAGE_NAME = "webexport"
HOUDINI_PREFS_NAME = "houdini22.0"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def documents_dir() -> Path:
    """The real Documents folder. On Windows this asks the shell, so a
    OneDrive-redirected Documents is found; elsewhere it is ~/Documents."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD),
                            ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            guid = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                        (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            res = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(out))
            if res == 0 and out.value:
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return path
        except Exception:
            pass
    return Path.home() / "Documents"


def default_prefs_dir() -> Path:
    env = os.environ.get("HOUDINI_USER_PREF_DIR", "").strip()
    if env:
        return Path(env.replace("__HVER__", "22.0"))
    return documents_dir() / HOUDINI_PREFS_NAME


def resolve_prefs_dir(arg: str | None) -> Path:
    return Path(arg) if arg else default_prefs_dir()


def target_path(prefs_dir: Path) -> Path:
    return prefs_dir / "packages" / f"{PACKAGE_NAME}.json"


def build_package(root: Path) -> dict:
    """The tracked packages/webexport.json with every path made absolute."""
    pkg = json.loads((root / "packages" / f"{PACKAGE_NAME}.json").read_text(encoding="utf-8"))
    pkg["comment"] = (f"Resolved copy written by scripts/install_package.py for {root.as_posix()}. "
                      "Re-run the installer if the repo moves.")
    pkg["env"] = [
        {"var": "WEBEXPORT_ROOT", "value": root.as_posix()},
        {"var": "PYTHONPATH", "value": (root / "python").as_posix(), "method": "prepend"},
    ]
    pkg["hpath"] = (root / "houdini").as_posix()
    return pkg


def render(pkg: dict) -> str:
    return json.dumps(pkg, indent=4) + "\n"


def verify_target(target: Path, root: Path) -> list[str]:
    """Return a list of problems; empty means the install is good. Writes nothing."""
    if not target.is_file():
        return [f"not installed: {target.as_posix()} does not exist"]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"{target.as_posix()} does not parse as JSON: {exc}"]
    if not isinstance(data, dict) or data.get("name") != PACKAGE_NAME:
        return [f"{target.as_posix()} is not the {PACKAGE_NAME} package"]
    problems: list[str] = []
    env = {e.get("var"): e for e in data.get("env", []) if isinstance(e, dict)}
    we = env.get("WEBEXPORT_ROOT", {}).get("value")
    if not isinstance(we, str):
        return problems + ["WEBEXPORT_ROOT is not set"]
    if Path(we).resolve() != root.resolve():
        problems.append(f"WEBEXPORT_ROOT is {we}, but this repo is {root.as_posix()}")
    pp = env.get("PYTHONPATH", {}).get("value")
    pp_list = pp if isinstance(pp, list) else [pp]
    want = (Path(we) / "python").resolve()
    if not any(isinstance(p, str) and Path(p).resolve() == want for p in pp_list):
        problems.append(f"PYTHONPATH does not include {want.as_posix()}")
    if not (Path(we) / "python" / "webexport").is_dir():
        problems.append(f"{(Path(we) / 'python' / 'webexport').as_posix()} does not exist")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Install the Web Export Houdini package.")
    ap.add_argument("--prefs-dir", help="Houdini user prefs folder (e.g. .../houdini22.0).")
    ap.add_argument("--dry-run", action="store_true", help="Print the target path and JSON; write nothing.")
    ap.add_argument("--verify", action="store_true", help="Read-only check of an installed copy; exit 0 or 1.")
    args = ap.parse_args(argv)

    root = repo_root()
    target = target_path(resolve_prefs_dir(args.prefs_dir))

    if args.verify:
        problems = verify_target(target, root)
        print(f"target : {target.as_posix()}")
        if problems:
            for p in problems:
                print(f"FAIL   : {p}")
            return 1
        print("OK     : package parses, points at this repo, webexport is importable from it")
        return 0

    text = render(build_package(root))
    print(f"target : {target.as_posix()}")
    print(text, end="")
    if args.dry_run:
        print("(dry run, nothing written)")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote  : {target.as_posix()}")
    print("Restart Houdini to load the package.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
