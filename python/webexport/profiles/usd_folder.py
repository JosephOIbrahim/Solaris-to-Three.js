"""USD folder profile: what a stage loses on its way into a self-contained USD folder.

Blueprint 4.2 and 4.6, mission M2. The web census already knows what the browser viewer can't
load, so the profile starts from its findings, bucket for bucket:

    census FIX FIRST            ->  blocks
    census WILL LOOK DIFFERENT  ->  changes
    census WORTH KNOWING        ->  note

Two fix-first findings are the writer's own job, so they become notes ("copied into the export
folder, path made relative"): an absolute path that resolves to a real file, and a file outside
the export folder. Whatever can't be copied stays a block: a missing file, a $VAR path, a UDIM set.

Then the writer's plan adds what the census can't see, because the census reads the stage as it
is composed now: a missing file or a .rat in a variant that isn't selected, an unsaved layer. And
the usd_folder column of 4.6 adds what the census doesn't cover: rect, disk and dome lights are
bridged by the viewer, which is worth knowing and changes no verdict.
"""
from __future__ import annotations

import os
import re

from pxr import Usd

from ..inventory import Inventory, StageFacts, _norm, gather, is_absolute
from ..report import LossItem
from ..validate import census_of, web_census
from ..writers.usd_folder import FORMAT, FolderPlan, plan_folder

COPIED_IN = "copied into the export folder, path made relative"
CANT_COPY = "not found, so it can't be copied in"
ABSOLUTE = "absolute or $VAR asset paths"                    # census labels the writer partly fixes
OUTSIDE = "files outside the export folder (404 on the web)"
SEVERITY = {"red": "blocks", "amber": "changes", "info": "note"}

# census label -> (feature, becomes, fix). A label not listed here keeps its bucket, under a feature
# made from its own words, so a finding the census adds later is never dropped.
TEXTS = {
    "composition errors": ("composition_error", "part of the stage does not load",
                           "fix or remove the broken reference, payload or sublayer"),
    "layer formats the browser can't read": ("layer_format", "not read by the browser's USD",
                                             "convert the layer to .usd, .usda or .usdc"),
    "UDIM textures (no UDIM support)": ("udim_texture", "not loaded: the browser has no UDIM support",
                                        "bake the tiles to one map"),
    "texture formats the browser can't load": ("texture_format", "not loaded by the browser",
                                               "convert to .png, .jpg or .exr (no conversion in v0.2)"),
    "missing files": ("missing_file", "missing on the web too", "fix the path, or put the file there"),
    "MaterialX meshes without UVs (render black)": ("materialx_no_uv", "renders black",
                                                    "give the mesh UVs (primvars:st)"),
    "dome texture not .hdr (the bridge reads .hdr)": ("dome_texture", "no environment: the bridge reads .hdr only",
                                                      "convert the dome texture to .hdr"),
    "meshes with no material (default grey)": ("unbound_mesh", "default grey", "bind a material"),
    "lights approximated as point lights": ("approximated_light", "a point light", None),
    "light types the viewer ignores": ("ignored_light", "dropped", None),
    "prim types untested in the viewer": ("untested_prim_type", "may draw differently; volumes are not drawn", None),
    "renderer-only nodes (fall back to grey)": ("renderer_only_shader", "falls back to grey",
                                                "use MaterialX or UsdPreviewSurface"),
    "shader nodes that need your eyes": ("unrecognised_shader", "may fall back to grey",
                                         "check it is MaterialX or UsdPreviewSurface"),
    "proxy/guide prims: Hydra hides, USDLoader shows": ("proxy_guide_prims", "hidden by Hydra, shown by USDLoader", None),
}
PREFIXES = {  # labels with words that change from run to run
    "untested MaterialX nodes": ("untested_materialx_node", "kept; not yet tested in the viewer", None),
}

ABSOLUTE_PATH = ("absolute_path", CANT_COPY, "point it at a file that exists, or make it relative")
VAR_PATH = ("var_path", "a $VAR the export does not expand", "write the path out, or make it relative")
OUTSIDE_FILE = ("outside_file", CANT_COPY, "fix the path, or put the file there")

# writers.usd_folder.PROBLEMS kind -> (feature, becomes, fix). Every one is a block.
PLAN_TEXTS = {
    "unsaved": ("unsaved_layer", "not saved to disk, so a copy would lose it",
                "save the stage to disk, then export the saved file"),
    "root_format": ("root_format", "can't hold /WebSettings: the root must be editable",
                    "export from a .usd, .usda or .usdc root layer"),
    "layer_format": TEXTS["layer formats the browser can't read"],
    "missing": TEXTS["missing files"],
    "absolute": ABSOLUTE_PATH,
    "var": VAR_PATH,
    "udim": TEXTS["UDIM textures (no UDIM support)"],
    "texture_format": TEXTS["texture formats the browser can't load"],
    "uri": ("uri_path", "a URI: the export copies files only", "put the file in the folder and point at it"),
    "package": ("package_path", "a path into a package: not unpacked", "reference the package itself"),
}

# 4.6, usd_folder column, for what the census doesn't cover: these lights are bridged, not kept.
BRIDGED = {
    "RectLight": ("rect_light", "bridged: drawn as a point light, intensity converted"),
    "DiskLight": ("disk_light", "bridged: drawn as a point light, intensity converted"),
    "DomeLight": ("dome_light", "bridged: becomes the environment, not a light"),
    "DomeLight_1": ("dome_light", "bridged: becomes the environment, not a light"),
}


def _texts(label: str) -> tuple[str, str, str | None]:
    if label in TEXTS:
        return TEXTS[label]
    for prefix, texts in PREFIXES.items():
        if label.startswith(prefix):
            return texts
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"), "", None


