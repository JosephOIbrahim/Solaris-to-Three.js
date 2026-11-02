"""
build_router.py: before a big model spends tokens, decide where each task runs.

    python tools/build_router.py plan/tasks.json
    python tools/build_router.py plan/tasks.json --md board.md --json routes.json
    python tools/build_router.py plan/tasks.json --check plan/tasks.expected.json

For every task Jev answers three narrow questions, all in one request:
  where     Choice  cloud agent / Joe's desk / Joe's decision
  scaffold  Choice  spike / fixture / mock / none: what must exist before the real work
  effort    Score   four concrete situations, from one-line change to multi-session
Code owns the policy, and it's all here in one place:
  - Jev under 0.5 confident about where a task runs: it goes to Joe, never to an agent.
  - Cloud and small effort: ROUTER_SMALL_MODEL (default haiku). Otherwise ROUTER_BIG_MODEL (default opus).
  - A task that waits on another says so.
Without TYPESAFE_API_KEY it falls back to keyword rules and labels every row as a guess.
"""
import argparse, json, os, re, sys
from pathlib import Path

import jev  # tools/jev.py: the shared TypeSafe door

FLOOR = 0.5
SMALL = os.environ.get("ROUTER_SMALL_MODEL", "haiku")
BIG = os.environ.get("ROUTER_BIG_MODEL", "opus")

WHERE = {
    "cloud": "An AI coding agent in the cloud container described in `environment.cloud` can finish it alone: code, "
             "tests, docs or analysis that need nothing from Joe's machine, accounts or judgment.",
    "desk": "It needs something only on Joe's workstation described in `environment.desk`: Houdini or hython, Karma, "
            "the GPU, Windows, his real scenes or exports, his phone, or acting in his accounts.",
    "decision": "It is a choice only Joe can make: what counts as done, scope, money, licensing, taste, or sending "
                "something in his name.",
}
SCAFFOLD = {
    "spike": "A short throwaway test of behaviour nobody has verified yet (an API, a header, a version, a hardware "
             "result), run before building on it.",
    "fixture": "A small test scene or data set with planted known cases, so the finished work can be checked automatically.",
    "mock": "A local stand-in for an outside service or tool that is not available where the work runs.",
    "none": "Nothing first: the task is small or already well understood, or it is a decision rather than a build.",
}
EFFORT = [
    "A single setting, a one-line change, one command to run, or one short decision.",
    "A focused change to one file, or one short session of checks at the desk.",
    "Several files or steps with tests, done in one working session.",
    "Several working sessions, with unknowns likely to cause rework.",
]
EFFORT_WORD = ["tiny", "small", "medium", "large"]
LANES = [("decision", "YOU", "decisions only you can make"),
         ("desk", "DESK", "your workstation, weekday afternoons"),
         ("cloud", "CLOUD", "agents")]


def questions_for(tasks):
    q = {}
    for t in tasks:
        i = t["id"]
        q[f"{i}_where"] = {"type": "choice", "criteria": WHERE,
                           "instructions": f"Where can the task `tasks.{i}` be done?"}
        q[f"{i}_scaffold"] = {"type": "choice", "criteria": SCAFFOLD,
                              "instructions": f"Before the main work on the task `tasks.{i}` starts, what should exist first?"}
        q[f"{i}_effort"] = {"type": "score", "criteria": EFFORT,
                            "instructions": f"How much work is the task `tasks.{i}`?"}
    return q


def keyword_guess(t):
    text = f"{t['title']} {t['detail']}".lower()
    if re.search(r"\b(choose|pick|decide|define|email|licen[cs]e|terms|price|budget|approve|what counts)\b", text):
        return "decision"
    if re.search(r"houdini|hython|karma|4090|\bgpu\b|windows|phone|joe's (machine|workstation|github|account)|real export|usd rop", text):
        return "desk"
    return "cloud"


def route(plan):
    tasks = plan["tasks"]
    state = {"plan": plan.get("plan", ""), "environment": plan.get("environment", {}),
             "tasks": {t["id"]: {k: t[k] for k in ("title", "detail", "after") if k in t} for t in tasks}}
    answers, note = jev.ask(questions_for(tasks), state)
    rows = []
    for t in tasks:
        i = t["id"]
        row = {"id": i, "title": t["title"], "after": t.get("after", [])}
        if answers:
            w, s, e = answers[f"{i}_where"], answers[f"{i}_scaffold"], answers[f"{i}_effort"]
            row.update(jev_where=w["choice"], where_conf=round(float(w["confidence"]), 2),
                       scaffold=s["choice"] if float(s["confidence"]) >= FLOOR else "?",
                       effort=float(e["score"]), effort_conf=round(float(e["confidence"]), 2))
            row["unsure"] = row["where_conf"] < FLOOR
            row["lane"] = "decision" if row["unsure"] else row["jev_where"]
        else:
            row.update(lane=keyword_guess(t), jev_where=None, where_conf=None, scaffold="?", effort=None,
                       effort_conf=None, unsure=False, guess=True)
        small = row["effort"] is not None and row["effort"] < 1.5 and (row["effort_conf"] or 0) >= FLOOR
        row["model"] = (SMALL if small else BIG) if row["lane"] == "cloud" else "-"
        rows.append(row)
    return rows, note if answers else f"{note}; keyword guesses"


