#!/usr/bin/env bash
# Run the free tool's logic headlessly: `bash tools/ci/planner-check.sh`.
#
# The page under test is a single dependency-free HTML file, so the only way to check its
# arithmetic is to evaluate it. Node is present on the GitHub Pages CI runner; where it is not,
# this says so in one line and exits 0, because a missing optional toolchain is not a defect in
# the work. `tests/test_planner.py` records the skip in the test output so it cannot be missed.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

FILE=${1:-03-tool/index.html}

if ! command -v node >/dev/null 2>&1; then
  echo "SKIP planner-check — node is not on PATH; the tool's JS was not executed on this machine"
  exit 0
fi

if [ ! -f "$FILE" ]; then
  echo "::error::$FILE is missing — the tool cannot be checked, and the site build would fail too"
  exit 1
fi

node "$(dirname "$0")/planner-check.mjs" "$FILE"
