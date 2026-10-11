"""Provider adapter. Optional by design.

The agent's value does not depend on a paid API: scoring, gating, royalty math,
ledgers, roadmaps and briefs all run offline. This module exists for the one step
that genuinely wants a text generator — turning a brief into prose — and it is
deliberately unable to *pretend* it did that when no provider is configured.

Priority:
  1. WEALFORGE_LLM_CMD   an operator-supplied shell command reading prompt on stdin,
                         writing text on stdout. Free path: a local model, an
                         `llm` CLI, a script that shells to anything.
  2. ANTHROPIC_API_KEY   Messages API.
  3. OPENAI_API_KEY      Chat Completions API (also works against any
                         OpenAI-compatible base_url, incl. a local server).
  4. nothing             Unavailable. `mode == "offline"`.

Spending note: keys are read from the environment at call time and never
persisted. Requests go out with `max_tokens` capped so a runaway loop cannot burn
a provider quota, and every call is recorded in the ledger with its token usage
so cost is visible in the same place as the work.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MODEL = {"anthropic": "claude-sonnet-4-5", "openai": "gpt-5-mini"}
MAX_TOKENS = 4000
TIMEOUT_S = 120


class LLMUnavailable(RuntimeError):
    """Raised when prose was requested but no provider is configured.

    The alternative — silently emitting boilerplate and calling it a draft — is
    how a content operation ends up shipping thin work that gets it removed.
    """


@dataclass
class Result:
    text: str
    provider: str
    model: str
    in_tokens: int = 0
    out_tokens: int = 0
    cost_note: str = "configure pricing in config/providers.json to estimate"


@dataclass
class Provider:
    mode: str                                   # "offline" | "cmd" | "anthropic" | "openai"
    model: str = ""
    base_url: str = ""
    cmd: str = ""
    env_used: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.mode != "offline"

    @classmethod
    def detect(cls, force: str | None = None) -> "Provider":
        env = {k: v for k, v in os.environ.items() if k.startswith(("WEALFORGE_", "ANTHROPIC_", "OPENAI_"))}
        base = os.environ.get("WEALFORGE_LLM_BASE_URL", "")
        model = os.environ.get("WEALFORGE_LLM_MODEL", "")
        cmd = os.environ.get("WEALFORGE_LLM_CMD", "")
        chosen = force or (
            "cmd" if cmd and shutil.which(cmd.split()[0]) else
            "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else
            "openai" if (os.environ.get("OPENAI_API_KEY") or base) else "offline")
        if chosen == "cmd":
            if not cmd:
                raise LLMUnavailable("WEALFORGE_LLM_CMD requested but unset")
            return cls("cmd", model or "local", base, cmd, sorted(env))
        if chosen == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise LLMUnavailable("ANTHROPIC_API_KEY not set")
            return cls("anthropic", model or DEFAULT_MODEL["anthropic"],
                       base or "https://api.anthropic.com", "", sorted(env))
        if chosen == "openai":
            key = os.environ.get("OPENAI_API_KEY", "")
            if not key and not base:
                raise LLMUnavailable("OPENAI_API_KEY not set and no base_url given")
            return cls("openai", model or DEFAULT_MODEL["openai"],
                       base or "https://api.openai.com/v1", "", sorted(env))
        return cls("offline", "", "", "", sorted(env))

    # ------------------------------------------------------------- calls ----
    def complete(self, prompt: str, system: str = "", max_tokens: int = MAX_TOKENS) -> Result:
        if self.mode == "offline":
            raise LLMUnavailable(
                "no provider configured. Two free options: (a) set WEALFORGE_LLM_CMD to any "
                "command that reads a prompt on stdin and writes text to stdout — including a "
                "local model — or (b) run `wf build --emit-prompt FILE` and paste the brief into "
                "a chat UI yourself, then `wf build --from-file`. Drafting is the only step that "
                "needs this; scanning, gating, math and packaging run offline.")
        if self.mode == "cmd":
            return self._via_cmd(prompt, system, max_tokens)
        if self.mode == "anthropic":
            return self._via_anthropic(prompt, system, max_tokens)
        return self._via_openai(prompt, system, max_tokens)

    def _via_cmd(self, prompt: str, system: str, max_tokens: int) -> Result:
        full = f"{system}\n\n{prompt}" if system else prompt
        try:
            proc = subprocess.run(self.cmd, shell=True, input=full.encode(), capture_output=True,
                                  timeout=TIMEOUT_S * 4)
        except subprocess.TimeoutExpired as e:
            raise LLMUnavailable(f"WEALFORGE_LLM_CMD timed out after {TIMEOUT_S*4}s") from e
        if proc.returncode != 0:
            raise LLMUnavailable(f"llm cmd exited {proc.returncode}: {proc.stderr.decode()[:400]}")
        text = proc.stdout.decode("utf-8", "replace").strip()
        if not text:
            raise LLMUnavailable("llm cmd produced no output")
        return Result(text, "cmd", self.model or self.cmd, 0, 0,
                      "no API cost; local compute")

    def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={**headers, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:   # noqa: S310 - https, operator-set
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise LLMUnavailable(f"{url} -> HTTP {e.code}: {e.read().decode()[:300]}") from e
        except urllib.error.URLError as e:
            raise LLMUnavailable(f"{url} unreachable: {e.reason}") from e

    def _via_anthropic(self, prompt: str, system: str, max_tokens: int) -> Result:
        data = self._post(f"{self.base_url}/v1/messages",
                          {"model": self.model, "max_tokens": max_tokens,
                           **({"system": system} if system else {}),
                           "messages": [{"role": "user", "content": prompt}]},
                          {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        usage = data.get("usage", {})
        return Result("".join(b.get("text", "") for b in data.get("content", [])).strip(),
                      "anthropic", self.model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))

    def _via_openai(self, prompt: str, system: str, max_tokens: int) -> Result:
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        data = self._post(f"{self.base_url}/chat/completions",
                          {"model": self.model, "max_output_tokens": max_tokens, "messages": msgs},
                          {"authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"})
        usage = data.get("usage", {})
        return Result(data["choices"][0]["message"]["content"].strip(), "openai", self.model,
                      usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


def brief_to_prompt(brief: str, section: str, word_target: int) -> str:
    """The drafting prompt, kept in code so it is versioned and reviewable."""
    return (
        "You are drafting one section of a larger work. Constraints, all binding:\n"
        "- No medical, legal or financial advice; no diagnosis or treatment claims; no supplement "
        "dosing. If the topic needs it, say 'ask your pharmacist/clinician' and move on.\n"
        "- No invented credentials. Do not claim to be a clinician.\n"
        "- No income promises, no hype, no 'in today's fast-paced world' openings.\n"
        "- Be specific: named numbers, worked examples, real constraints, concrete steps.\n"
        "- Vary sentence length. End with one action the reader can take tonight.\n"
        f"- Write ONLY the section named below, ~{word_target} words, as Markdown starting at H2.\n\n"
        f"WORK BRIEF:\n{brief}\n\nSECTION TO WRITE: {section}\n")


def prompt_to_file(path: str, brief: str, section: str, word_target: int) -> str:
    """Write the prompt for a human to paste anywhere they like. Zero cost, zero keys."""
    from pathlib import Path
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(brief_to_prompt(brief, section, word_target), encoding="utf-8")
    return str(p)
