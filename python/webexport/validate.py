"""Validate: re-open what was written and check it. For the USD folder this is the web census.

validate_folder() runs tools/web_census.py's census() and decide() on the written root layer,
then checks what the census does not: every layer and file sits in the folder, every asset path
is a "./" or "../" path that lands inside it and resolves, and /WebSettings is in the root layer.

census_of() is the same census on a live stage, read through the same gather(), so the profile
can assess a stage without saving it or opening it a second time.

Nothing here exits the host: census() calls sys.exit on a stage that won't open, and that is
caught and reported. Pure pxr (principle 3).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pxr import Ar, Sdf, Usd, UsdUtils

from . import textures
from .inventory import StageFacts, _norm, gather, inside, is_absolute
from .settings import SETTINGS_PATH

# tools/web_census.py is a script, not a package. Put tools/ on the path the way the tests do.
_TOOLS = str(Path(__file__).resolve().parents[2] / "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import web_census  # noqa: E402

__all__ = ["Validation", "census_of", "validate_folder", "path_problems", "web_census"]


def census_of(stage: Usd.Stage, facts: StageFacts | None = None) -> dict:
    """web_census.census() for a live stage: the same keys, from the same gather().

    facts: a StageFacts from gather(stage, track_types=web_census.UNTESTED_TYPES), when the
    caller already has one. Facts gathered without those types would miss the untested prims.
    """
    f = facts or gather(stage, track_types=web_census.UNTESTED_TYPES)
    return {"stage": stage, "shaders": f.shaders, "textures": f.asset_rows, "lights": f.lights_by_type,
            "untested": f.tracked, "no_uv": f.no_uv, "unbound": f.unbound, "purposes": f.purposes,
            "prims": f.prims, "layers": f.layers, "outside": f.outside, "errors": f.errors}


@dataclass
class Validation:
    entry_file: Path
    verdict: str | None = None                          # the census on the folder; None if it didn't open
    problems: list[str] = field(default_factory=list)   # what the census does not check, in plain words
    census_text: str = ""                               # the census report, for a terminal

    @property
    def ok(self) -> bool:
        """It opens, the census does not say FIX FIRST, and every path stays in the folder."""
        return self.verdict not in (None, "FIX FIRST") and not self.problems


def path_problems(entry_file: str | Path) -> list[str]:
    """Every way the folder under entry_file is not self-contained. Empty when it is."""
    entry = Path(entry_file)
    folder = _norm(str(entry.parent))
    out = []
    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(entry)))
    out += [f"resolves to no file: {p}" for p in unresolved]
    out += [f"outside the folder: {a}" for a in assets if not inside(a, folder)]
    for layer in layers:
        if Ar.IsPackageRelativePath(layer.identifier):
            outer = Ar.SplitPackageRelativePathOuter(layer.identifier)[0]
            if not outer or not inside(outer, folder):
                out.append(f"layer outside the folder: {layer.identifier}")
            continue
        if layer.anonymous or not layer.realPath or not inside(layer.realPath, folder):
            out.append(f"layer outside the folder: {layer.identifier}")
            continue
        if os.path.splitext(layer.realPath)[1].lower() not in textures.USD_LAYERS:
            continue  # a .usdz carries package-relative paths of its own
        name = os.path.relpath(os.path.realpath(layer.realPath), os.path.realpath(entry.parent)).replace(os.sep, "/")
        for path in textures.authored_paths(layer):
            p = Ar.SplitPackageRelativePathOuter(path)[0] if Ar.IsPackageRelativePath(path) else path
            if is_absolute(p) or not p.startswith(("./", "../")):
                out.append(f"{name}: {path} is not a ./ or ../ path")
            elif not inside(textures.anchor(layer, p), folder):
                out.append(f"{name}: {path} points outside the folder")
            elif not textures.is_udim(p) and textures.locate(layer, p) is None:
                out.append(f"{name}: {path} resolves to no file")
    return out


def validate_folder(entry_file: str | Path, jev_on: bool = False) -> Validation:
    """Re-open the written folder and check it. Jev is off by default: it only sorts unfamiliar
    shader nodes into notes, which never decide whether the folder loads."""
    entry = Path(entry_file)
    v = Validation(entry)
    if not entry.is_file():
        v.problems.append(f"{entry} was not written")
        return v
    try:
        stage = Usd.Stage.Open(str(entry))
    except Exception as e:  # pxr raises Tf.ErrorException for a layer it can't read
        v.problems.append(f"{entry.name} does not open: {e}")
        return v
    if not stage:
        v.problems.append(f"{entry.name} does not open")
        return v
    c = census_of(stage)
    verdict, red, amber, info, note = web_census.decide(c, jev_on=jev_on)
    v.verdict = verdict
    v.census_text = web_census.report(entry, c, verdict, red, amber, info, note)
    v.problems += path_problems(entry)
    prim = stage.GetPrimAtPath(SETTINGS_PATH)
    if not (prim and prim.IsValid() and prim.IsActive() and prim.GetTypeName() == "Scope"
            and stage.GetRootLayer().GetPrimAtPath(SETTINGS_PATH)):
        v.problems.append(f"{SETTINGS_PATH} is not a Scope in {entry.name}")
    return v
