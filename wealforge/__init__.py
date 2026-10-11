"""WealthForge agent — zero-capital asset loop.

Stdlib only. No paid APIs, no installed packages, no network calls unless the
operator explicitly configures an LLM provider (see ``wealforge.llm``).

The loop this package implements:

    scan → score → select → brief → draft → gate → package → publish(checklist)
                                                        ↓
                                          measure → diagnose → one experiment

Design rules baked into the code, not just the docs:

* A stage cannot advance past ``drafted`` without a passing compliance gate.
* ``offline`` is a first-class mode, not an error: with no provider configured
  the agent writes briefs, checklists, math and gates, and refuses to pretend
  it wrote prose.
* Money never leaves the operator: nothing in here buys, spends or subscribes.
"""

from __future__ import annotations

__version__ = "0.2.0"
__all__ = ["__version__"]
