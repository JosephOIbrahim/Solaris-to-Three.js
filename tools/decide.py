"""
decide.py: one design decision, judged by Jev, settled by code.

    python tools/decide.py plan/decisions/<id>.json
    python tools/decide.py plan/decisions/<id>.json --record plan/decisions.jsonl

A decision file states what is being decided and gives Jev the facts:

    {"id": "w3-in-memory-export",
     "decision": "what is being decided, in one sentence",
     "constraints": {"name": "a rule the choice must respect", ...},
     "evidence": ["an observed fact", ...],
     "options": {"key": "what this option does", ...},
     "checks": {"key": "a yes/no question about the evidence", ...}}      (checks are optional)

Jev answers, in one request:
  approach      Choice  one of the options, or "neither"
  needs_person  Noul    does the choice need a judgment the facts don't settle
  check_<key>   Noul    each yes/no question in "checks"

Code owns the policy. The thresholds are TypeSafe's documented starting points
(docs.typesafe.ai/confidence and /primitives/noul): act on the probability of the option
you will act on, treat a Noul at or above 0.7 as yes and at or below 0.3 as no, and send
anything between to a person.
  ADOPT    the choice is not "neither", its probability is at least 0.7,
           and needs_person is at most 0.3.
  ASK JOE  everything else: a weak or "neither" choice, needs_person above 0.3,
           no TYPESAFE_API_KEY, or a failed request. It never adopts silently.
The record keeps the raw probabilities, so the thresholds can change later without asking
Jev again. Exit code 0 means ADOPT and 1 means ASK JOE, so a script can gate on it.
Never feed it a decision Joe owns outright (scope, money, licensing, taste): those go to him.
"""
import argparse, datetime, json, sys
from pathlib import Path

import jev  # tools/jev.py: the shared TypeSafe door

ACT = 0.7       # probability of the chosen option needed to act on it
NOUL_YES = 0.7  # a Noul at or above this is a yes
NOUL_NO = 0.3   # a Noul at or below this is a no; anything between is a person's call


def questions_for(d):
    options = {k: f"The option described in `options.{k}`." for k in d["options"]}
    options["neither"] = "Neither option meets `constraints` on this `evidence`; a person must design another approach."
    q = {
        "approach": {"type": "choice", "criteria": options,
                     "instructions": "Given `decision`, `constraints` and `evidence`, which option in `options` should be chosen?"},
        "needs_person": {"type": "noul",
                         "instructions": "Does choosing between the options in `options` need a judgment that `constraints` and "
                                         "`evidence` do not settle, such as scope, money, licensing, taste, or a trade-off only "
                                         "the project owner can weigh?"},
    }
    for key, text in (d.get("checks") or {}).items():
        q[f"check_{key}"] = {"type": "noul", "instructions": text}
    return q


def noul(answer):
    """A Noul's probability of yes, or 0.5 (a person's call) if the field is missing."""
    try:
        return float(answer["noul"])
    except (KeyError, TypeError, ValueError):
        return 0.5


def word(p):
    return "yes" if p >= NOUL_YES else "no" if p <= NOUL_NO else "unsure"


def decide(d):
    state = {k: d[k] for k in ("decision", "constraints", "options", "evidence") if k in d}
    answers, note = jev.ask(questions_for(d), state)
    rec = {"id": d["id"], "date": datetime.date.today().isoformat(), "decided_by": "jev", "jev": note}
    if not answers:
        rec.update(verdict="ASK JOE", reasons=[f"no judgment ({note})"])
        return rec
    a = jev.answer_for(answers, "approach")
    if a is None:
        rec.update(verdict="ASK JOE", reasons=[f"incomplete reply: missing approach ({note})"])
        return rec
    raw_probs = a.get("probabilities") if isinstance(a.get("probabilities"), dict) else {}
    probs = {k: round(float(v), 3) for k, v in raw_probs.items()}
    choice = a.get("choice")
    p = probs.get(choice)
    need = noul(jev.answer_for(answers, "needs_person"))
    checks = {k[len("check_"):]: round(noul(v), 3) for k, v in answers.items() if k.startswith("check_")}
    reasons = []
    if choice == "neither":
        reasons.append("Jev chose neither option")
    if p is None or p < ACT:
        reasons.append(f"the probability of {choice} is {'missing' if p is None else f'{p:.2f}'}, under {ACT}")
    if need > NOUL_NO:
        reasons.append(f"needs_person is {need:.2f}, above {NOUL_NO} ({word(need)})")
    rec.update(choice=choice, probability=p, confidence=round(float(a.get("confidence") or 0), 3), probabilities=probs,
               needs_person=round(need, 3), checks=checks, verdict="ASK JOE" if reasons else "ADOPT", reasons=reasons)
    return rec


def report(rec):
    W = 72
    lines = [f"DECISION   {rec['id']}", "-" * W]
    lead = "; ".join(rec["reasons"]) if rec["reasons"] else "Jev's choice clears every threshold."
    lines.append(f"{rec['verdict']:<11}{lead}")
    if "choice" in rec:
        others = ", ".join(f"{k} {v:.2f}" for k, v in sorted(rec["probabilities"].items(), key=lambda kv: -kv[1])
                           if k != rec["choice"])
        lines += ["", f"  Jev picks   {rec['choice']}  {rec['probability'] if rec['probability'] is not None else '?'}"
                      + (f"   ({others})" if others else ""),
                  f"  needs you   {rec['needs_person']:.2f}  {word(rec['needs_person'])}"]
        for k, v in rec["checks"].items():
            lines.append(f"  check       {k}  {v:.2f}  {word(v)}")
    lines += ["", f"  {rec['jev']}", "-" * W]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Judge one design decision with Jev; code decides ADOPT or ASK JOE.")
    ap.add_argument("decision", help="decision file: id, decision, constraints, evidence, options, checks")
    ap.add_argument("--record", help="append the result, with raw probabilities, to this JSONL file")
    ap.add_argument("--json", action="store_true", help="print the record as JSON instead of the report")
    a = ap.parse_args(argv)
    rec = decide(json.loads(Path(a.decision).read_text(encoding="utf-8")))
    print(json.dumps(rec, indent=2) if a.json else report(rec))
    if a.record:
        with open(a.record, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec) + "\n")
    return 0 if rec["verdict"] == "ADOPT" else 1


if __name__ == "__main__":
    sys.exit(main())
