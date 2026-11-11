"""/WebSettings: viewer settings that travel in the stage (blueprint 4.5, principle 8).

A Scope at /WebSettings with attributes namespaced `web:`. Pure pxr. An absent prim, or
an absent attribute, gives the default. None means "use the rule": title falls back to
the stage file name, camera to the first camera, hudVariantSets to all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from pxr import Sdf, Usd

SETTINGS_PATH = "/WebSettings"


@dataclass
class WebSettings:
    title: str | None = None                  # web:title, string; None = stage file name
    camera: str | None = None                 # web:camera, prim path; None = first camera
    hudVariantSets: list[str] | None = None   # web:hudVariantSets, string[]; None = all
    showProxy: bool = False                   # web:showProxy
    backgroundBlur: float = 0.35              # web:backgroundBlur
    exposureOffset: float = 0.0               # web:exposureOffset
    showHud: bool = True                      # web:showHud


# attribute name (without "web:") -> Sdf type
_TYPES = {
    "title": Sdf.ValueTypeNames.String,
    "camera": Sdf.ValueTypeNames.String,
    "hudVariantSets": Sdf.ValueTypeNames.StringArray,
    "showProxy": Sdf.ValueTypeNames.Bool,
    "backgroundBlur": Sdf.ValueTypeNames.Float,
    "exposureOffset": Sdf.ValueTypeNames.Float,
    "showHud": Sdf.ValueTypeNames.Bool,
}


def _stage_file_name(stage: Usd.Stage) -> str:
    layer = stage.GetRootLayer()
    if layer.anonymous:
        return ""
    return os.path.basename(layer.realPath or layer.identifier)


def read_settings(stage: Usd.Stage) -> WebSettings:
    """Read /WebSettings. Missing prim or attribute gives the default."""
    s = WebSettings()
    prim = stage.GetPrimAtPath(SETTINGS_PATH)
    if prim and prim.IsValid():
        for name in _TYPES:
            attr = prim.GetAttribute(f"web:{name}")
            if not attr or not attr.HasAuthoredValue():
                continue
            v = attr.Get()
            if v is None:
                continue
            if name == "hudVariantSets":
                v = [str(x) for x in v]
            elif name in ("showProxy", "showHud"):
                v = bool(v)
            elif name in ("backgroundBlur", "exposureOffset"):
                v = round(float(v), 6)  # float32 on disk; keep 0.8 as 0.8
            else:
                v = str(v)
            setattr(s, name, v)
    if s.title is None:
        s.title = _stage_file_name(stage)
    return s


def write_settings(stage: Usd.Stage, settings: WebSettings) -> Usd.Prim:
    """Write /WebSettings as a Scope onto the stage's current edit target.

    A None field removes its attribute so the default rule applies again.
    """
    prim = stage.DefinePrim(SETTINGS_PATH, "Scope")
    for name, type_name in _TYPES.items():
        value = getattr(settings, name)
        attr_name = f"web:{name}"
        if value is None:
            if prim.HasAttribute(attr_name):
                prim.RemoveProperty(attr_name)
            continue
        attr = prim.CreateAttribute(attr_name, type_name, custom=True)
        if name == "hudVariantSets":
            value = [str(x) for x in value]
        attr.Set(value)
    return prim
