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
