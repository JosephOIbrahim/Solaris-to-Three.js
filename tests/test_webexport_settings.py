"""Tests for python/webexport/settings.py. Needs pxr: `pip install usd-core`.

    python -m unittest tests.test_webexport_settings -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

try:
    from pxr import Usd
    from webexport.settings import WebSettings, read_settings, write_settings
except ImportError:  # no pxr on this machine
    Usd = None


@unittest.skipIf(Usd is None, "needs pxr (pip install usd-core)")
class WebSettingsScope(unittest.TestCase):
    def test_defaults_when_prim_absent(self):
        s = read_settings(Usd.Stage.CreateInMemory())
        self.assertEqual(s, WebSettings(title=""))  # anonymous stage: no file name
        self.assertIsNone(s.camera)
        self.assertIsNone(s.hudVariantSets)
        self.assertFalse(s.showProxy)
        self.assertEqual(s.backgroundBlur, 0.35)
        self.assertEqual(s.exposureOffset, 0.0)
        self.assertTrue(s.showHud)

    def test_title_defaults_to_stage_file_name(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            stage = Usd.Stage.CreateNew(str(Path(d) / "shot.usda"))
            self.assertEqual(read_settings(stage).title, "shot.usda")
            del stage

    def test_write_then_read_in_memory(self):
        stage = Usd.Stage.CreateInMemory()
        want = WebSettings(title="Hero", camera="/World/cam", hudVariantSets=["look", "set"],
                           showProxy=True, backgroundBlur=0.8, exposureOffset=-1.5, showHud=False)
        prim = write_settings(stage, want)
        self.assertEqual(prim.GetTypeName(), "Scope")
        self.assertEqual(str(prim.GetPath()), "/WebSettings")
        self.assertTrue(prim.GetAttribute("web:showHud").IsValid())
        self.assertEqual(read_settings(stage), want)

    def test_none_clears_the_attribute(self):
        stage = Usd.Stage.CreateInMemory()
        write_settings(stage, WebSettings(title="A", camera="/c"))
        write_settings(stage, WebSettings(title="A", camera=None))
        self.assertFalse(stage.GetPrimAtPath("/WebSettings").HasAttribute("web:camera"))
        self.assertIsNone(read_settings(stage).camera)

    def test_partial_prim_fills_in_defaults(self):
        from pxr import Sdf
        stage = Usd.Stage.CreateInMemory()
        prim = stage.DefinePrim("/WebSettings", "Scope")
        prim.CreateAttribute("web:showHud", Sdf.ValueTypeNames.Bool).Set(False)
        s = read_settings(stage)
        self.assertFalse(s.showHud)
        self.assertEqual(s.backgroundBlur, 0.35)


if __name__ == "__main__":
    unittest.main()