def fit(text, n):
    """Shorten at a word boundary so titles never end mid-word."""
    return text if len(text) <= n else text[:n - 3].rsplit(" ", 1)[0] + "..."


def board(rows, note, title):
    W = 78
    out = [f"BUILD ROUTER   {title}   {len(rows)} tasks", "-" * W]
    for lane, name, desc in LANES:
        n = [r for r in rows if r["lane"] == lane]
        extra = ""
        if lane == "cloud" and n:
            extra = ": " + ", ".join(f"{m} {sum(r['model'] == m for r in n)}" for m in (SMALL, BIG) if any(r["model"] == m for r in n))
        out.append(f"{name:<8}{len(n):>2}   {desc}{extra}")
    out.append("")
    for lane, name, _ in LANES:
        n = [r for r in rows if r["lane"] == lane]
        if not n:
            continue
        out.append(name)
        for r in n:
            eff = "?" if r["effort"] is None else EFFORT_WORD[min(3, round(r["effort"]))] + ("~" if (r["effort_conf"] or 0) < FLOOR else "")
            size = r["model"] if lane == "cloud" else eff
            conf = "guess" if r.get("guess") else f"{r['where_conf']:.2f}"
            flag = "  JEV UNSURE: was " + r["jev_where"] if r["unsure"] else ""
            wait = f"  after {', '.join(r['after'])}" if r["after"] else ""
            out.append(f"  {r['id']}  {fit(r['title'], 40):<40}  {r['scaffold']:<8}{size:<8}{conf:>5}{wait}{flag}")
        out.append("")
    out += [f"  {note}", "-" * W]
    return "\n".join(out)


def markdown(rows, note, title):
    md = [f"# Build board: {title}", "", f"_{note}_", ""]
    for lane, name, desc in LANES:
        n = [r for r in rows if r["lane"] == lane]
        if n:
            md += [f"## {name.title()} ({len(n)}): {desc}", "", "| Task | First | Size | Jev | Waits on |", "|---|---|---|---|---|"]
            for r in n:
                size = r["model"] if lane == "cloud" else ("?" if r["effort"] is None else EFFORT_WORD[min(3, round(r["effort"]))])
                conf = "guess" if r.get("guess") else f"{r['where_conf']:.2f}" + (" (unsure)" if r["unsure"] else "")
                md.append(f"| {r['id']} {r['title']} | {r['scaffold']} | {size} | {conf} | {', '.join(r['after']) or '-'} |")
            md.append("")
    return "\n".join(md)


def main():
    ap = argparse.ArgumentParser(description="Decide where each build task runs before a big model spends tokens.")
    ap.add_argument("tasks", help="tasks.json: plan, environment, tasks[{id,title,detail,after}]")
    ap.add_argument("--md", help="also write the board as Markdown (for the Project or Duet)")
    ap.add_argument("--json", help="also write the routes as JSON")
    ap.add_argument("--check", help="expected lanes {id: lane}; prints how often the router agrees")
    a = ap.parse_args()
    plan = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    rows, note = route(plan)
    title = Path(a.tasks).name
    print(board(rows, note, title))
    if a.md:
        Path(a.md).write_text(markdown(rows, note, title), encoding="utf-8")
    if a.json:
        Path(a.json).write_text(json.dumps({"note": note, "routes": rows}, indent=2), encoding="utf-8")
    if a.check:
        exp = {k: v for k, v in json.loads(Path(a.check).read_text(encoding="utf-8")).items() if not k.startswith("_")}
        miss = [(r["id"], exp[r["id"]], r["lane"]) for r in rows if r["id"] in exp and exp[r["id"]] != r["lane"]]
        print(f"CHECK  agrees on {len(exp) - len(miss)} of {len(exp)}" + "".join(f"\n  {i}: expected {e}, got {g}" for i, e, g in miss))


if __name__ == "__main__":
    sys.exit(main())
