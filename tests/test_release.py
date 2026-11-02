"""Tests for how tools/release.py reads the changelog. Standard library only.

    python -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from release import notes_for  # noqa: E402

SAMPLE = """# Changelog

## [Unreleased]

## [0.2.0] - 2026-12-01

### Added

- A thing.

## [0.1.0] - 2026-11-02

### Fixed

- Another thing.

[Unreleased]: https://example.com/compare/v0.2.0...HEAD
[0.2.0]: https://example.com/releases/tag/v0.2.0
"""


class ReleaseNotesTest(unittest.TestCase):
    def test_reads_one_section(self):
        self.assertEqual(notes_for("0.2.0", SAMPLE), "### Added\n\n- A thing.")

    def test_last_section_stops_at_the_links(self):
        self.assertEqual(notes_for("0.1.0", SAMPLE), "### Fixed\n\n- Another thing.")

    def test_missing_or_empty_section_is_none(self):
        self.assertIsNone(notes_for("9.9.9", SAMPLE))
        self.assertIsNone(notes_for("Unreleased", SAMPLE))

    def test_changelog_has_notes_for_the_current_version(self):
        version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
        notes = notes_for(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertTrue(notes, f"CHANGELOG.md needs a '## [{version}]' section with notes")


if __name__ == "__main__":
    unittest.main()