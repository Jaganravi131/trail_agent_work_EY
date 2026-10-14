"""Tests for the free tool: does the planner in `03-tool/index.html` actually work?

The rest of the suite checks the tool's *copy* (the gate runs over it, the site builder checks its
metadata and links). This file executes it. The page is one dependency-free HTML file, so the only
honest check is to evaluate its script against a stub DOM in node and assert on the plan, the
rendered output, the `.ics` bytes and the error paths.

node is an optional toolchain here. If it is missing the test skips — and says so in the skip
reason, because a silent green is how a claim like "tested headlessly across nine rota patterns"
gets made without being tested at all.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "03-tool" / "index.html"
HARNESS = REPO / "tools" / "ci" / "planner-check.mjs"

NODE = shutil.which("node")
REASON = ("node is not installed, so the planner's JavaScript was not executed. "
          "Install node, or run `bash tools/ci/planner-check.sh` on a machine that has it.")

# The harness walks nine rota patterns plus the failure surfaces; anything close to this count
# means it silently stopped exercising the page (a renamed id, a script block that no longer
# matches) rather than genuinely passing.
ASSERTION_FLOOR = 300


def run_harness(page: Path) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, str(HARNESS), str(page)], capture_output=True, text=True,
                          cwd=str(REPO), timeout=180)


@unittest.skipUnless(NODE, REASON)
class TestPlannerHarness(unittest.TestCase):
    def test_the_page_and_the_harness_are_both_present(self) -> None:
        self.assertTrue(PAGE.is_file(), f"{PAGE} is missing")
        self.assertTrue(HARNESS.is_file(), f"{HARNESS} is missing")
        self.assertIn("<script>", PAGE.read_text(encoding="utf-8"),
                      "the page no longer contains an inline script — the harness cannot run it")

    def test_the_tool_passes_every_pattern(self) -> None:
        proc = run_harness(PAGE)
        self.assertEqual(proc.returncode, 0,
                         f"planner-check failed:\n{proc.stdout}\n{proc.stderr}")
        m = re.search(r"(\d+) assertions", proc.stdout)
        self.assertIsNotNone(m, f"the harness never reported an assertion count: {proc.stdout[:400]}")
        self.assertGreaterEqual(int(m.group(1)), ASSERTION_FLOOR,
                                f"the harness ran only {m.group(1)} assertions — it should exercise "
                                "every rota pattern; check that go() and buildPlan() still exist")
        self.assertIn("ALL GOOD", proc.stdout)

    def _mutate(self, needle: str, replacement: str) -> str:
        src = PAGE.read_text(encoding="utf-8")
        self.assertIn(needle, src, f"the line {needle!r} moved — update this test's anchor")
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "index.html"
            broken.write_text(src.replace(needle, replacement), encoding="utf-8")
            return run_harness(broken)

    def test_the_harness_catches_a_broken_curfew(self) -> None:
        """A checker with no failing case is a decoration.

        The curfew is the tool's core promise: nothing caffeinated later than two hours before
        the planned bedtime. Widen the curfew in a copy of the page and the harness must fail.
        """
        proc = self._mutate("rel.tCaff  = bed - 120;", "rel.tCaff  = bed + 300;")
        self.assertNotEqual(proc.returncode, 0, "a page that lets caffeine run past the curfew must fail the check")
        self.assertIn("before bed", proc.stdout + proc.stderr)

    def test_the_harness_catches_a_missing_download(self) -> None:
        """And the same for the part the user carries out of the browser: the .ics file."""
        self.assertNotEqual(self._mutate("'END:VCALENDAR']", "'WRONG:END']").returncode, 0,
                            "a malformed calendar must not pass")

    def test_the_harness_catches_unfolded_ics_lines(self) -> None:
        self.assertNotEqual(
            self._mutate("if (bytes(next) > (out.length ? 74 : 75))", "if (false)").returncode, 0,
            "an .ics line over 75 octets must be reported")

    def test_the_page_is_self_contained(self) -> None:
        """"No account, nothing uploaded, works offline" is a claim about requests, so check for requests.

        The harness cannot see this: it executes the page, it does not know what the page would
        have fetched. Comments are stripped because they mention domains on purpose (the note
        explaining why there is no hard-coded canonical URL), and `data:` URIs are collapsed
        because an inline SVG icon is bytes in the file, not a network call.
        """
        src = PAGE.read_text(encoding="utf-8")
        body = re.sub(r"data:[^\"]*", "data:", re.sub(r"<!--.*?-->", "", src, flags=re.S))
        rules = (
            (r"<script[^>]*\ssrc=", "an external <script src>"),
            (r"<link[^>]*rel=[\"']stylesheet", "a stylesheet link"),
            (r"<img\b", "an <img> request"),
            (r"https?://", "an absolute URL"),
            (r"\bfetch\s*\(", "a fetch() call"),
            (r"XMLHttpRequest|sendBeacon|new\s+Image\s*\(", "a hand-rolled request"),
            (r"<iframe\b", "an iframe"),
        )
        for pattern, label in rules:
            hit = re.search(pattern, body)
            if hit:
                around = body[max(0, hit.start() - 70):hit.start() + 70].replace("\n", " ")
                self.fail(f"the page contains {label}, which the privacy promise forbids: …{around}…")


if __name__ == "__main__":
    unittest.main()
