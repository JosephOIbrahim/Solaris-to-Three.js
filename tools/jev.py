"""
jev.py: one small door to TypeSafe's Jev, shared by the census and the build router.

    answers, note = ask(questions, state)

`questions` and `state` follow TypeSafe's HTTP API (POST /v1/systemone): questions are
keyed by an ID you choose; Choice uses a criteria object, Score an ordered criteria list.
Fails open: with no TYPESAFE_API_KEY, or on any error, `answers` is None and `note` says
why, so callers fall back to their own rules. Retries 429/529 with backoff.
Stdlib only, so it runs in hython as well as plain Python.
"""
import json, os, time, urllib.error, urllib.request

URL = os.environ.get("TYPESAFE_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")


def ask(questions, state, timeout=30):
    key = os.environ.get("TYPESAFE_API_KEY")
    if not questions:
        return {}, ""
    if not key:
        return None, "Jev off (no TYPESAFE_API_KEY)"
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)["answers"], f"Jev {MODEL}"
        except urllib.error.HTTPError as e:
            e.close()  # release the connection; Python 3.14 warns about an unclosed error
            if e.code in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None, f"Jev unavailable (HTTP {e.code})"
        except Exception as e:  # network, timeout, malformed reply
            return None, f"Jev unavailable ({type(e).__name__})"
    return None, "Jev unavailable"
