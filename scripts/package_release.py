#!/usr/bin/env python
"""Build a deterministic, studio-deployable Houdini 22 release bundle + SHA256SUMS.txt.

Copyright (c) 2026 Joseph O. Ibrahim. MIT.

Strictly excludes `node_modules/` (never bundles PolyForm Noncommercial `@needle-tools/*`),
`.git/`, `stages/`, `shots/`, `dist/`, `.claude/`, and bytecode caches.

Usage:
    python scripts/package_release.py [--out-dir dist]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

# Directories and files excluded from the portable release archive.
EXCLUDE_DIRS = {
    ".git",
    ".claude",
    "node_modules",
    "dist",
    "shots",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".vscode",
    ".idea",
}

EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log"}

REQUIRED_ARCHIVE_MEMBERS = (
    "package.json",
    "package-lock.json",
    "LICENSE",
    "README.md",
    "packages/webexport.json",
    "houdini/otls/web_export.hda",
    "houdini/toolbar/webexport.shelf",
    "houdini/python_panels/webexport.pypanel",
    "houdini/MainMenuCommon.xml",
    "houdini/config/Icons/webexport.svg",
    "houdini/scripts/python/webexport_shelf.py",
    "python/webexport/api.py",
    "python/webexport/hou_node.py",
    "python/webexport/panel/widget.py",
    "scripts/install_package.py",
    "scripts/doctor.py",
    "server/serve.mjs",
    "viewer/index.html",
    "viewer/main.js",
    "viewer/players/gltf.html",
    "viewer/players/x3d.html",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def collect_files(root: Path) -> list[Path]:
    """Deterministically collect all portable repository files to package."""
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        parts = rel.parts
        if any(p in EXCLUDE_DIRS for p in parts):
            continue
        if parts and parts[0] == "stages" and rel.as_posix() != "stages/.gitkeep":
            continue
        if path.is_dir():
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES or path.name.startswith(".env"):
            continue
        files.append(path)
    files.sort(key=lambda p: p.relative_to(root).as_posix())
    return files


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_bundle(root: Path | None = None, out_dir: Path | None = None) -> tuple[Path, Path, str]:
    """Create `<out_dir>/solaris-to-three-v<version>-houdini22.zip` and `SHA256SUMS.txt`."""
    repo = (root or repo_root()).resolve()
    version = json.loads((repo / "package.json").read_text(encoding="utf-8"))["version"]
    dest = (out_dir or (repo / "dist")).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    prefix = f"solaris-to-three-v{version}"
    zip_path = dest / f"{prefix}-houdini22.zip"
    sums_path = dest / "SHA256SUMS.txt"

    files = collect_files(repo)
    rel_set = {p.relative_to(repo).as_posix() for p in files}
    missing = [m for m in REQUIRED_ARCHIVE_MEMBERS if m not in rel_set]
    if missing:
        raise RuntimeError(f"Release bundle is missing required files: {missing}")

    forbidden = [m for m in rel_set if "node_modules/" in m or "@needle-tools" in m]
    if forbidden:
        raise RuntimeError(f"Refusing to bundle third-party/PolyForm files: {forbidden[:5]}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel_posix = path.relative_to(repo).as_posix()
            info = zipfile.ZipInfo(filename=f"{prefix}/{rel_posix}", date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())

    digest = sha256_file(zip_path)
    sums_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    return zip_path, sums_path, digest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Package a deterministic Houdini 22 release archive + SHA256SUMS.txt.")
    ap.add_argument("--out-dir", default="dist", help="Output directory for release artifacts (default: dist).")
    args = ap.parse_args(argv)

    root = repo_root()
    out_dir = Path(args.out_dir) if Path(args.out_dir).is_absolute() else root / args.out_dir
    zip_path, sums_path, digest = build_bundle(root=root, out_dir=out_dir)
    print(f"archive : {zip_path.as_posix()} ({zip_path.stat().st_size:,} bytes)")
    print(f"sha256  : {digest}")
    print(f"sums    : {sums_path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
