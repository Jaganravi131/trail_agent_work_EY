"""WealthForge agent — zero-capital asset loop.

Stdlib only. No installed packages, no server. HTTP is used solely to talk to an
open-weight model the operator is already running (Ollama or any OpenAI-compatible
server); hosted providers need an explicit ``WEALFORGE_ALLOW_PAID=1`` opt-in.

The loop this package implements:

    scan → score → select → brief → draft → gate → repair → package → publish(checklist)
                        ↑                                         ↓
                        └── dataset → finetune ← measure → diagnose → one experiment

Design rules baked into the code, not just the docs:

* A stage cannot advance past ``drafted`` without a passing compliance gate.
* A model's output is a candidate, never a result: it becomes an asset only by
  passing the gate, within a bounded repair loop (``wealforge.pipeline``).
* ``offline`` is a first-class mode, not an error: with no provider configured
  the agent writes briefs, checklists, math and gates, and refuses to pretend
  it wrote prose.
* A fine-tune needs evidence before it is allowed to happen (``wealforge.dataset``
  has a corpus floor) and evidence after (``wealforge.evalkit`` compares base vs
  tuned, and a regression means keep the base).
* Money never leaves the operator: nothing in here buys, spends or subscribes.
"""

from __future__ import annotations

__version__ = "0.3.0"
__all__ = ["__version__"]
