"""The generation step, now pointed at open-weight models by default.

Priority (checked in this order; the first that actually *answers* wins):

  0. WEALFORGE_MODEL          registry id from `config/models.json`, or `ollama:tag`,
                              `openai:<base_url>`, `cmd:<command>` — the open-weights path.
  1. WEALFORGE_LLM_CMD        any command reading a prompt on stdin, writing text on stdout.
  2. Ollama                   auto-detected on $WEALFORGE_OLLAMA_URL / localhost:11434.
  3. WEALFORGE_OPENAI_BASE_URL any OpenAI-compatible server (vLLM, llama.cpp, LM Studio).
  4. ANTHROPIC_API_KEY / OPENAI_API_KEY   hosted, **opt-in only** (see below).
  5. nothing                  `mode == "offline"` — and that is a supported mode, not an error.

Why local first: the brief is 40 lines, the constraints are 200 tokens, and the output is
900 words of prose that a 2026 open-weight model writes fine. Paying per token for that is
the same mistake as paying for a tool that does arithmetic. The local model is not a
downgrade path — it is the default, and the hosted providers stay wired for the rare case
where reasoning depth actually matters.

Why hosted is opt-in: this operation's constraint is *zero capital*. A weekly cron with a
key in the environment is how a $0 project becomes a surprise invoice, so the paid paths
now require WEALFORGE_ALLOW_PAID=1 as well as the key. Nothing else in the repo spends.

Two things this module refuses to do:
  * it will not pretend it drafted something when no provider answered (raises
    `LLMUnavailable`, and the pipeline records `needs_human` instead of writing filler);
  * it will not treat model output as done. Everything returned here is a *candidate*:
    the gate has the final word, in `pipeline.py`, before a file exists.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from . import providers
from .providers import Backend, ProviderError, Unreachable

DEFAULT_MODEL = {"anthropic": "claude-sonnet-4-5", "openai": "gpt-5-mini"}
MAX_TOKENS = 4000
TIMEOUT_S = 180

SYSTEM_RULES = (
    "You are drafting for a real, named human who will be accountable for every sentence. "
    "Binding constraints:\n"
    "- No medical, legal or financial advice; no diagnosis or treatment claims; no supplement "
    "or drug dosing. Redirect to a pharmacist or clinician and move on.\n"
    "- No invented credentials. Never claim to be a clinician, or to hold any qualification "
    "the operator cannot substantiate.\n"
    "- No income promises, no hype, no 'in today's fast-paced world' openings, no 'unlock', "
    "'game-changer', 'delve' or 'in conclusion'.\n"
    "- No scraping, review manipulation, undisclosed promotion, or copied text.\n"
    "- Be specific: named numbers, one worked example, real constraints, concrete steps.\n"
    "- Vary sentence length. End with one action the reader can take tonight.\n"
    "- Output the requested section only, as Markdown starting at the section heading. "
    "No preamble, no 'Certainly', no apology, no meta-commentary about the task.\n")


@dataclass
class VoiceCard:
    """The free substitute for a fine-tune: 2–4 excerpts of approved prose plus the
    mechanical rules a detector can check. Injected into the system prompt only when
    it fits the window, because a 20 GB local model has a smaller budget than the
    prompt a lazy designer assumes."""
    excerpts: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    max_chars: int = 4_000

    def render(self) -> str:
        if not self.excerpts and not self.rules:
            return ""
        parts = ["VOICE — imitate the mechanics of these excerpts, never their content:"]
        if self.rules:
            parts.append("Rules: " + "; ".join(self.rules) + ".")
        for i, e in enumerate(self.excerpts, 1):
            parts.append(f"--- sample {i} ---\n{e.strip()}")
        out = "\n".join(parts)
        return out[:self.max_chars]

    @classmethod
    def load(cls, path: str | os.PathLike | None = None, max_chars: int = 4_000) -> "VoiceCard":
        """Config-driven, with a sane default: if the operator hasn't curated samples,
        borrow two approved passages from the repo so the voice is still consistent."""
        from pathlib import Path
        root = Path(".")
        p = Path(path) if path else root / "config" / "voice.json"
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        excerpts = list(data.get("excerpts", []))
        for src in data.get("from_files", []) or default_sample_files(root):
            fpath, chars = (src.split(":") + ["700"])[:2] if isinstance(src, str) else \
                (src["path"], str(src.get("chars", 700)))
            f = Path(fpath)
            if f.exists():
                body = strip_front_matter(f.read_text(encoding="utf-8", errors="ignore"))
                excerpts.append(body[:int(chars)])
        return cls(excerpts=[e for e in excerpts if len(e.strip()) > 120][:4],
                   rules=data.get("rules", DEFAULT_RULES), max_chars=max_chars)


DEFAULT_RULES = ["second person, concrete nouns", "a number in every rule",
                 "one worked example per section", "short sentence to land a point",
                 "never promise an outcome", "redirect clinical questions"]


def default_sample_files(root: Any) -> list[str]:
    """Where to look for house voice when nobody configured it. Repo-relative so it
    works from the checkout root, and tolerant of a partial portfolio."""
    from pathlib import Path
    cands = ["01-book/manuscript.md", "04-seo/articles/03-caffeine-on-night-shift.md",
             "04-seo/articles/01-sleep-after-night-shift.md", "05-marketing/01-CONTENT-CALENDAR.md"]
    return [c for c in cands if (root / c).exists()]


def strip_front_matter(text: str) -> str:
    """Drop YAML fences, pandoc `%title` metadata blocks and leading <!-- --> so a
    style sample starts on real prose."""
    t = text.lstrip()
    if t.startswith("---"):
        end = t.find("\n---", 3)
        if end > 0:
            t = t[end + 4:].lstrip()
    while t.startswith("%") or t.startswith("<!--"):
        if t.startswith("%"):
            nl = t.find("\n")
            t = (t[nl + 1:] if nl > 0 else "").lstrip()
        else:
            end = t.find("-->")
            t = (t[end + 3:] if end > 0 else t[4:]).lstrip()
    return t


class LLMUnavailable(RuntimeError):
    """Raised when prose was requested but no provider answered.

    The alternative — silently emitting boilerplate and calling it a draft — is how a
    content operation ends up shipping thin work that gets it removed.
    """


@dataclass
class Result:
    text: str
    provider: str
    model: str
    in_tokens: int = 0
    out_tokens: int = 0
    cost_note: str = "open weights on local hardware: $0 per token"

    @property
    def words(self) -> int:
        import re
        return len(re.findall(r"[A-Za-z0-9'\u2019\-]+", self.text))


@dataclass
class Provider:
    mode: str                                   # offline | ollama | openai | cmd | anthropic
    model: str = ""
    base_url: str = ""
    cmd: str = ""
    env_used: list[str] = field(default_factory=list)
    backend: Backend | None = None
    voice: VoiceCard | None = None
    probed: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.mode != "offline"

    @property
    def is_local(self) -> bool:
        return self.mode in ("ollama", "openai", "cmd")

    @classmethod
    def detect(cls, force: str | None = None, probe: bool = True,
               voice_path: str | None = None) -> "Provider":
        env = {k: v for k, v in os.environ.items()
               if k.startswith(("WEALFORGE_", "OLLAMA", "ANTHROPIC_", "OPENAI_"))}
        paid_ok = os.environ.get("WEALFORGE_ALLOW_PAID") == "1"

        def build(kind: str, model: str, base_url: str = "", cmd: str = "") -> "Provider":
            be: Backend | None = None
            if kind != "offline":
                # for kind=="cmd" the model field IS the command line (providers._via_cmd)
                be = Backend(kind, cmd or model, base_url)
            p = cls(kind, model or cmd, base_url, cmd, sorted(env), backend=be,
                    voice=VoiceCard.load(voice_path))
            if probe and kind != "offline":
                p.probed = be.probe() if be else {}
                if not p.probed.get("ok"):
                    p.probed["mode_before_unreachable"] = kind
                    return cls("offline", model, base_url, "", sorted(env),
                               probed={"ok": False, "reason": p.probed.get("reason", "")})
            return p

        if force and force not in ("auto", "offline"):
            if force in ("ollama", "openai", "anthropic", "cmd"):
                model = os.environ.get("WEALFORGE_LLM_MODEL", "") or DEFAULT_MODEL.get(force, "local")
                base = os.environ.get("WEALFORGE_LLM_BASE_URL", "") or \
                    (providers.DEFAULT_OLLAMA if force == "ollama" else "")
                return build(force, model, base or ("https://api.anthropic.com" if force == "anthropic" else ""))
            spec = force
            return build(*_split_spec(spec))

        if spec := os.environ.get("WEALFORGE_MODEL", "").strip():
            kind, model, base = _split_spec(spec)
            return build(kind, model, base)

        if cmd := os.environ.get("WEALFORGE_LLM_CMD", ""):
            if shutil.which(cmd.split()[0]):
                return build("cmd", "local", "", cmd)

        url = os.environ.get("WEALFORGE_OLLAMA_URL") or os.environ.get("OLLAMA_HOST") or providers.DEFAULT_OLLAMA
        if not url.startswith("http"):
            url = "http://" + url
        explicit = bool(os.environ.get("WEALFORGE_OLLAMA_URL") or os.environ.get("OLLAMA_HOST"))
        notes: list[str] = []
        if url == providers.DEFAULT_OLLAMA or explicit:
            try:
                tags = providers._request(url.rstrip("/") + "/api/tags", None, {}, method="GET",
                                          timeout=3, retries=0)
                names = [m.get("name", "") for m in tags.get("models", [])]
                if names:
                    want = os.environ.get("WEALFORGE_OLLAMA_MODEL") or _pick_ollama_tag(names)
                    return build("ollama", want, url)
                notes.append(f"Ollama at {url} answered but has no models pulled — `ollama pull qwen3.6:8b`")
            except Exception as e:
                # A dead Ollama is reported but does not end the search: an operator with both a
                # stopped Ollama and a working vLLM server should get the vLLM server.
                notes.append(f"Ollama at {url} unreachable: {str(e)[:110]}" if explicit
                             else "no Ollama server on the default port")

        if base := os.environ.get("WEALFORGE_OPENAI_BASE_URL", ""):
            return build("openai", os.environ.get("WEALFORGE_OPENAI_MODEL", "local"), base)

        if paid_ok and os.environ.get("ANTHROPIC_API_KEY"):
            return build("anthropic", os.environ.get("WEALFORGE_LLM_MODEL") or DEFAULT_MODEL["anthropic"],
                         "https://api.anthropic.com")
        if paid_ok and os.environ.get("OPENAI_API_KEY"):
            return build("openai", os.environ.get("WEALFORGE_LLM_MODEL") or DEFAULT_MODEL["openai"],
                         os.environ.get("WEALFORGE_LLM_BASE_URL") or "https://api.openai.com/v1")
        if not paid_ok and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
            notes.append("hosted key present but WEALFORGE_ALLOW_PAID != 1 — refusing to spend by default")
        return cls("offline", "", "", "", sorted(env), voice=VoiceCard.load(voice_path),
                   probed={"ok": False, "reason": "; ".join(n for n in notes if n) or
                           "no provider configured"})

    # ------------------------------------------------------------- calls ----
    def complete(self, prompt: str, system: str = "", max_tokens: int = MAX_TOKENS) -> Result:
        if self.backend is None:
            why = self.probed.get("reason", "no provider configured")
            raise LLMUnavailable(
                f"no model answered ({why}). Three free options: (a) `ollama pull qwen3.6:8b` and "
                "set WEALFORGE_MODEL=qwen3.6:8b — see `wf model --select`; (b) set WEALFORGE_LLM_CMD to "
                "any command that reads a prompt on stdin and writes text to stdout; (c) run "
                "`wf build --emit-prompt FILE`, paste it into any chat UI, then `wf build --from-file`. "
                "Scanning, gating, math, datasets and packaging all run without a model.")
        sys_full = SYSTEM_RULES
        if self.voice:
            card = self.voice.render()
            if card:
                sys_full = f"{sys_full}\n{card}\n"
        if system:
            sys_full = f"{sys_full}\n{system.strip()}\n"
        try:
            reply = self.backend.generate(prompt, sys_full)
        except ProviderError as e:
            raise LLMUnavailable(str(e)) from e
        return Result(reply.text, self.mode, reply.model, reply.usage.prompt_tokens,
                      reply.usage.completion_tokens,
                      "open weights on local hardware: $0 per token" if self.is_local
                      else "hosted provider — check your own pricing")

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 1200) -> dict[str, Any]:
        """Structured slots (metadata, listings) with the tolerant parser, because
        small models put JSON in fences or wrap it in prose roughly a fifth of the time.
        A parse failure raises; the caller records `needs_human` rather than guessing."""
        if self.backend is None:
            self.complete(prompt, system, max_tokens)   # raises LLMUnavailable with the full message
            raise LLMUnavailable("no provider configured")
        be = self.backend
        saved, be.json_mode = be.json_mode, True
        try:
            try:
                reply = be.generate(prompt + "\n\nAnswer with a single JSON object only.",
                                    f"{SYSTEM_RULES}\n{system.strip()}\n" if system else SYSTEM_RULES)
            except ProviderError:
                be.json_mode = saved                                     # servers without json mode
                reply = be.generate(prompt + "\n\nReturn ONLY a JSON object.", SYSTEM_RULES + (system or ""))
            return providers.extract_json(reply.text)
        except ProviderError as e:
            raise LLMUnavailable(str(e)) from e
        finally:
            be.json_mode = saved


def _split_spec(spec: str) -> tuple[str, str, str]:
    """`qwen3.6:8b` → ollama; `openai:http://h:8080/v1` → openai-compat; `cmd:prog` → cmd;
    a registry id (`gemma4-26b-a4b`) → its ollama tag."""
    if spec.startswith("openai:"):
        url = spec.split(":", 1)[1]
        return "openai", os.environ.get("WEALFORGE_OPENAI_MODEL", url.rstrip("/").rsplit("/", 1)[-1]), url
    if spec.startswith("ollama:"):
        return "ollama", spec.split(":", 1)[1], os.environ.get("WEALFORGE_OLLAMA_URL", providers.DEFAULT_OLLAMA)
    if spec.startswith("cmd:"):
        return "cmd", spec.split(":", 1)[1], ""
    if os.sep in spec or spec.endswith(".py"):
        return "cmd", spec, ""
    if ":" in spec:
        return "ollama", spec, os.environ.get("WEALFORGE_OLLAMA_URL", providers.DEFAULT_OLLAMA)
    from . import modelhub
    hit = modelhub.by_id(modelhub.load(), spec)
    if hit:
        return "ollama", hit.ollama_tag or hit.id, os.environ.get("WEALFORGE_OLLAMA_URL", providers.DEFAULT_OLLAMA)
    return "ollama", spec, os.environ.get("WEALFORGE_OLLAMA_URL", providers.DEFAULT_OLLAMA)


def _pick_ollama_tag(names: list[str]) -> str:
    """Auto-detection runs on a machine whose RAM the registry knows nothing about, so the
    ordering is: fits this host first, then instruct-family preference, then largest.

    Without the capacity check, auto-detect happily selects a 26B model on a 4 GB box and
    the first real generation call OOMs — which reads to the operator as "the integration is
    broken" when it is a sizing question we can answer up front."""
    import re

    def fits(n: str) -> int:
        try:
            from . import modelhub
            vram = modelhub.detect_vram_gb()
            for spec in modelhub.load():
                if spec.ollama_tag == n or spec.ollama_tag and n.startswith(spec.ollama_tag.split(":")[0]):
                    return 0 if spec.vram_gb_4bit <= vram else 1
        except Exception:
            pass
        return 0                      # unknown model: don't penalise it

    def key(n: str) -> tuple[int, int, int]:
        m = re.search(r"(\d+(?:\.\d+)?)b", n, re.I)
        size = int(float(m.group(1))) if m else 0
        return (fits(n), 0 if any(k in n.lower() for k in ("instruct", "qwen", "gemma")) else 1, -size)
    return sorted(names, key=key)[0] if names else "qwen3.6:8b"


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
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(brief_to_prompt(brief, section, word_target), encoding="utf-8")
    return str(p)
