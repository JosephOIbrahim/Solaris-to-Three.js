"""Tests for the Houdini 22 custom shelf, Python Panel definition, and pure-Python panel model.

    python -m unittest tests/test_houdini_ui.py -v

Runs in CI without Houdini or PySide6 (blueprint principle 3).
"""
import importlib
import json
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "houdini" / "scripts" / "python"))

import doctor  # noqa: E402
import package_release  # noqa: E402
from webexport import panel  # noqa: E402
from webexport.report import LossItem, LossReport  # noqa: E402

try:
    from pxr import Usd  # noqa: E402
    from webexport import api, inventory  # noqa: E402
except ImportError:
    Usd = None

SHELF_PATH = ROOT / "houdini" / "toolbar" / "webexport.shelf"
PYPANEL_PATH = ROOT / "houdini" / "python_panels" / "webexport.pypanel"
MENU_PATH = ROOT / "houdini" / "MainMenuCommon.xml"
ICON_PATH = ROOT / "houdini" / "config" / "Icons" / "webexport.svg"
CLEAN_FIXTURE = ROOT / "fixtures" / "stage" / "shot.usda"
DIRTY_FIXTURE = ROOT / "fixtures" / "stage-dirty" / "dirty.usda"


class HoudiniAssetManifestTests(unittest.TestCase):
    def test_icon_svg_is_valid_xml(self):
        self.assertTrue(ICON_PATH.is_file(), f"missing icon: {ICON_PATH}")
        root = ET.parse(ICON_PATH).getroot()
        self.assertTrue(root.tag.endswith("svg"))

    def test_shelf_xml_structure_and_compilable_tool_scripts(self):
        self.assertTrue(SHELF_PATH.is_file(), f"missing shelf: {SHELF_PATH}")
        root = ET.parse(SHELF_PATH).getroot()
        self.assertEqual(root.tag, "shelfDocument")

        shelves = root.findall("toolshelf")
        self.assertEqual(len(shelves), 1)
        shelf = shelves[0]
        self.assertEqual(shelf.get("name"), "web_export")
        self.assertEqual(shelf.get("label"), "Web Export")

        member_names = [m.get("name") for m in shelf.findall("memberTool")]
        self.assertEqual(
            member_names,
            [
                "webexport_add_node",
                "webexport_open_panel",
                "webexport_check",
                "webexport_export",
                "webexport_preview",
            ],
        )

        tools = {t.get("name"): t for t in root.findall("tool")}
        self.assertEqual(set(member_names), set(tools.keys()))
        for name, tool in tools.items():
            script = tool.find("script")
            self.assertIsNotNone(script, f"tool {name} missing <script>")
            self.assertEqual(script.get("scriptType"), "python")
            code = (script.text or "").strip()
            self.assertIn("webexport_shelf", code)
            compile(code, f"webexport.shelf:{name}", "exec")

    def test_pypanel_xml_structure_menu_hooks_and_compilable_script(self):
        self.assertTrue(PYPANEL_PATH.is_file(), f"missing pypanel: {PYPANEL_PATH}")
        root = ET.parse(PYPANEL_PATH).getroot()
        self.assertEqual(root.tag, "pythonPanelDocument")

        ifaces = root.findall("interface")
        self.assertEqual(len(ifaces), 1)
        iface = ifaces[0]
        self.assertEqual(iface.get("name"), "web_export_panel")
        self.assertEqual(iface.get("label"), "Web Export")
        self.assertEqual(iface.get("icon"), "webexport")
        self.assertEqual(iface.get("showNetworkNavigationBar"), "true")
        self.assertIsNotNone(iface.find("includeInToolbarMenu"))
        self.assertIsNotNone(iface.find("includeInPaneTabMenu"))

        script = iface.find("script")
        self.assertIsNotNone(script)
        code = (script.text or "").strip()
        for hook in (
            "def onCreateInterface(",
            "def onActivateInterface(",
            "def onDeactivateInterface(",
            "def onDestroyInterface(",
            "def onNodePathChanged(",
        ):
            self.assertIn(hook, code)
        compile(code, "webexport.pypanel", "exec")

    def test_main_menu_xml_structure_and_compilable_scripts(self):
        self.assertTrue(MENU_PATH.is_file(), f"missing menu: {MENU_PATH}")
        root = ET.parse(MENU_PATH).getroot()
        self.assertEqual(root.tag, "mainMenu")
        submenu = root.find("./menuBar/subMenu[@id='webexport_menu']")
        self.assertIsNotNone(submenu)
        self.assertEqual(submenu.findtext("label"), "Web Export")
        items = submenu.findall("scriptItem")
        self.assertGreaterEqual(len(items), 6)
        for item in items:
            code = (item.findtext("scriptCode") or "").strip()
            self.assertIn("webexport_shelf", code)
            compile(code, f"MainMenuCommon.xml:{item.get('id')}", "exec")


