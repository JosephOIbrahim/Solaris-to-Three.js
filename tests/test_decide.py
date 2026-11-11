"""Tests for tools/decide.py, against the local Jev stand-in. No key or network needed.

    python -m unittest tests.test_decide -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import decide  # noqa: E402
import jev  # noqa: E402
from jev_stub import JevStub  # noqa: E402

DEFAULT_URL = jev.URL
DECISION = {
    "id": "test-writer",
    "decision": "Which writer should export the folder?",
    "constraints": {"one_writer": "One format is one plugin: one writer per format."},
    "evidence": ["Option b already passed its tests.", "Option a left a path dangling in a probe."],
    "options": {"a": "Write the folder with a new localizer.", "b": "Reuse the writer that passed its tests."},
    "checks": {"risk": "Does `evidence` show a risk in option a?"},
}


def judge(pick="b", p=0.95, need=0.1, risk=0.9):
    def answer(qid, _q, _state):
        return {"approach": (pick, p), "needs_person": need, "check_risk": risk}[qid]
    return answer


class DecideTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("TYPESAFE_API_KEY", None)

    def tearDown(self):
        jev.URL = DEFAULT_URL
        os.environ.pop("TYPESAFE_API_KEY", None)

    def decide_with(self, fail_first=None, **kw):
        with JevStub(judge(**kw), fail_first=fail_first) as stub:
            jev.URL, os.environ["TYPESAFE_API_KEY"] = stub.url, "test-key"
            return decide.decide(DECISION), stub

    def test_a_clear_choice_is_adopted_in_one_request(self):
        rec, stub = self.decide_with()
        self.assertEqual((rec["verdict"], rec["choice"], rec["reasons"]), ("ADOPT", "b", []))
        self.assertEqual(len(stub.requests), 1)
        questions = stub.requests[0]["body"]["questions"]
        self.assertEqual(set(questions), {"approach", "needs_person", "check_risk"})
        self.assertEqual(set(questions["approach"]["criteria"]), {"a", "b", "neither"})
        self.assertEqual(questions["needs_person"]["type"], "noul")
        self.assertEqual(rec["checks"], {"risk": 0.9})

    def test_a_weak_choice_goes_to_joe(self):
        rec, _ = self.decide_with(p=0.6)
        self.assertEqual(rec["verdict"], "ASK JOE")
        self.assertIn("under 0.7", rec["reasons"][0])

    def test_neither_goes_to_joe(self):
        rec, _ = self.decide_with(pick="neither")
        self.assertEqual(rec["verdict"], "ASK JOE")
        self.assertIn("Jev chose neither option", rec["reasons"])

    def test_an_uncertain_needs_person_goes_to_joe(self):
        rec, _ = self.decide_with(need=0.39)
        self.assertEqual(rec["verdict"], "ASK JOE")
        self.assertIn("(unsure)", rec["reasons"][0])

    def test_a_yes_on_needs_person_goes_to_joe(self):
        rec, _ = self.decide_with(need=0.8)
        self.assertEqual(rec["verdict"], "ASK JOE")
        self.assertIn("(yes)", rec["reasons"][0])

    def test_no_key_asks_joe_without_a_request(self):
        rec = decide.decide(DECISION)
        self.assertEqual(rec["verdict"], "ASK JOE")
        self.assertIn("no TYPESAFE_API_KEY", rec["reasons"][0])

    def test_a_failed_request_asks_joe(self):
        rec, _ = self.decide_with(fail_first=500)
        self.assertEqual(rec["verdict"], "ASK JOE")
        self.assertIn("HTTP 500", rec["reasons"][0])

    def test_the_record_keeps_raw_probabilities_and_the_exit_code_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, log = Path(tmp) / "d.json", Path(tmp) / "decisions.jsonl"
            src.write_text(json.dumps(DECISION), encoding="utf-8")
            with JevStub(judge(need=0.39)) as stub:
                jev.URL, os.environ["TYPESAFE_API_KEY"] = stub.url, "test-key"
                code = decide.main([str(src), "--record", str(log), "--json"])
            rec = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(code, 1)
        self.assertEqual(rec["needs_person"], 0.39)
        self.assertEqual(set(rec["probabilities"]), {"a", "b", "neither"})


if __name__ == "__main__":
    unittest.main()
