"""Test fixture for the viewer's /WebSettings support (blueprint M3).

    fixtures/stage/shot_websettings.usda   sublayers ./shot.usda and authors /WebSettings

It sits next to shot.usda because the viewer resolves the dome texture against the root
layer's folder; a root layer anywhere else would 404 the dome. /WebSettings sets a title
and hides the HUD. The prim is authored by webexport.settings, the same code the exporter uses.

Run: python tools/make_websettings_stage.py   (needs usd-core, or hython)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from pxr import Usd  # noqa: E402
from webexport.settings import WebSettings, write_settings  # noqa: E402

TITLE = "WebSettings Fixture"  # tests/browser/smoke.mjs asserts this
OUT = ROOT / "fixtures" / "stage" / "shot_websettings.usda"

stage = Usd.Stage.CreateNew(str(OUT))
stage.GetRootLayer().subLayerPaths.append("./shot.usda")
stage.GetRootLayer().defaultPrim = "hero"  # same as shot.usda; metadata is not inherited by name
write_settings(stage, WebSettings(title=TITLE, showHud=False))
stage.GetRootLayer().Save()
print(f"wrote {OUT}")
