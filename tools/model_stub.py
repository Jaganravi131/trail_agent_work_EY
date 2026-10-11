#!/usr/bin/env python3
"""A local stand-in for Ollama / vLLM, for testing the integration without a GPU.

    python3 tools/model_stub.py --port 11434 [--bad-first] [--from-file responses.json]
    export WEALFORGE_OLLAMA_URL=http://127.0.0.1:11434
    ./wf model && ./wf work --verbose && ./wf eval

It speaks the real wire formats (`/api/tags`, `/api/chat`, and OpenAI-compat
`/v1/models`, `/v1/chat/completions`) and returns real text: by default it answers a
drafting request with an approved section from this repo, so you can watch the whole
loop — prompt, generation, gate, ledger — move without pulling 15 GB of weights.

`--bad-first` plants a credential claim plus an income promise in the first draft of each
section and returns the clean text afterwards. That is the flag to use when you want to
see the repair loop work, because it produces exactly one gate error, exactly once.

This is a test fixture, not a product. It is not in `wealforge/` because the runtime
package must stay importable-with-nothing, and it deliberately does not try to be a
language model: nothing here says anything about the agent's *quality*, only about
whether the plumbing between the agent and a model server is intact.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

STATE = {"bad_first": False, "seeded": set(), "responses": None, "calls": 0}

PLANT = ("\n\nAs a board-certified sleep physician I can promise you will make $500 in your "
         "first week with this system.\n")


def approved_section(prompt: str) -> str:
    """Pick a real, gate-passing section from the repo whose heading best matches the request."""
    from wealforge import dataset

    wanted = ""
    m = re.search(r"SECTION TO WRITE:\s*(.+)", prompt, re.S)
    if m:
        wanted = m.group(1).strip().splitlines()[0].strip("'\"")
    cands: list[tuple[int, str]] = []
    for f in list((REPO / "01-book/chapters").glob("*.md")) + list((REPO / "04-seo/articles").glob("*.md")):
        for title, body in dataset.split_sections(f.read_text(encoding="utf-8", errors="ignore")):
            if len(body) < 500:
                continue
            overlap = len(set(w.lower() for w in re.findall(r"\w+", wanted.lower())) &
                          set(w.lower() for w in re.findall(r"\w+", title.lower())))
            cands.append((overlap, body))
    if not cands:
        return "## Stub\n\nNo repo sections available to serve. This is a plumbing test only.\n"
    cands.sort(key=lambda x: -x[0])
    return cands[0][1][:4000]


def answer(prompt: str) -> str:
    STATE["calls"] += 1
    if "Reply with exactly" in prompt:
        return "OK"
    if "FAILED the pre-publication compliance check" in prompt:
        key = re.search(r"SECTION: (.+)", prompt)
        STATE["seeded"].add(key.group(1)[:40] if key else "any")
        return approved_section(prompt)
    if STATE["bad_first"]:
        key = re.search(r"SECTION TO WRITE: (.+)", prompt)
        k = key.group(1)[:40] if key else "any"
        if k not in STATE["seeded"]:
            STATE["seeded"].add(k)
            return approved_section(prompt).rstrip() + PLANT
    return approved_section(prompt)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                            # noqa: N802
        if self.path.endswith("/api/tags"):
            self._json(200, {"models": [{"name": "qwen3.6:8b", "size": 5_600_000_000,
                                         "details": {"family": "qwen3", "parameter_size": "8B"}},
                                        {"name": "gemma4:26b-a4b", "size": 15_000_000_000}]})
        elif self.path.endswith("/api/version"):
            self._json(200, {"version": "0.0-stub"})
        elif self.path.endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "qwen3.6:8b"}, {"id": "gpt-oss:20b"}]})
        elif self.path == "/stats":
            self._json(200, {"calls": STATE["calls"], "sections_served": len(STATE["seeded"])})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):                                           # noqa: N802
        n = int(self.headers.get("content-length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad json"})
            return
        msgs = req.get("messages") or []
        prompt = "\n\n".join(str(m.get("content", "")) for m in msgs)
        if STATE["responses"]:
            text = STATE["responses"].pop(0) if STATE["responses"] else approved_section(prompt)
        else:
            text = answer(prompt)
        STATE["calls"] += 0
        words = len(text.split())
        if self.path.endswith("/api/chat"):
            payload = {"model": req.get("model", "qwen3.6:8b"), "created_at": "2026-10-11T00:00:00Z",
                       "done": True, "message": {"role": "assistant", "content": text},
                       "prompt_eval_count": len(prompt) // 4, "eval_count": words}
        else:
            payload = {"id": "chatcmpl-stub", "object": "chat.completion",
                       "model": req.get("model", "local"),
                       "choices": [{"index": 0, "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": text}}],
                       "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": words,
                                 "total_tokens": len(prompt) // 4 + words}}
        self._json(200, payload)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=11434)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--bad-first", action="store_true",
                    help="plant one gate violation per section, then serve the clean text")
    ap.add_argument("--responses", help="JSON file: a list of strings to serve in order")
    a = ap.parse_args()
    STATE["bad_first"] = a.bad_first
    if a.responses:
        STATE["responses"] = json.loads(Path(a.responses).read_text(encoding="utf-8"))
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"model stub on http://{a.host}:{a.port} · bad_first={a.bad_first} · "
          f"GET /api/tags · POST /api/chat · GET /stats", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstub stopped")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
