"""Open-weight model providers: Ollama and any OpenAI-compatible server.

Why these two: they cover ~everything a $0 operation can run — Ollama for
laptop/desktop, and the OpenAI wire format for vLLM, llama.cpp's server, LM
Studio, LocalAI, TGI and hosted open-weight endpoints.

Everything here is stdlib urllib. No SDK to install, nothing to `pip install`
before the agent works. The whole point of the local-model path is that the
inference cost is zero, so the dependency cost had better be zero too.

Two behaviours worth calling out:

* `preflight()` is a real capability probe (is the server up, is the model
  pulled, does it answer) rather than a config read. It is what makes
  `wf work` able to say "the model is not there" instead of emitting prose that
  was never generated.
* `RepairRequired` is raised by the pipeline, not here — this module never
  decides whether text is acceptable, it only moves text.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_OLLAMA = os.environ.get("WEALFORGE_OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_OPENAI = os.environ.get("WEALFORGE_OPENAI_BASE_URL", "http://127.0.0.1:8080/v1")
TIMEOUT_S = int(os.environ.get("WEALFORGE_HTTP_TIMEOUT", "180"))
RETRIES = int(os.environ.get("WEALFORGE_HTTP_RETRIES", "2"))


class ProviderError(RuntimeError):
    pass


class Unreachable(ProviderError):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_usd(self) -> float:
        return 0.0          # open weights on your own hardware: the whole thesis


@dataclass
class Reply:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def words(self) -> int:
        return len(re.findall(r"[A-Za-z0-9'\u2019\-]+", self.text))


def _request(url: str, payload: dict[str, Any] | None, headers: dict[str, str],
             method: str = "POST", timeout: int = TIMEOUT_S, retries: int | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"content-type": "application/json", **headers})
    budget = RETRIES if retries is None else retries
    last: Exception | None = None
    for attempt in range(budget + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 (operator-set URL)
                body = r.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip().startswith(("{", "[")) else {"_raw": body}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < budget:
                time.sleep(1.5 * (attempt + 1))       # backoff; a local server under load recovers
                last = e
                continue
            raise ProviderError(f"{url} -> HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            if attempt < budget:
                time.sleep(0.8 * (attempt + 1))
                continue
    raise Unreachable(f"{url} unreachable: {last}")


# --------------------------------------------------------------- backends ----
@dataclass
class Backend:
    """A configured endpoint. `kind` decides the wire format only."""
    kind: str                                   # "ollama" | "openai" | "cmd"
    model: str
    base_url: str = ""
    api_key_env: str = ""
    temperature: float = 0.4
    max_tokens: int = 3000
    json_mode: bool = False
    timeout: int = TIMEOUT_S
    extra_headers: dict[str, str] = field(default_factory=dict)

    def headers(self) -> dict[str, str]:
        h = dict(self.extra_headers)
        if self.kind == "openai":
            key = os.environ.get(self.api_key_env or "WEALFORGE_LOCAL_KEY", "")
            if key:
                h["authorization"] = f"Bearer {key}"
        return h

    # -- generation ---------------------------------------------------------
    def generate(self, prompt: str, system: str = "", images: list[str] | None = None) -> Reply:
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        if self.kind == "ollama":
            payload: dict[str, Any] = {"model": self.model, "messages": msgs, "stream": False,
                                       "options": {"temperature": self.temperature,
                                                   "num_predict": self.max_tokens}}
            if self.json_mode:
                payload["format"] = "json"
            data = _request(self.base_url.rstrip("/") + "/api/chat", payload, self.headers(),
                            timeout=self.timeout)
            msg = data.get("message") or {}
            return Reply((msg.get("content") or "").strip(), data.get("model") or self.model,
                         Usage(int(data.get("prompt_eval_count") or 0),
                               int(data.get("eval_count") or 0)), data)
        if self.kind == "openai":
            payload = {"model": self.model, "messages": msgs, "temperature": self.temperature,
                       "max_tokens": self.max_tokens}
            if self.json_mode:
                payload["response_format"] = {"type": "json_object"}
            data = _request(self.base_url.rstrip("/") + "/chat/completions", payload,
                            self.headers(), timeout=self.timeout)
            try:
                choice = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise ProviderError(f"malformed completion response: {json.dumps(data)[:300]}") from e
            u = data.get("usage") or {}
            return Reply(choice.strip(), data.get("model") or self.model,
                         Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)), data)
        if self.kind == "anthropic":
            payload = {"model": self.model, "max_tokens": self.max_tokens,
                       **({"system": system} if system else {}),
                       "messages": [{"role": "user", "content": prompt}]}
            data = _request(self.base_url.rstrip("/") + "/v1/messages", payload,
                            {"x-api-key": os.environ.get(self.api_key_env or "ANTHROPIC_API_KEY", ""),
                             "anthropic-version": "2023-06-01", **self.headers()}, timeout=self.timeout)
            u = data.get("usage") or {}
            text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
            return Reply(text, data.get("model") or self.model,
                         Usage(int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)), data)
        if self.kind == "cmd":
            return self._via_cmd(prompt, system)
        raise ProviderError(f"unknown backend kind {self.kind!r}")

    def _via_cmd(self, prompt: str, system: str) -> Reply:
        """Any command reading stdin, writing stdout. The zero-dependency escape hatch
        for a runtime with no HTTP server (llama.cpp CLI, a script, a mock in tests)."""
        import shutil
        import subprocess
        exe = self.model.split()[0]
        if not shutil.which(exe):
            raise Unreachable(f"WEALFORGE_LLM_CMD binary {exe!r} not on PATH")
        full = f"{system}\n\n{prompt}" if system else prompt
        try:
            p = subprocess.run(self.model, shell=True, input=full.encode(),
                               capture_output=True, timeout=self.timeout * 2)
        except subprocess.TimeoutExpired as e:
            raise ProviderError(f"llm cmd timed out after {self.timeout*2}s") from e
        if p.returncode != 0:
            raise ProviderError(f"llm cmd exit {p.returncode}: {p.stderr.decode()[:300]}")
        return Reply(p.stdout.decode("utf-8", "replace").strip(), self.model)

    # -- capability probe ---------------------------------------------------
    def probe(self) -> dict[str, Any]:
        """Real availability check. Returns {ok, reason, model_present, latency_ms, ...}."""
        out: dict[str, Any] = {"kind": self.kind, "model": self.model, "base_url": self.base_url,
                               "ok": False, "reason": "", "model_present": None, "latency_ms": 0}
        if self.kind == "anthropic":
            if not os.environ.get(self.api_key_env or "ANTHROPIC_API_KEY"):
                out["reason"] = "no API key in environment"
                return out
            try:
                out["ok"] = True
                out["reason"] = "key present (not probed: a ping would bill tokens)"
            except Exception as e:            # pragma: no cover
                out["reason"] = str(e)[:200]
            return out
        if self.kind == "cmd":
            import shutil
            out["ok"] = bool(shutil.which(self.model.split()[0]))
            out["reason"] = "command found" if out["ok"] else "command not on PATH"
            return out
        t0 = time.time()
        try:
            if self.kind == "ollama":
                tags = _request(self.base_url.rstrip("/") + "/api/tags", None, self.headers(),
                                method="GET", timeout=8, retries=0)
                names = [m.get("name", "") for m in tags.get("models", [])]
                out["models_seen"] = names[:25]
                out["model_present"] = any(n == self.model or n.split(":")[0] == self.model.split(":")[0]
                                           for n in names)
                if not out["model_present"]:
                    out["reason"] = (f"server is up but model {self.model!r} is not pulled; "
                                     f"run: ollama pull {self.model}")
                    return out
            else:
                try:
                    models = _request(self.base_url.rstrip("/") + "/models", None, self.headers(),
                                      method="GET", timeout=8, retries=0)
                    ids = [m.get("id", "") for m in models.get("data", [])]
                    out["models_seen"] = ids[:25]
                    out["model_present"] = (self.model in ids) if ids else None
                except ProviderError:
                    out["model_present"] = None   # some servers omit /models; not fatal
            reply = self._ping()
            out["ok"] = True
            out["reason"] = "reachable"
            out["sample"] = reply.text[:60]
        except (Unreachable, ProviderError) as e:
            out["reason"] = str(e)[:220]
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out

    def _ping(self) -> Reply:
        saved = self.max_tokens
        self.max_tokens = 24                # a probe must not burn 3k tokens of a local run
        try:
            return self.generate("Reply with exactly: OK")
        finally:
            self.max_tokens = saved


# ---------------------------------------------------------------- builders ----
def ollama(model: str = "qwen3.6:27b", base_url: str = DEFAULT_OLLAMA, **kw: Any) -> Backend:
    return Backend("ollama", model, base_url, **kw)


def openai_compat(model: str, base_url: str = DEFAULT_OPENAI, **kw: Any) -> Backend:
    return Backend("openai", model, base_url, **kw)


def from_env() -> Backend | None:
    """Precedence: WEALFORGE_MODEL (registry id or `kind:model`) → explicit base url →
    Ollama default if that server answers. Returns None when nothing is reachable,
    which the pipeline treats as 'run in offline mode', never as a silent fallback."""
    spec = os.environ.get("WEALFORGE_MODEL", "").strip()
    if spec:
        from .modelhub import resolve
        return resolve(spec)
    if os.environ.get("WEALFORGE_OPENAI_BASE_URL"):
        return openai_compat(os.environ.get("WEALFORGE_OPENAI_MODEL", "local"),
                             os.environ["WEALFORGE_OPENAI_BASE_URL"])
    if os.environ.get("OLLAMA_HOST"):
        url = os.environ["OLLAMA_HOST"]
        if not url.startswith("http"):
            url = "http://" + url
        return ollama(os.environ.get("WEALFORGE_OLLAMA_MODEL", "qwen3.6:27b"), url)
    return None


def extract_json(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction: models wrap objects in prose or fences more often
    than they return bare JSON, and a failed parse shouldn't lose a good draft."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    if start < 0:
        raise ProviderError("no JSON object found in model output")
    depth = 0
    in_str = esc = False
    for i in range(start, len(t)):
        c = t[i]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str and c == "{":
            depth += 1
        elif not in_str and c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ProviderError("unbalanced JSON in model output")


def render_prompt(system: str, user: str) -> str:
    return f"{system.strip()}\n\n{user.strip()}" if system.strip() else user.strip()