class _Items:
    """LossItems keyed by (severity, feature): a second finding of the same kind adds to the first."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], LossItem] = {}

    def add(self, severity: str, texts: tuple[str, str, str | None], count: int, examples=()) -> None:
        if count <= 0:
            return
        feature, becomes, fix = texts
        item = self.items.setdefault((severity, feature), LossItem(severity, feature, 0, [], becomes, fix))
        item.count += count
        for ex in examples:
            if ex and ex not in item.examples and len(item.examples) < 3:
                item.examples.append(str(ex))


def _census_blocked(c: dict) -> set[str]:
    """Paths the census already reports as fix-first and the writer can't fix, so a plan problem for
    the same path isn't counted twice. Mirrors web_census.decide()'s red rules for asset rows."""
    out = set()
    for t in c["textures"]:
        p = t["path"]
        if "$" in p or (is_absolute(p) and not t["resolved"]):
            out.add(p)
        if "<udim>" in p.lower():
            out.add(p)
        if not t["dome"] and "<" not in p and t["ext"] not in web_census.WEB_TEXTURES:
            out.add(p)
        if not t["resolved"] and "<" not in p and not is_absolute(p):
            out.add(p)
    for error in c["errors"]:
        out.update(re.findall(r"@([^@]+)@", error))
    for ext, ids in c["layers"].items():
        if ext not in web_census.WEB_LAYERS:
            out.update(ids)
    return out | {_norm(p) for p in out if os.path.isabs(p)}


def items_for(c: dict, decision: tuple, plan: FolderPlan, inv: Inventory) -> list[LossItem]:
    """The usd_folder LossItems from the census (c and decide()'s answer), the writer's plan and the Inventory."""
    _verdict, red, amber, info, _note = decision
    out = _Items()
    rows = {}
    for t in c["textures"]:
        rows.setdefault(t["path"], []).append(t)
    blocked = _census_blocked(c)

    for bucket, findings in (("red", red), ("amber", amber), ("info", info)):
        for label, count, example in findings:
            if bucket == "red" and label == ABSOLUTE:
                # Absolute paths that resolve are copied in (the plan's note below); the rest can't be.
                paths = [p for p in rows if is_absolute(p) or "$" in p]
                var = [p for p in paths if "$" in p]
                missing = [p for p in paths if "$" not in p and "<udim>" not in p.lower()
                           and not any(r["resolved"] for r in rows[p])]
                out.add("blocks", VAR_PATH, len(var), var)
                out.add("blocks", ABSOLUTE_PATH, len(missing), missing)
            elif bucket == "red" and label == OUTSIDE:
                # Outside files are copied in (the plan's note below). An outside path that resolves to
                # nothing can't be; the census already blocks a missing file or a UDIM set under its own
                # label, so only what no other label holds is a block here.
                lost = [p for p in c["outside"] if p in rows and p not in blocked
                        and not any(r["resolved"] for r in rows[p])]
                out.add("blocks", OUTSIDE_FILE, len(lost), lost)
            else:
                out.add(SEVERITY[bucket], _texts(label), count, [example])

    for problem in plan.problems:
        if not ({problem.path, problem.where, _norm(problem.where) if problem.where else ""} & blocked):
            out.add("blocks", PLAN_TEXTS[problem.kind], 1, [problem.path])

    for reason, texts in (("absolute", ABSOLUTE_PATH), ("outside", OUTSIDE_FILE)):
        copied = plan.copied_in.get(reason, {})
        out.add("note", (texts[0], COPIED_IN, None), len(copied), list(copied.values()))

    # A dome whose texture the census already flags (not .hdr) gets that change, not a "bridged" note.
    dome_flagged = {t["path"] for t in c["textures"] if t["dome"] and t["ext"] not in web_census.DOME_TEXTURES}
    for light in inv.lights:
        if light.type in BRIDGED and not (light.texture and light.texture in dome_flagged):
            feature, becomes = BRIDGED[light.type]
            out.add("note", (feature, becomes, None), 1, [light.path])

    order = {"blocks": 0, "changes": 1, "note": 2}
    return sorted(out.items.values(), key=lambda i: order[i.severity])


class UsdFolderProfile:
    """What a stage keeps, changes and loses in a USD folder (blueprint 4.2).

    Pass the live stage (and the StageFacts the caller already gathered, and the writer's plan)
    so nothing is read twice. Without them, assess() opens inv.root_layer itself. After assess(),
    .census, .decision, .plan and .jev_note hold what it used.
    """
    name = FORMAT
    extensions = (".usda", ".usdc", ".usd")  # the root layer keeps its source format

    def __init__(self, stage: Usd.Stage | None = None, facts: StageFacts | None = None,
                 plan: FolderPlan | None = None, jev_on: bool = True) -> None:
        self.stage, self.facts, self.plan, self.jev_on = stage, facts, plan, jev_on
        self.census: dict | None = None
        self.decision: tuple | None = None
        self.jev_note = ""

    def assess(self, inv: Inventory) -> list[LossItem]:
        stage = self.stage or Usd.Stage.Open(inv.root_layer)
        if not stage:
            raise ValueError(f"could not open {inv.root_layer}")
        facts = self.facts if self.facts is not None else gather(stage, track_types=web_census.UNTESTED_TYPES)
        self.census = census_of(stage, facts)
        self.decision = web_census.decide(self.census, jev_on=self.jev_on)
        self.jev_note = self.decision[4]
        if self.plan is None:
            self.plan = plan_folder(stage.GetRootLayer())
        return items_for(self.census, self.decision, self.plan, inv)
