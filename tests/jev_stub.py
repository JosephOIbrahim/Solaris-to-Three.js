"""A local stand-in for TypeSafe's POST /v1/systemone, used by the tests.

It answers in the documented response shapes:
  Choice -> {"type": "choice", "choice", "confidence", "probabilities"}
  Score  -> {"type": "score", "score", "confidence", "legend", "probabilities"}
`decide(question_id, question, state)` returns (option, top_probability) for a Choice and
(score, confidence) for a Score. `fail_first` makes the first request return that HTTP status.
This checks our plumbing and policy, not Jev's judgment.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class JevStub:
    def __init__(self, decide, fail_first=None):
        self.decide, self.fail_first, self.requests = decide, fail_first, []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/systemone"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def _handler(stub):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"auth": self.headers.get("Authorization"), "body": body})
                if stub.fail_first and len(stub.requests) == 1:
                    self.send_response(stub.fail_first)
                    self.end_headers()
                    return
                answers = {}
                for qid, q in body["questions"].items():
                    if q["type"] == "choice":
                        options = list(q["criteria"])
                        pick, p = stub.decide(qid, q, body["state"])
                        n = len(options)
                        answers[qid] = {"type": "choice", "choice": pick, "confidence": (p - 1 / n) / (1 - 1 / n),
                                        "probabilities": {o: p if o == pick else (1 - p) / (n - 1) for o in options}}
                    else:
                        levels = q["criteria"]
                        score, confidence = stub.decide(qid, q, body["state"])
                        answers[qid] = {"type": "score", "score": score, "confidence": confidence,
                                        "legend": {str(i): level for i, level in enumerate(levels)},
                                        "probabilities": {str(i): 1 / len(levels) for i in range(len(levels))}}
                out = json.dumps({"model": "jev-stub", "answers": answers,
                                  "usage": {"input_tokens": 0, "output_tokens": 0}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)

        return Handler
