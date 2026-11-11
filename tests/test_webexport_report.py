"""Tests for python/webexport/report.py. Pure Python, no pxr needed.

    python -m unittest tests.test_webexport_report -v
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from webexport.report import LossItem, LossReport, verdict_for  # noqa: E402


def item(sev, feature="rect_light", count=2):
    return LossItem(sev, feature, count, ["/World/lights/a", "/World/lights/b"], "point light", "use a sphere light")


class VerdictRule(unittest.TestCase):
    def test_blocks_gives_fix_first(self):
        self.assertEqual(verdict_for([item("note"), item("changes"), item("blocks")]), "FIX FIRST")

    def test_changes_gives_go_with_notes(self):
        self.assertEqual(verdict_for([item("note"), item("changes")]), "GO, WITH NOTES")

    def test_notes_or_nothing_gives_go(self):
        self.assertEqual(verdict_for([item("note")]), "GO")
        self.assertEqual(verdict_for([]), "GO")

    def test_from_items_computes_the_verdict(self):
        self.assertEqual(LossReport.from_items("gltf", [item("blocks")]).verdict, "FIX FIRST")

    def test_bad_severity_is_refused(self):
        with self.assertRaises(ValueError):
            LossItem("fatal", "x", 1)


class RoundTrip(unittest.TestCase):
    def test_to_json_from_json(self):
        r = LossReport.from_items("x3d", [item("blocks"), LossItem("note", "proxy", 1, [], "hidden", None)],
                                  jev_note="Jev off")
        d = r.to_json()
        json.dumps(d)  # must be serialisable as is
        self.assertEqual(LossReport.from_json(d), r)
        self.assertEqual(LossReport.from_json(json.dumps(d)), r)
        self.assertEqual(LossReport.from_json(r.to_json_str()), r)


class RenderText(unittest.TestCase):
    def setUp(self):
        self.text = LossReport.from_items("gltf", [
            item("blocks", "udim_texture", 3),
            item("changes", "rect_light", 2),
            item("note", "proxy_prims", 1),
        ]).render_text()

    def test_verdict_first(self):
        lines = self.text.splitlines()
        self.assertTrue(lines[2].startswith("FIX FIRST"))

    def test_three_sections_three_marks(self):
        for title, mark, feature in (("FIX FIRST", "x", "udim_texture"),
                                     ("WILL LOOK DIFFERENT", "!", "rect_light"),
                                     ("WORTH KNOWING", "-", "proxy_prims")):
            self.assertIn(title, self.text)
            self.assertRegex(self.text, rf"(?m)^  {mark}  {feature}\s+\d+$")

    def test_item_detail(self):
        self.assertIn("/World/lights/a", self.text)
        self.assertNotIn("/World/lights/b", self.text)  # first example only
        self.assertIn("becomes: point light", self.text)
        self.assertIn("fix: use a sphere light", self.text)

    def test_empty_sections_are_left_out(self):
        text = LossReport.from_items("gltf", []).render_text()
        self.assertIn("GO", text)
        self.assertNotIn("WILL LOOK DIFFERENT", text)


if __name__ == "__main__":
    unittest.main()
