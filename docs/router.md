# Operator's Card: build router

**One question:** where should each build task run, before an agent spends tokens on it?

## Run

With any Python 3; no USD needed:

```
python tools/build_router.py plan/tasks.json
```

| Flag | What it adds |
|---|---|
| `--md board.md` | Writes the board as Markdown |
| `--json routes.json` | Writes every route as JSON |
| `--check plan/tasks.expected.json` | Scores the routes against labels written before any Jev run |

`npm run route` runs the check version.

## The three lanes

| Lane | Means |
|---|---|
| **CLOUD** | A cloud agent can do it alone |
| **DESK** | It needs Joe's machine: Houdini, the 4090, a real export |
| **DECISION** | It needs Joe's judgment before anyone builds |

## How it decides

**Jev answers three questions per task, all in one request.** Where it runs is a Choice between cloud, desk and decision. What must exist first is a Choice between spike, fixture, mock and none. How big it is is a Score on four concrete levels.

**Code owns the policy.** If Jev is under 0.5 confident about where a task runs, the task goes to the decision lane, marked `JEV UNSURE: was <lane>`. Unsure tasks go to a person, never to an agent. A cloud task whose size scores under 1.5, with at least 0.5 confidence, goes to `ROUTER_SMALL_MODEL` (default `haiku`). Every other cloud task goes to `ROUTER_BIG_MODEL` (default `opus`). Desk and decision tasks run no model.

Without a key, the router falls back to keyword guesses and labels every row a guess. The guesses agree with the expected labels on 14 of 16 tasks.

## Add a task

Append `{"id", "title", "detail", "after"}` to `plan/tasks.json`. `after` lists the task ids it depends on. If you want the check to cover it, add your own label to `plan/tasks.expected.json` *before* running the router, so the label stays independent.

## Jev setup

The same key as the census, from the environment only: `TYPESAFE_API_KEY`. `TYPESAFE_URL` and `TYPESAFE_MODEL` override the endpoint and model if you need to.

## Decisions

**One question:** can an agent settle this design choice, or does it go to Joe?

```
python tools/decide.py plan/decisions/<id>.json --record plan/decisions.jsonl
```

A decision file states the decision, the constraints, the evidence and the options, plus any yes/no checks on the evidence. `plan/decisions/w3-in-memory-export.json` is the worked example.

**Jev answers in one request.** Which option to choose is a Choice, with "neither" always offered. Whether the choice needs a judgment the facts don't settle is a Noul (a yes/no probability). Each check is another Noul.

**Code owns the policy.** It acts on the probability of the chosen option, not on Jev's confidence, and it reads a Noul at or above 0.7 as yes and at or below 0.3 as no. Those are TypeSafe's documented starting points. **ADOPT** needs a choice other than "neither", a probability of at least 0.7, and a needs-a-person score of at most 0.3. Everything else is **ASK JOE**, including no key and a failed request, so nothing is adopted silently. The exit code is 0 for ADOPT and 1 for ASK JOE.

**The record keeps the raw probabilities**, one JSON line per run, so the thresholds can change later without asking Jev again. Never run it on a decision Joe owns outright (scope, money, licensing, taste); those go straight to him.

| Command | Use |
|---|---|
| `python tools/build_router.py plan/web-export-tasks.json` | Route the wave 2 tasks of the Web Export blueprint |
| `python tools/decide.py <file> --json` | Print the full record instead of the report |
