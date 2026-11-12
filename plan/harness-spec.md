# Harness spec: the dev-workflow harness

Version 1, 2026-11-12. Joseph O. Ibrahim. Pairs with `plan/harness-blueprint.md` and answers to `docs/review-2026-11-12.md`.

The harness is a small set of stdlib-Python modules and JSON files inside this repo. It adds no runtime, no framework, and no multi-agent orchestration. It is the machinery that lets a change say what it proves.

---

## 01. Files

```
harness/
  README.md            operator card
  gates.py             the registry and its runner
  gates.json           every check, declared once
  mutations.py         runs a gate against a deliberately broken input
  mutations.jsonl      append-only evidence that a gate can fail
  claims.py            the claims checker
  claims.schema.json   the claims file's schema
  replay.py            runs golden tasks against a tree
  golden/              the fixtures and their expectations
  lessons.jsonl        append-only lessons, with provenance
  __init__.py
```

`harness/` is a directory, not a package on the import path of the exporters. Nothing in `python/webexport/` imports it.

---

## 02. The gate schema

```json
{
  "id": "claims-version",
  "label": "README version equals package.json",
  "command": ["py", "-3.12", "-m", "unittest", "tests.test_claims.VersionClaims"],
  "timeout_seconds": 120,
  "needs": ["pxr"],
  "mutation": "change README.md's version line and expect a failure",
  "verified_on": "2026-11-12"
}
```

Rules:

- Every gate in `gates.json` has a `mutation` entry describing the broken input that must make it fail.
- `mutations.jsonl` records `{id, when, how, exit_code, excerpt}`. A gate without a record cannot count as proven.
- A gate with no record prints `UNPROVEN` and exits non-zero when `run --require-evidence`.

---

## 03. The mutation runner

`python harness/mutations.py <gate-id>`:

1. Makes a temp copy of the fixture or document the gate reads.
2. Applies the gate's declared mutation.
3. Runs the gate's command.
4. Asserts it failed, and appends the evidence.
5. Removes the copy.

It never mutates a file in the tree. A gate whose mutation is not expressible as a file edit (for example "the player ignored the camera") instead uses a fixture in `harness/golden/` that already contains the defect, and the record is the run's failure output.

---

## 04. The claims ledger

A change writes `harness/claims/<change-id>.json`:

```json
{
  "id": "gltf-uv-seams",
  "claims": [
    {"text": "UV seams survive glTF export", "gate": "golden-uv-seam"}
  ]
}
```

`claims.py` checks:

- every claim names a gate in `gates.json` or a golden task id,
- every such gate has a mutation record,
- no claim is left unnamed.

A change with a claim that has no gate fails the run with the message "name the gate that proves it, or remove the claim".

---

## 05. Golden tasks

One directory per task:

```
harness/golden/instanceable-refs/
  stage.usda
  expect.json
  README.md
```

`expect.json`:

```json
{
  "task": "instanceable-refs",
  "defect": "writers skipped geometry inside instance proxies",
  "before": "glb meshes = 0, verdict GO",
  "after": "glb meshes = 2, verdict GO, WITH NOTES",
  "checks": [
    {"kind": "glb-mesh-count", "value": 2},
    {"kind": "x3d-scene-not-empty"}
  ]
}
```

The MVP's six tasks come from the review:

| Task | Proves |
|---|---|
| `attribute-parity` | glTF vertex attributes agree in count |
| `instanceable-refs` | geometry inside instances is written |
| `uv-seam` | per-corner UVs survive in both formats |
| `camera-node` | the exported camera is used by the player |
| `z-up-cm` | axis and unit conversion is applied and reported |
| `schema-in-ci` | the X3D schema test runs and can fail in CI |

Each task's README names the review finding it came from and how to reproduce it by hand.

---

## 06. The replay runner

`python harness/replay.py [--task <id>] [--golden harness/golden]`:

1. For each golden task, exports its stage with the current writers into a temp folder.
2. Runs the task's checks.
3. Prints pass/fail with the evidence, and a summary of which defects still reproduce.
4. Exits non-zero if any task's expectation fails.

It is the measurement for the improvement loop: a proposed harness edit is adopted only when replay gets strictly better, not when it merely sounds better.

---

## 07. Lessons

`harness/lessons.jsonl`, append-only:

```json
{"when": "2026-11-12", "what": "the schema check skipped when lxml was absent", "evidence": "ci.yml:35", "why": "a silent skip is a false green", "supersedes": null}
```

A lesson must name its evidence. A lesson with no evidence is not recorded.

---

## 08. CI wiring

- `npm test` runs the Python suite, then `harness/claims.py`, then `harness/replay.py`.
- CI installs `lxml` alongside `usd-core`, and the schema XSDs are vendored under `harness/golden/schema/`, so the schema test cannot skip for a missing package or a network failure.
- The browser job regenerates the player fixtures before running, or compares a structural digest with the committed ones, so it tests the current writers.
- A change with no gate for its claims fails the run.

---

## 09. Permissions

| Action | Allowed |
|---|---|
| Read anything in the repo | yes |
| Write inside the repo | yes, in the working tree |
| Write into a user's source stage or Houdini prefs | never |
| Run Houdini or hython | only with Joe's go-ahead |
| Push to `main` | after gates, claims and replay pass |
| Release | Joe's decision only |
| Edit the Synapse repo | never |

---

## 10. Acceptance tests for the harness itself

- Deleting a gate's command makes the run fail.
- A gate with no mutation record is `UNPROVEN` and blocks the run.
- A claim without a gate fails.
- A deliberately broken golden fixture makes replay fail.
- `npm test` still passes on a clean tree.
- Replay reproduces every golden defect before its fix and none after.

---

## 11. Out of scope

- A new orchestrator runtime, a queue, a database, a UI.
- Any use of Harness²'s own benchmark suite or model credentials.
- Multi-agent coordination as a default. The review lanes remain an on-demand tool, not the architecture.
- Model fine-tuning of any kind. Weights are fixed; the harness is what changes.
