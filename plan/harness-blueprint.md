# Harness blueprint: the dev-workflow harness

Version 1, 2026-11-12. Joseph O. Ibrahim.

Reading path: the one paragraph, the shape, the core rule, then the phases. Ten minutes. Derived from `docs/review-2026-11-12.md`, not from a template.

---

## 01. The one paragraph

A harness is the system around a model that makes it an agent: its tools, permissions, memory, orchestration and evaluation. This one is for **how this repository gets developed**. It serves Joe, who is a solo lighting TD and owns every product and release decision, and it owns one job: a change that lands on `main` must carry evidence that the checks behind it actually ran, and that those checks could have failed.

That job comes straight from the review. Its four blockers and nineteen majors share one shape: a report described a state nobody had verified. A validator that never compared counts, a schema test that skipped silently, a census that assumed, a batch test that could exit 0, a player that ignored its own camera.

The harness exists so that "we checked" and "the check ran and could have failed" are the same statement.

---

## 02. Shape

**A lean code agent with one orchestrator.** It inspects files, runs commands, edits the repo, runs tests and asks Joe before anything risky.

Not a multi-agent system by default. Review lanes already exist and stay available on demand, but they are not part of the core harness, because this session's costliest failures happened *after* the agents reported, when nobody checked their sentences.

If two subsystems ever merge, say so. The harness's own layers are the gate registry, the evidence ledger, the claims checker, the replay runner, the lessons ledger and the permission layer.

---

## 03. The core rule

> **No gate counts unless it has been seen to fail.**

That is the only new idea, and the review proves its value: every false confidence in the tree came from a check that had never been shown to fail on a deliberately broken input.

Everything else is machinery for that rule.

---

## 04. The primitives

| Primitive | What it is | What it replaces |
|---|---|---|
| Gate registry | Every check named once, with its command, timeout and scope | Checks scattered across scripts and never listed |
| Mutation ledger | A record that each gate was run against a broken input and failed | Assuming a green test means the test works |
| Claims ledger | A change declares what it now claims, and which gate proves it | Prose drifting from code between releases |
| Golden tasks | Fixtures that reproduce a named defect, failing before the fix and passing after | Tests written after the fact and never shown to fail |
| Replay runner | Runs the golden tasks against any tree, so a proposed change is measured | Measuring improvement by intuition |
| Lessons ledger | Append-only, provenance-carrying lessons with their evidence | Advice in chat, forgotten between sessions |
| Permission layer | What reads, writes, exports, releases and spends are allowed, and by whom | Implicit trust in whatever runs |

The two Harness² mechanisms map cleanly:

- **Task-level recursion (propose, probe, compose):** propose an edit to the harness itself (a new gate, a sharper prompt, a permission change), probe it by replaying the golden tasks, and adopt it only when the evidence improves.
- **Harnessing-level recursion:** the lessons ledger persists what each task taught, with provenance, and guides the next edit. Weights never move; only the harness does.

---

## 05. Lifecycle

```
request
  -> context: blueprint, handoff, claims, lessons
  -> capabilities: reads, edits, tests, fixtures
  -> gates: run, and each gate has mutation evidence
  -> claims: any new claim must name its gate
  -> write: only inside the repo, never into a user's source stage
  -> verify: replay golden tasks; compare counts; check claims
  -> commit / push: after gates and claims pass
  -> release: Joe's decision, after CI and the browser dispatch
```

Stop states are explicit: waiting on a long run, waiting on approval, blocked on a missing dependency, and stopped because a gate failed.

---

## 06. MVP boundary

Build only this first:

1. The gate registry and its runner, wrapping the checks that already exist (`npm test`, the claims test, `release.py`, the HDA batch test, the schema test, the fixture builders).
2. The mutation ledger, and one deliberate breakage recorded per gate.
3. Six golden tasks, taken from the review's blockers and majors, each failing before its fix and passing after.
4. The claims checker: a change that asserts something new must name the gate that proves it.
5. The replay runner, so a proposed change is measured against the same fixtures.

Not in the MVP: multi-agent orchestration, a durable workflow engine, a UI, or any Harness² installation. Those come later if the evidence says they help.

---

## 07. Phases

### Phase 1: the gate registry and the mutation ledger

Every check is named once, and each is shown to fail on a deliberately broken input. That includes the checks added in the last few releases, because several of them had never been shown to fail.

### Phase 2: the golden tasks

One fixture per defect from the review: an instanced stage, a UV seam, a Z-up centimetre stage, a scaled and oriented PointInstancer, two variant sets, a camera-carrying export, a missing HDR, a schema-invalid X3D, a partial Jev reply. Each is a replayable task with an expected outcome.

### Phase 3: claims and CI

A change declares its claims, the checker requires a gate for each, and CI runs gates, claims and replay on every push. The schema test becomes a real dependency in CI rather than a silent skip, and the player fixtures are regenerated or digest-compared.

### Phase 4: the improvement loop

Propose a harness edit, replay the golden tasks, measure whether fewer defects escape, adopt or revert, and record the lesson. This is the Harness² pattern, without its benchmark suite.

---

## 08. Evaluation and acceptance

The harness is judged on **false-pass rate**: the number of known defects that reach `main` without a gate failing.

Acceptance criteria for Phase 2 and 3:

- Every blocker from the review has a golden task that fails before its fix and passes after.
- Every gate has mutation evidence on record.
- A change with a claim that no gate covers fails the run.
- The X3D schema test runs in CI and fails on a schema-invalid file.
- A change to a writer that alters counts or variants breaks the fixture comparison, not only the unit tests.
- The replay runner reproduces every golden task on a clean checkout.

Failure modes I have to design for:

- A golden fixture that goes stale and keeps passing for the wrong reason.
- Mutation evidence that was never actually run.
- A claims ledger that becomes prose the model writes for itself, which is why it is a file with a schema, not a prompt.
- Replay environments that differ, which is why the schema and the fixtures must be vendored, not fetched.

---

## 09. Guardrails

- No writes to a user's source stage or Houdini prefs, and no Houdini runs without Joe's approval.
- No edits to the Synapse repo.
- No release without Joe's decision, a clean `main`, CI on the exact commit and the browser dispatch.
- No need to spend money on model benchmarks for this harness.
- Lessons carry provenance and a date; nothing is remembered without both.

---

## 10. Open decisions for Joe

1. **Whether Phase 1 is worth it.** The machinery is small, but it is machinery, and it will be touched on every change.
2. **Whether the improvement loop is wanted at all.** A harness that edits its own gates needs the mutation ledger to be trustworthy, or it optimises itself into weaker checks.
3. **Whether the golden tasks live as fixtures in `fixtures/`** or as a separate replay pack, since a few will be deliberately broken stages.
4. **Whether the replay runner gates the release** or only warns, given the four blockers were all found in code that had already shipped.

The harness exists to make the next report true, not to make it longer.
