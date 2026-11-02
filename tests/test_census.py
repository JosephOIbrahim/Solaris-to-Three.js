"""Tests for tools/web_census.py. Needs pxr: `pip install usd-core`, or run under hython.

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jev  # noqa: E402
from jev_stub import JevStub  # noqa: E402

try:
    import web_census  # noqa: E402
except ImportError:  # no pxr on this machine
    web_census = None

CLEAN = ROOT / "fixtures" / "stage" / "shot.usda"
DIRTY = ROOT / "fixtures" / "stage-dirty" / "dirty.usda"
DEFAULT_URL = jev.URL

FIX_FIRST = {
    "composition errors",
    "absolute or $VAR asset paths",
    "UDIM textures (no UDIM support)",
    "texture formats the browser can't load",
    "missing files",
    "MaterialX meshes without UVs (render black)",
}


@unittest.skipIf(web_census is None, "needs pxr (pip install usd-core, or run under hython)")
class CensusTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("TYPESAFE_API_KEY", None)

    def tearDown(self):
        jev.URL = DEFAULT_URL
        os.environ.pop("TYPESAFE_API_KEY", None)

    def run_census(self, path, jev_on=True):
        return web_census.decide(web_census.census(path), jev_on=jev_on)

    def test_clean_stage_is_go(self):
        verdict, red, amber, _info, _note = self.run_census(CLEAN)
        self.assertEqual(verdict, "GO")
        self.assertEqual(red, [])
        self.assertEqual(amber, [])

    def test_dirty_stage_catches_all_eight_planted_problems(self):
        verdict, red, amber, _info, note = self.run_census(DIRTY)
        self.assertEqual(verdict, "FIX FIRST")
        self.assertEqual({label for label, _n, _ex in red}, FIX_FIRST)
        self.assertEqual({label for label, _n, _ex in amber}, {
            "dome texture not .hdr (the bridge reads .hdr)",
            "lights approximated as point lights",
            "shader nodes that need your eyes",
        })
        self.assertIn("no TYPESAFE_API_KEY", note)

    def test_jev_sorts_unfamiliar_nodes_in_one_request(self):
        def decide(qid, _q, state):
            node = state["nodes"][qid]["id"]
            return ("renderer_specific", 0.92) if node.startswith("kma") else ("portable_materialx", 0.95)

        with JevStub(decide) as stub:
            jev.URL, os.environ["TYPESAFE_API_KEY"] = stub.url, "test-key"
            _verdict, _red, amber, info, _note = self.run_census(DIRTY)
        notes = {label: example for label, _n, example in amber}
        self.assertTrue(notes["renderer-only nodes (fall back to grey)"].startswith("kma_fixture_node"))
        self.assertNotIn("shader nodes that need your eyes", notes)
        self.assertIn("untested MaterialX nodes, Jev says portable", {label for label, _n, _ex in info})
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual(stub.requests[0]["auth"], "Bearer test-key")

    def test_jev_failure_fails_open(self):
        with JevStub(lambda *a: ("cannot_tell", 1.0), fail_first=401) as stub:
            jev.URL, os.environ["TYPESAFE_API_KEY"] = stub.url, "test-key"
            verdict, _red, amber, _info, note = self.run_census(DIRTY)
        self.assertEqual(verdict, "FIX FIRST")
        self.assertIn("shader nodes that need your eyes", {label for label, _n, _ex in amber})
        self.assertIn("HTTP 401", note)


if __name__ == "__main__":
    unittest.main()