class DoctorAndPackagerTests(unittest.TestCase):
    def test_doctor_checks_repo_assets_and_reports_uninstalled_prefs(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="webexport_doctor_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        rep = doctor.run_checks(root=ROOT, prefs_dir=tmp)
        by_name = {c["name"]: c for c in rep["checks"]}
        self.assertTrue(by_name["houdini_assets"]["ok"], by_name["houdini_assets"])
        self.assertFalse(by_name["houdini_package"]["ok"])
        self.assertIn("WEB EXPORT PREFLIGHT", doctor.render_report(rep))

    def test_package_release_builds_deterministic_mit_bundle_without_needle(self):
        import shutil
        import tempfile
        import zipfile
        tmp = Path(tempfile.mkdtemp(prefix="webexport_pkg_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        z1, sums1, d1 = package_release.build_bundle(root=ROOT, out_dir=tmp / "a")
        z2, _sums2, d2 = package_release.build_bundle(root=ROOT, out_dir=tmp / "b")
        self.assertEqual(d1, d2, "release zip must be deterministic across builds")
        self.assertIn(d1, sums1.read_text(encoding="utf-8"))
        with zipfile.ZipFile(z1) as zf:
            names = zf.namelist()
        self.assertTrue(any(n.endswith("/houdini/otls/web_export.hda") for n in names))
        self.assertTrue(any(n.endswith("/houdini/toolbar/webexport.shelf") for n in names))
        self.assertTrue(any(n.endswith("/houdini/python_panels/webexport.pypanel") for n in names))
        self.assertTrue(any(n.endswith("/houdini/MainMenuCommon.xml") for n in names))
        self.assertFalse(any("node_modules/" in n or "@needle-tools" in n for n in names),
                         "release bundle must never contain node_modules or PolyForm @needle-tools packages")


class PanelModelTests(unittest.TestCase):
    def test_model_imports_without_hou_or_qt_and_computes_matrix_and_verdicts(self):
        self.assertNotIn("PySide6", sys.modules)
        self.assertNotIn("PySide2", sys.modules)
        self.assertIn("QWidget#WebExportPanel", panel.build_stylesheet())

        r_usd = LossReport.from_items("usd_folder", [
            LossItem("note", "dome_light", 1, ["/lights/dome"], "becomes environment"),
        ])
        r_gltf = LossReport.from_items("gltf", [
            LossItem("changes", "dome_light", 1, ["/lights/dome"], "dropped"),
            LossItem("blocks", "texture_format", 1, ["rust.rat"], "not loaded", "convert to .png"),
        ])
        self.assertEqual(panel.overall_verdict([r_usd, r_gltf]), "FIX FIRST")
        self.assertEqual(panel.overall_verdict([r_usd]), "GO")
        self.assertEqual(panel.overall_verdict([]), "IDLE")

        snap = panel.build_snapshot(
            node_path="/stage/web_export1",
            is_web_export_node=True,
            reports=[r_usd, r_gltf],
            headline="Check complete.",
        )
        self.assertEqual(snap.overall_verdict, "FIX FIRST")
        self.assertEqual(len(snap.cards), 2)
        self.assertEqual(snap.cards[1].blocks, 1)
        self.assertEqual(snap.cards[1].changes, 1)

        # Worst severity rows sort first: blocks (texture_format) before changes (dome_light)
        self.assertEqual([row.feature for row in snap.matrix], ["texture_format", "dome_light"])
        self.assertEqual(snap.matrix[0].cells["usd_folder"], ("ok", "kept"))
        self.assertEqual(snap.matrix[0].cells["gltf"][0], "blocks")

        txt = panel.format_copy_text(snap)
        self.assertIn("LOSS REPORT   gltf", txt)
        data = json.loads(panel.format_copy_json(snap))
        self.assertEqual(data["overall_verdict"], "FIX FIRST")
        self.assertEqual(len(data["reports"]), 2)

    @unittest.skipIf(Usd is None, "needs pxr (pip install usd-core)")
    def test_snapshot_from_real_fixtures(self):
        stage = Usd.Stage.Open(str(CLEAN_FIXTURE))
        facts = inventory.gather(stage)
        reports = api.check(stage, api.FORMATS, jev_on=False)
        snap = panel.build_snapshot(node_path="/stage/shot", inv=facts.inventory, reports=reports)
        self.assertEqual(len(snap.cards), 4)
        self.assertIsNotNone(snap.stage_summary)
        self.assertEqual(snap.stage_summary.instances, 36)
        self.assertIn("36 instances", snap.stage_summary.one_line())

        dirty_stage = Usd.Stage.Open(str(DIRTY_FIXTURE))
        dirty_reports = api.check(dirty_stage, api.FORMATS, jev_on=False)
        dirty_snap = panel.build_snapshot(node_path="/stage/dirty", reports=dirty_reports)
        self.assertEqual(dirty_snap.overall_verdict, "FIX FIRST")


class ShelfCallbackTests(unittest.TestCase):
    def _make_fake_hou(self):
        class FakeCat:
            def __init__(self, name):
                self._name = name

            def name(self):
                return self._name

        class FakeType:
            def __init__(self, name, cat="Lop"):
                self._name = name
                self._cat = FakeCat(cat)

            def name(self):
                return self._name

            def category(self):
                return self._cat

        class FakeParm:
            def __init__(self, val=""):
                self._val = val

            def eval(self):
                return self._val

            def evalAsString(self):
                return str(self._val)

            def set(self, val):
                self._val = val

        class FakeNode:
            def __init__(self, path, type_name, cat="Lop", parent=None, parms=None):
                self._path = path
                self._type = FakeType(type_name, cat)
                self._parent = parent
                self._parms = {k: FakeParm(v) for k, v in (parms or {}).items()}
                self.inputs_wired = {}
                self.display_flag = False
                self.selected = False
                self._children = []

            def path(self):
                return self._path

            def name(self):
                return self._path.rsplit("/", 1)[-1]

            def type(self):
                return self._type

            def parent(self):
                return self._parent

            def childTypeCategory(self):
                return FakeCat("Lop" if self._path == "/stage" else "Object")

            def parm(self, name):
                return self._parms.get(name)

            def setInput(self, idx, src):
                self.inputs_wired[idx] = src

            def moveToGoodPosition(self):
                pass

            def setDisplayFlag(self, flag):
                self.display_flag = flag

            def setCurrent(self, flag, clear_all_selected=False):
                self.selected = flag

            def children(self):
                return list(self._children)

            def displayNode(self):
                for c in self._children:
                    if c.display_flag:
                        return c
                return None

            def createNode(self, type_name, node_name):
                n = FakeNode(f"{self._path}/{node_name}", type_name, "Lop", parent=self,
                             parms={"check_message": "GO"})
                self._children.append(n)
                return n

        stage_net = FakeNode("/stage", "lopnet", "Manager")
        sublayer = FakeNode("/stage/sublayer1", "sublayer", "Lop", parent=stage_net)
        sublayer.display_flag = True
        stage_net._children.append(sublayer)

        status_log = []
        selected = [sublayer]
        fake_hou = types.SimpleNamespace(
            selectedNodes=lambda: list(selected),
            node=lambda p: stage_net if p == "/stage" else None,
            paneTabType=types.SimpleNamespace(NetworkEditor=1, PythonPanel=2),
            severityType=types.SimpleNamespace(Message=0, Warning=1, Error=2),
            ui=types.SimpleNamespace(
                setStatusMessage=lambda msg, severity=None: status_log.append((msg, severity)),
                paneTabOfType=lambda _t: None,
            ),
            _stage_net=stage_net,
            _sublayer=sublayer,
            _selected=selected,
            _status=status_log,
        )
        return fake_hou

    def test_shelf_add_check_export_and_preview_callbacks(self):
        fake_hou = self._make_fake_hou()
        fake_hou_node = types.SimpleNamespace(
            on_check=mock.Mock(),
            on_export=mock.Mock(),
            on_preview=mock.Mock(),
        )
        fake_panel_widget = mock.Mock()

        import webexport

        with mock.patch.dict(sys.modules, {"hou": fake_hou, "webexport.hou_node": fake_hou_node}), \
             mock.patch.object(webexport, "hou_node", fake_hou_node, create=True):
            sys.modules.pop("webexport_shelf", None)
            shelf = importlib.import_module("webexport_shelf")
            try:
                # 1. Add Web Export wires upstream selected LOP into input 0
                created = shelf.add_web_export_node({})
                self.assertIsNotNone(created)
                self.assertEqual(created.type().name(), shelf.HDA_TYPE)
                self.assertIs(created.inputs_wired.get(0), fake_hou._sublayer)
                self.assertTrue(created.display_flag)
                self.assertTrue(created.selected)

                # 2. Check invokes hou_node.on_check and focuses panel on the created node
                fake_hou._selected[:] = [created]
                with mock.patch("webexport.panel.open_floating_panel", return_value=fake_panel_widget):
                    shelf.check_stage({})
                fake_hou_node.on_check.assert_called_once_with({"node": created})
                fake_panel_widget.set_target_node.assert_called_once_with(created, auto_check=True)

                # 3. Export invokes hou_node.on_export
                with mock.patch("webexport.panel.open_floating_panel", return_value=fake_panel_widget):
                    shelf.export_stage({})
                fake_hou_node.on_export.assert_called_once_with({"node": created})

                # 4. Preview invokes hou_node.on_preview
                shelf.preview_viewer({})
                fake_hou_node.on_preview.assert_called_once_with({"node": created})
            finally:
                sys.modules.pop("webexport_shelf", None)


if __name__ == "__main__":
    unittest.main()
