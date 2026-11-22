"""Textures: find the paths a layer authors and the files they mean, flag what no web format
can load, copy.

No conversion in v0.2. Karma's .rat, a .tx mipmap and a UDIM set block every format in
blueprint 4.6, so they are flagged here and never converted. Pure pxr (principle 3).

    paths = authored_paths(layer)             # every asset path in the layer, as written
    where = locate(layer, "./tex/wood.png")   # the file on disk, or None
    kind = flag("./tex/skin.<UDIM>.exr")      # "udim", "texture_format" or None
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from pxr import Ar, Sdf, UsdUtils

BLOCKED_FORMATS = {".rat": "Karma's mipmapped .rat", ".tx": "a .tx mipmap"}
USD_LAYERS = {".usd", ".usda", ".usdc"}  # layers whose asset paths can be read and rewritten; not .usdz
_UDIM = re.compile(r"<udim>", re.IGNORECASE)


def authored_paths(layer: Sdf.Layer) -> list[str]:
    """Every asset path a layer authors, as written, each once: sublayers, references and payloads
    in every variant, attribute values and time samples, metadata. Reads only: ModifyAssetPaths with
    an identity function edits nothing, and an edit would be undone from disk and reported."""
    seen: dict[str, None] = {}
    was_dirty = layer.dirty

    def visit(path):
        if path:
            seen.setdefault(path, None)
        return path

    UsdUtils.ModifyAssetPaths(layer, visit, keepEmptyPathsInArrays=True)
    if layer.dirty and not was_dirty:  # never expected; never leave a source layer edited
        layer.Reload(force=True)
        raise RuntimeError(f"reading asset paths edited {layer.identifier}; it was reloaded from disk")
    return list(seen)


def is_udim(path: str) -> bool:
    """True for a UDIM pattern such as skin.<UDIM>.exr. Read from the authored string: the
    tiles of a pattern can all resolve, so resolution alone never shows it."""
    return bool(_UDIM.search(path))


def flag(path: str) -> str | None:
    """Why no web format can load this texture: "udim", "texture_format", or None."""
    inner = path
    while inner and Ar.IsPackageRelativePath(inner):
        _, inner = Ar.SplitPackageRelativePathOuter(inner)
    if is_udim(inner):
        return "udim"
    if os.path.splitext(inner)[1].lower() in BLOCKED_FORMATS:
        return "texture_format"
    return None


def anchor(layer: Sdf.Layer, path: str) -> str:
    """Where an authored path points, read from the layer that authors it. "./" and "../" anchor
    to the layer's folder; a bare "tex/a.png" does too when the file is there, as USD resolves it."""
    return Sdf.ComputeAssetPathRelativeToLayer(layer, path)


def locate(layer: Sdf.Layer, path: str) -> str | None:
    """The real file on disk an authored path means, or None when USD can't resolve it."""
    resolved = str(Ar.GetResolver().Resolve(anchor(layer, path)))
    return os.path.realpath(resolved) if resolved else None


def copy(src: str | Path, dst: str | Path) -> int:
    """Copy one file, making its folder. Returns the bytes written."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    os.chmod(dst, dst.stat().st_mode | 0o200)
    return dst.stat().st_size
