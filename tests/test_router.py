"""Tests for tools/build_router.py. Standard library only.

    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_router  # noqa: E402
import jev  # noqa: E402
from jev_stub import JevStub  # noqa: E402

PLAN = json.loads((ROOT / "plan" / "tasks.json").read_text(encoding="utf-8"))
EXPECTED = {k: v for k, v in json.loads((ROOT / "plan" / "tasks.expected.json").read_text(encoding="utf-8")).items()
            if not k.startswith("_")}
DEFAULT_URL = jev.URL


def stub_decider(unsure=("T16",), sizes=None):
    """Answer `where` with Claude's labels (unsure tasks at 0.40), scaffold 'none', sizes as given."""
    sizes = sizes or {}

    def decide(qid, _q, _state):
        task, kind = qid.split("_")
        if kind == "where":
            return EXPECTED[task], 0.40 if task in unsure else 0.93
        if kind == "scaffold":
            return "none", 0.9
        return sizes.get(task, 1.0), 0.8

    return decide


class RouterTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("TYPESAFE_API_KEY", None)

    def tearDown(self):
        jev.URL = DEFAULT_URL
        os.environ.pop("TYPESAFE_API_KEY", None)

    def test_without_a_key_rows_are_labelled_keyword_guesses(self):
        rows, note = build_router.route(PLAN)
        self.assertTrue(all(r.get("guess") for r in rows))
        self.assertIn("keyword guesses", note)
        self.assertEqual(sum(r["lane"] == EXPECTED[r["id"]] for r in rows), 14)

    def test_one_request_three_questions_per_task(self):
        with JevStub(stub_decider()) as stub:
            jev.URL, os.environ["TYPESAFE_API_KEY"] = stub.url, "test-key"
            build_router.route(PLAN)
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual(len(stub.requests[0]["body"]["questions"]), 3 * len(PLAN["tasks"]))

    def test_unsure_tasks_go_to_joe_never_to_an_agent(self):
        with JevStub(stub_decider(unsure=("T16", "T07"))) as stub:
            jev.URL, os.environ["TYPESAFE_API_KEY"] = stub.url, "test-key"
            rows, _note = build_router.route(PLAN)
        by_id = {r["id"]: r for r in rows}
        for task in ("T16", "T07"):
            self.assertEqual(by_id[task]["lane"], "decision")
            self.assertTrue(by_id[task]["unsure"])
            self.assertEqual(by_id[task]["model"], "-")

    def test_small_cloud_tasks_get_the_small_model(self):
        with JevStub(stub_decider(sizes={"T07": 0.4, "T12": 2.3})) as stub:
            jev.URL, os.environ["TYPESAFE_API_KEY"] = stub.url, "test-key"
            rows, _note = build_router.route(PLAN)
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["T07"]["model"], build_router.SMALL)
        self.assertEqual(by_id["T12"]["model"], build_router.BIG)
        self.assertEqual(by_id["T02"]["model"], "-")  # desk work runs no model

    def test_rate_limit_is_retried(self):
        with JevStub(stub_decider(), fail_first=429) as stub:
            jev.URL, os.environ["TYPESAFE_API_KEY"] = stub.url, "test-key"
            rows, _note = build_router.route(PLAN)
        self.assertEqual(len(stub.requests), 2)
        self.assertFalse(any(r.get("guess") for r in rows))


if __name__ == "__main__":
    unittest.main()
