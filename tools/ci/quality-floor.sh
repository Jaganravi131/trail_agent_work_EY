#!/usr/bin/env bash
# The quality floor, as a script so that `bash tools/ci/quality-floor.sh` answers the same
# question here that CI asks there. Failing numbers go into an annotation because the raw
# Actions log is not readable from this sandbox.
#
# Why a floor at all: "the assets look good" is not a checkable claim. These are.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

OUT=${OUT:-00-strategy/decisions/05-EVAL-RESULTS.md}
python3 -m wealforge eval --self-check --out "$OUT" >/tmp/floor.log 2>&1 || {
  echo "::error::wf eval --self-check exited non-zero || $(tail -20 /tmp/floor.log | tr '\n' ' ' | tr -d '\r')"
  exit 1
}
python3 - "$OUT" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
d = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
rows = d["repo_grades"]

def die(msg: str) -> None:
    """One line, GitHub-visible, with the numbers that produced the verdict."""
    print("::error::" + msg.replace("%", "%25").replace("\n", "%0A"))
    raise SystemExit(1)

if not rows:
    die("graded nothing — did the portfolio move?")

# Front/back matter (copyright, about the author, "also in this series") carries no numbers by
# design, so it is exempt from the specificity floor but NOT from the grade floor. Exemptions
# are listed rather than quietly filtered out.
MATTER = ("-front-matter", "back-matter", "NOTES")
body = [r for r in rows if not any(k in r["file"] for k in MATTER)]
exempt = [r["file"] for r in rows if any(k in r["file"] for k in MATTER)]
mean = sum(r["score"] for r in rows) / len(rows)
errors = sum(r["gate_errors"] for r in rows)
cliches = sum(len(r["cliches"]) for r in rows)
nums = sum(r["numbers_per_100w"] for r in body) / max(len(body), 1)
thin = [r["file"] for r in body if r["numbers_per_100w"] < 0.4]

print(f"mean grade {mean:.1f} over {len(rows)} files ({len(body)} body, "
      f"{len(exempt)} front/back matter exempt from the numbers floor)")
print(f"body numbers/100 words: {nums:.2f} · {errors} gate errors · {cliches} clichés")
for f in thin:
    # Per-file thinness is a drafting note, not a compliance failure: warn, don't block.
    print(f"::warning file={f}::under 0.4 numbers/100 words — more specifics next pass")

bad = []
if mean < 75:
    bad.append(f"mean grade {mean:.1f} below the house floor of 75")
if errors:
    bad.append(f"{errors} gate error(s) in committed assets")
if cliches > 1:
    bad.append(f"{cliches} cliché hits — the voice card or the copy needs attention")
if nums < 0.6:
    bad.append(f"body specificity collapsed to {nums:.2f} numbers/100 words (floor 0.6)")
if bad:
    die("; ".join(bad))
print("quality floor holds")
PY
