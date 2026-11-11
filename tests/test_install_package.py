"""Tests for scripts/install_package.py. Installs only into a temp folder.

    python -m unittest tests.test_install_package -v
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import install_package as ip  # noqa: E402


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = ip.main(list(argv))
    return code, buf.getvalue()


class TrackedPackage(unittest.TestCase):
    def test_shape(self):
        pkg = json.loads((ROOT / "packages" / "webexport.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg["name"], "webexport")
        self.assertTrue(pkg["load_package_once"])
        self.assertEqual(pkg["hpath"], "$WEBEXPORT_ROOT/houdini")
        self.assertEqual([e["var"] for e in pkg["env"]], ["WEBEXPORT_ROOT", "PYTHONPATH"])
        self.assertEqual(pkg["env"][0]["value"], "$HOUDINI_PACKAGE_PATH/..")
        self.assertEqual(pkg["env"][1]["method"], "prepend")
        self.assertTrue((ROOT / "houdini").is_dir())


class Install(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run("--dry-run", "--prefs-dir", tmp)
            self.assertEqual(code, 0)
            self.assertIn((Path(tmp) / "packages" / "webexport.json").as_posix(), out)
            self.assertIn('"WEBEXPORT_ROOT"', out)
            self.assertFalse((Path(tmp) / "packages").exists())

    def test_install_then_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run("--verify", "--prefs-dir", tmp)[0], 1)  # nothing yet
            self.assertEqual(run("--prefs-dir", tmp)[0], 0)
            deployed = Path(tmp) / "packages" / "webexport.json"
            data = json.loads(deployed.read_text(encoding="utf-8"))
            self.assertEqual(data["env"][0]["value"], ROOT.as_posix())
            code, out = run("--verify", "--prefs-dir", tmp)
            self.assertEqual(code, 0, out)

    def test_verify_fails_on_other_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("--prefs-dir", tmp)
            deployed = Path(tmp) / "packages" / "webexport.json"
            data = json.loads(deployed.read_text(encoding="utf-8"))
            data["env"][0]["value"] = (Path(tmp) / "elsewhere").as_posix()
            deployed.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(run("--verify", "--prefs-dir", tmp)[0], 1)

    def test_verify_fails_on_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            deployed = Path(tmp) / "packages" / "webexport.json"
            deployed.parent.mkdir()
            deployed.write_text("{nope", encoding="utf-8")
            self.assertEqual(run("--verify", "--prefs-dir", tmp)[0], 1)


class PrefsDir(unittest.TestCase):
    def test_env_wins(self):
        with mock.patch.dict(os.environ, {"HOUDINI_USER_PREF_DIR": "X:/prefs/houdini22.0"}):
            self.assertEqual(ip.default_prefs_dir(), Path("X:/prefs/houdini22.0"))

    def test_documents_fallback(self):
        env = {k: v for k, v in os.environ.items() if k != "HOUDINI_USER_PREF_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(ip.default_prefs_dir(), ip.documents_dir() / "houdini22.0")


if __name__ == "__main__":
    unittest.main()
