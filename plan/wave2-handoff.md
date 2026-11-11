# Wave 2 handoff: the Web Export HDA

Start wave 2 of the Web Export blueprint from a fresh Claude Code session in `E:\Myth_and_Logic\Software\Solaris-to-Three.js`, using this note, the blueprint and the M5 API brief. Wave 1 is merged and pushed; wave 2 installs the package, proves it in hython and builds the HDA.

Reading path: sections 01 to 03 first (five minutes), then the steps in section 05 one at a time.

---

### 01. Where things stand (2026-11-11)

01. `main` is at `2ada5f7` on GitHub. The last release is v0.1.3; wave 1 sits under `## [Unreleased]` in `CHANGELOG.md`.

02. Wave 1 delivered the pure-pxr core in `python/webexport/` (report, settings, inventory, the USD-folder profile and writer, validate, api), the viewer reading `/WebSettings` (with the stage-file-name title default), the package file and installer (dry run proven), the brand, the calm-format README, the explainer video, and `tools/decide.py`.

03. Checks at `2ada5f7`: 111 Python tests pass, the browser test passes 16 of 16, 43 relative links resolve, and nothing in `python/webexport/` imports `hou`.

---

### 02. Read these, in this order

01. `plan/web-export-blueprint.md`, sections 1 to 4 (the design and the contracts), section 6 (items 6 and 7 are new) and section 8 (decisions, updated today).

02. The M5 API brief: `.token-saver/m5-api-brief.md` in the repo folder, with a rendered copy beside it as `.html`. It is git-ignored on purpose, because it quotes SideFX documentation and source. Read only the section a step needs; the line numbers are in section 05.

03. This note.

---

### 03. Decisions and approvals on record

| Item | Value | Whose call |
|---|---|---|
| Where Web Export lives | this repo | Joe, 2026-11-11 |
| HDA inputs | `min_num_inputs=1` | Joe, 2026-11-11 |
| M5 check builds | 22.0.400, 22.0.492 and 22.0.466 (22.0.459 is not installed) | Joe, 2026-11-11 |
| Explainer video | silent | Joe, 2026-11-11 |
| W3 in-memory export | flatten the layer stack, stage it to a file, reuse the USD-folder writer | Jev ADOPT at the threshold (`plan/decisions/w3-in-memory-export.json`); Joe may veto |
| Approved hython and prefs operations | the package install, `--verify`, the 22.0.400 import check, building the HDA, and testing it on the three builds | Joe, 2026-11-11 |

Anything beyond that list (another hython job, another prefs write, a release) needs Joe's go-ahead first.

---

### 04. Pins for every hython run

01. **Pin the prefs folder.** Two `houdini22.0` folders exist. Your packages live in the OneDrive one, so set `HOUDINI_USER_PREF_DIR=C:/Users/User/OneDrive/Documents/houdini__HVER__` for the install and for every hython run.

02. Run hython with `-B` and `PYTHONDONTWRITEBYTECODE=1`, from the main checkout, never from a worktree: the installed package records the folder it was installed from.

03. hython lives at `C:/Program Files/Side Effects Software/Houdini 22.0.<build>/bin/hython.exe`. Port 5180 is Joe's; tests use their own port, and the Preview test never opens a browser (`WEBEXPORT_NO_BROWSER`).

---

### 05. The steps, routed by Jev

Re-route any time with `python tools/build_router.py plan/web-export-tasks.json`. The board from 2026-11-11:

| Step | Task | Where | First | Size |
|---|---|---|---|---|
| 01 | W1 Install the package and `--verify` | desk | none | small |
| 02 | W2 hython 22.0.400 imports `webexport` | desk | none | small |
| 03 | W3 Export in-memory LOP stages | agent (Opus), can run beside 01 to 04 | spike | |
| 04 | W4 Answer the brief's 43 open questions in hython | desk | spike | small |
| 05 | W5 Build the HDA | desk | spike (W4) | large |
| 06 | W6 Test the HDA on three builds | desk | fixture | medium |
| 07 | W7 Check the Preview button (section 6, item 1) | desk | unsure | small |
| 08 | W8 Merge, test and push | desk | unsure | medium |
| 09 | W9 Operator's card and release 0.2.0 | Joe decides | none | medium |
| 10 | W10 When glTF and X3D start | Joe | none | tiny |

Where each step starts:

01. W1: `python scripts/install_package.py`, then `python scripts/install_package.py --verify` (exit 0).

02. W2: `hython -B -c "import webexport, hou; print(webexport.__file__); print(hou.houdiniPath())"` with the pinned prefs folder; the path must be inside the repo.

03. W3: the recipe at brief line 442, ending in the existing writer instead of `LocalizeAsset` (the decision above). Decide FIX FIRST before writing anything. usd-core tests only.

04. W4: brief lines 378 to 441, one probe per question, on 22.0.400 first.

05. W5: the build sequence at brief line 70, the parameter rows at line 83, the Error LOP badge and `MessageNodes` at line 122, and the "Do not use" list at line 262. Callbacks are one expression each, calling `webexport.hou_node` and nothing else.

06. W6: `tests/hou/test_hda.py` loads `fixtures/stage/shot.usda` with a Sublayer LOP (not a Reference LOP, which moves the cameras and lights).

07. W7: the Preview design at brief line 190; record the result in blueprint section 6, item 1.

---

### 06. Carried over from the wave 1 review

These are minor findings from the adversarial reviewer, folded into wave 2 rather than fixed in a hurry. Three of them now have a recorded decision in `plan/decisions/` (raw probabilities in `plan/decisions.jsonl`): **validation-reporting is ADOPTED** (turn each validation problem into a `LossItem`), while `verdict-consistency` and `report-stage-binding` came back **ASK JOE** and wait on him. Read those files before changing the writer or the report.

01. Principle 9: the writer drops validation problems; surface each as a `LossItem` (W3 touches this code anyway).

02. `LossReport` accepts a verdict that contradicts its items, and `from_json` trusts it; recompute or refuse.

03. The writer trusts a caller's report without binding it to the stage it writes.

04. The contracts gained defaults and helpers (`from_items`, `to_json_str`, `jev_on`, `check`); Joe decides whether section 4 records them or they go.

05. `scripts/install_package.py` quotes Synapse's licence line as "Joseph Ibrahim"; Joe's call whether to add the "Joseph O. Ibrahim" form.

One task is Joe's by hand: upload `docs/assets/social-preview.png` in the repository's Settings, under Social preview.

---

The core is done and pushed; wave 2 is installing it into Houdini and wrapping it in the HDA, one approved step at a time.
