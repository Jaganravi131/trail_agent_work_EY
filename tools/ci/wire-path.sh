#!/usr/bin/env bash
# The model wire path, exercised without a GPU and without secrets.
#
# Kept as a script rather than inline YAML so that `bash tools/ci/wire-path.sh` reproduces
# exactly what CI does, on this machine, in the time it takes to type the command. Every
# other way of debugging a red Actions step meant reading a log this sandbox cannot fetch.
#
# What it proves: detection, the HTTP call, the gate → repair loop, and — importantly — that
# a stage advance is *refused* while a draft still fails. The stub answers with a gate
# violation on the first attempt of each section, so a pipeline that skipped the repair loop,
# or that faked a pass, cannot get through the assertions at the end.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

PORT=${PORT:-11434}
OUT=${OUT:-out}
export WEALFORGE_OLLAMA_URL="http://127.0.0.1:$PORT"

python3 tools/model_stub.py --port "$PORT" --host 127.0.0.1 --bad-first >/tmp/stub.log 2>&1 &
STUB=$!
cleanup() { kill "$STUB" 2>/dev/null || true; wait "$STUB" 2>/dev/null || true; }
trap cleanup EXIT

for _ in $(seq 1 30); do
  if curl -sf "$WEALFORGE_OLLAMA_URL/api/tags" >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! curl -sf "$WEALFORGE_OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  echo "::error::stub model server never came up; its log: $(tail -5 /tmp/stub.log | tr '\n' ' ')"
  exit 1
fi
echo "stub up on $WEALFORGE_OLLAMA_URL"

# On failure, put the evidence where it can be read: an annotation is a structured API
# field, and this sandbox cannot fetch a raw Actions log.
fail() {
  echo "✗ $1"
  tail -30 /tmp/wire-step.log
  msg=$(python3 - "$1" <<'PY'
import pathlib, sys
reason = sys.argv[1]
try:
    detail = pathlib.Path("/tmp/wire-step.log").read_text()[-3000:]
except Exception as e:                                    # noqa: BLE001
    detail = f"(could not read the step log: {e})"
print((f"{reason} failed || {detail}").replace("%", "%25").replace("\r", "%0D")
      .replace("\n", "%0A"))
PY
)
  echo "::error::$msg"
  exit 1
}

{ echo "### wf model"; python3 -m wealforge model; } > /tmp/wire-step.log 2>&1 || fail "wf model"
grep -q "provider mode : ollama" /tmp/wire-step.log || fail "wf model did not detect the stub as an ollama backend"
tail -3 /tmp/wire-step.log

{ echo "### wf score"; python3 -m wealforge score; } >> /tmp/wire-step.log 2>&1 || fail "wf score"
{ echo "### wf pick --build"; python3 -m wealforge pick --top 1 --build; } >> /tmp/wire-step.log 2>&1 \
  || fail "wf pick --top 1 --build"
tail -2 /tmp/wire-step.log

# --bad-first plants one gate violation per section on the first attempt, so this only
# passes if the draft → gate → repair loop actually converges.
{ echo "### wf work"; python3 -m wealforge work --verbose --max-ideas 1 --sections 2 \
    --out-dir "$OUT" --allow-fail; } >> /tmp/wire-step.log 2>&1 || fail "wf work"
tail -4 /tmp/wire-step.log

python3 - "$OUT" >> /tmp/wire-step.log 2>&1 <<'PY' || fail "work-log assertions"
import pathlib, re, sys
out = pathlib.Path(sys.argv[1])
logs = sorted(out.glob("*/WORK-LOG.md"))
assert logs, f"no work log written under {out}/"
body = logs[-1].read_text(encoding="utf-8")
assert "Attempt history" in body, "the loop must document what it tried"
assert re.search(r"try1\(\d+w, [1-9]\d*e", body), \
    "the stub should have caused a first-attempt failure; log head:\n" + body[:600]
assert "still failing after" not in body, "the repair loop did not converge — that is a real regression"
print(f"wire path ok — {logs[-1]}: first attempt failed, repaired within the budget")
PY
echo "ALL GOOD"
