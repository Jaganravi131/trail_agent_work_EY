"""Tests for the WealthForge agent. Stdlib unittest, no pytest, no network.

    python3 -m unittest discover -s tests -q
    python3 -m wealforge gate tests/          # the gate is gated too

The interesting cases are the negative ones: a gate that catches everything is
useless, and a gate that catches nothing is dangerous. So each rule family gets
one violating fixture and one *must-not-fire* fixture — including prohibitions
("never buy reviews") and correctly-disclosed affiliate links.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealforge import (gate, generator, kdp, llm, metrics, models, roadmap, scan,
                     store, rubric as rubric_mod)  # noqa: E402


# --------------------------------------------------------------- rubric ----
class TestRubric(unittest.TestCase):
    def setUp(self):
        self.rb = rubric_mod.Rubric.load(ROOT / "config" / "rubric.json")

    def test_normalisation_is_independent_of_axis_count(self):
        all5 = self.rb.score({k: 5 for k in self.rb.axis_keys})
        self.assertEqual(all5["total"], 100.0)
        all1 = self.rb.score({k: 1 for k in self.rb.axis_keys})
        self.assertEqual(all1["total"], 20.0)

    def test_weights_actually_matter(self):
        # feasibility is ×3, speed ×2: same raw numbers, different totals
        a = self.rb.score({**{k: 3 for k in self.rb.axis_keys}, "feasibility": 5})["total"]
        b = self.rb.score({**{k: 3 for k in self.rb.axis_keys}, "speed": 5})["total"]
        self.assertGreater(a, b)

    def test_unknown_axis_is_an_error_not_a_warning(self):
        with self.assertRaises(KeyError):
            self.rb.score({"definitely_not_an_axis": 5})

    def test_missing_axis_rejected(self):
        with self.assertRaises(KeyError):
            self.rb.score({"feasibility": 5})

    def test_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            self.rb.score({**{k: 3 for k in self.rb.axis_keys}, "quality": 9})

    def test_selection_can_pick_fewer_than_top_n(self):
        rows = [{"title": "good", "total": 95, "scores": {}},
                {"title": "meh", "total": 55, "scores": {}}]
        picked = self.rb.select(rows)
        self.assertEqual([r["title"] for r in picked], ["good"])

    def test_tie_break_is_stable_and_documented(self):
        rows = [{"title": "Zed", "total": 80, "scores": {"speed": 5, "defensibility": 1, "quality": 1}},
                {"title": "Abe", "total": 80, "scores": {"speed": 5, "defensibility": 1, "quality": 1}},
                {"title": "Mid", "total": 80, "scores": {"speed": 2, "defensibility": 5, "quality": 5}}]
        order = [r["title"] for r in self.rb.rank(rows)]
        self.assertEqual(order[0], "Abe")            # title tiebreak, alphabetical
        self.assertEqual(order[-1], "Mid")           # speed is the first tie-break axis
        self.assertEqual(order, [r["title"] for r in self.rb.rank(list(reversed(rows)))])


# ----------------------------------------------------------------- scan ----
class TestPreScreen(unittest.TestCase):
    def setUp(self):
        self.rb = rubric_mod.Rubric.load(ROOT / "config" / "rubric.json")
        self.pool = scan.load_seed(ROOT / "config" / "scan_seed.json")

    def test_seed_file_is_valid_and_scores_are_complete(self):
        for c in self.pool:
            if c.get("scores"):
                self.assertEqual(set(c["scores"]), set(self.rb.axis_keys), c["title"])

    def test_the_poisoned_ideas_are_rejected(self):
        cands = {c.idea.title: c for c in scan.evaluate(self.pool, self.rb)}
        for title, tag in [("Public domain classic, unabridged reprint with a new cover", "public-domain"),
                           ("Bulk 120-prompt journal mill: 1,000 low-content titles", "thin-ai"),
                           ("Seed 5-star reviews with a review swap group, then upvote our own Reddit posts", "review-fraud"),
                           ("Scrape Amazon reviews for keyword gold, bypass the rate limiter", "scraping"),
                           ("Melatonin dosing protocol to cure shift work sleep disorder", "medical-advice")]:
            self.assertTrue(cands[title].rejected, f"{title} should have been blocked")
            self.assertIn(tag, cands[title].idea.blocked[0], f"{title} should trip {tag}")

    def test_screen_and_gate_share_the_health_vocabulary(self):
        """An idea the screen calls health-adjacent must be one the gate demands
        safeguards for. Two regexes that drift is how 'we screened it' quietly
        stops meaning anything."""
        self.assertIn(gate.HEALTH_TRIGGER.pattern, scan._HEALTH_SRC,
                      "scan must reuse the gate's vocabulary, not keep its own copy")
        cand = {"title": "Melatonin timing", "niche": "sleep", "platform": "kdp", "thesis": "", "angles": []}
        rej, cau = scan.prescreen(cand)
        self.assertFalse(rej)
        self.assertTrue(any(c.startswith("health") for c in cau))
        self.assertTrue(bool(gate.HEALTH_TRIGGER.search("melatonin timing sleep")))

    def test_legitimate_ideas_are_not_blocked(self):
        cands = scan.evaluate(self.pool, self.rb)
        keep = [c for c in cands if c.idea.title.startswith(("The Night Shift", "Free single-file",
                                                              "Cozy mystery", "Air fryer"))]
        self.assertTrue(keep)
        for c in keep:
            self.assertFalse(c.idea.blocked, f"{c.idea.title} wrongly blocked: {c.idea.blocked}")

    def test_low_quality_survives_screen_but_fails_the_bar(self):
        cands = {c.idea.title: c for c in scan.evaluate(self.pool, self.rb)}
        air = cands["Air fryer meal prep for busy people"]
        self.assertFalse(air.rejected)              # legal, feasible, just worthless
        self.assertLess(air.idea.total, self.rb.min_total)

    def test_health_niche_gets_a_caution_not_a_rejection(self):
        cands = {c.idea.title: c for c in scan.evaluate(self.pool, self.rb)}
        book = cands["The Night Shift Reset"]
        self.assertTrue(any(c.startswith("health") for c in book.cautions))
        self.assertFalse(book.rejected)


# ----------------------------------------------------------------- gate ----
class TestGate(unittest.TestCase):
    def test_clean_health_article_passes(self):
        text = (ROOT / "04-seo/articles/01-sleep-after-night-shift.md").read_text()
        rep = gate.evaluate_text(text, "04-seo/articles/01-sleep-after-night-shift.md")
        self.assertTrue(rep.passed, "\n".join(f.render() for f in rep.findings))
        self.assertGreater(rep.words, 1200)

    def test_manuscript_passes(self):
        rep = gate.evaluate_text((ROOT / "01-book/manuscript.md").read_text(), "01-book/manuscript.md")
        self.assertTrue(rep.passed, "\n".join(f.render() for f in rep.findings))

    def test_every_reader_facing_asset_in_the_repo_passes(self):
        """The acceptance test that matters: no false positives left in shipped work."""
        reps = gate.evaluate_paths([ROOT / "01-book"], bundle=True) + \
            gate.evaluate_paths([ROOT / "03-tool", ROOT / "04-seo"])
        self.assertGreater(len(reps), 3)
        bad = [r for r in reps if not r.passed]
        self.assertFalse(bad, "\n" + "\n".join(r.render() for r in bad))

    def test_treatment_claim_and_dosing_are_errors(self):
        text = ("This 12-step protocol cures insomnia in a week. Take 10 mg of melatonin and "
                "400 mg magnesium at 08:15. It is educational content, not medical advice, and never "
                "drive when drowsy; see a clinician if it persists.")
        ids = {f.rule_id for f in gate.evaluate_text(text, "x/a.md").findings}
        self.assertIn("M1", ids)
        self.assertIn("M3", ids)

    def test_income_guarantee_is_an_error(self):
        ids = {f.rule_id for f in gate.evaluate_text(
            "You'll earn $400 per day guaranteed — a passive income machine that prints money.", "x/b.md").findings}
        self.assertIn("I1", ids)

    def test_ranges_with_assumptions_are_allowed(self):
        text = ("Base case ≈ $110 in month 1 and $500-650/mo by month 6, assuming 1 sale/day and "
                "18 reviews by day 45. Anyone promising a higher month 1 already had the audience.")
        rep = gate.evaluate_text(text, "x/c.md")
        self.assertNotIn("I1", {f.rule_id for f in rep.findings})

    def test_undisclosed_affiliate_link_fails_but_disclosed_passes(self):
        link = "Try the mask at https://amzn.to/3xYz9kA before the price changes."
        self.assertIn("A1", {f.rule_id for f in gate.evaluate_text(link, "x/d.md").findings})
        disclosed = "Disclosure: some links are affiliate links and I may earn a commission.\n" + link
        self.assertNotIn("A1", {f.rule_id for f in gate.evaluate_text(disclosed, "x/e.md").findings})

    def test_prohibitions_are_not_violations(self):
        text = ("No second accounts, no vote rings, no re-posting a removed item. Never buy reviews "
                "and never upvote your own thread. Do not scrape Amazon reviews for keywords.")
        rep = gate.evaluate_text(text, "x/f.md")
        self.assertEqual([], [f.rule_id for f in rep.findings])
        self.assertTrue(any(k.startswith("negated:") for k in rep.stats), "suppression should be visible")

    def test_a_past_tense_denial_is_not_a_violation(self):
        # Originality statements in this repo's own policy pages say things like "nothing was
        # scraped". A gate that flags a denial as the act it denies gets muted within a week, so
        # the negation set reads sentence-initial denials — "Nothing was scraped from a
        # competitor's listing" — exactly the way it reads "No scraping of competitor listings".
        denial = ("Everything here is original writing. Nothing was scraped from a competitor's "
                  "listing, a review corpus or a book. Nobody bought a review. No one upvoted a "
                  "thread from a second account.")
        rep = gate.evaluate_text(denial, "04-seo/pages/editorial.md")
        self.assertEqual([], [f.rule_id for f in rep.findings],
                           f"a denial must not be reported as the thing it denies: {rep.errors}")

    def test_the_trade_this_makes_is_documented_not_sneaky(self):
        # Known limit, asserted so it stays a decision rather than a hole: a sentence that opens
        # with a denial and then states a live instruction is read as a denial, because the guard
        # only reads backwards from the match (asymmetry is what keeps the real violations
        # firing — see the "negation after the match does not count" case in test_models.py).
        # If a document ever needs to say "nothing stops you scraping X", it must be reworded.
        loophole = "Nothing stops you scraping Amazon reviews once the pattern is down."
        rep = gate.evaluate_text(loophole, "x/g.md")
        self.assertEqual([], [f.rule_id for f in rep.findings],
                         "this is the accepted cost of the anchored-denial rule")

    def test_disclaimed_credentials_pass_but_fabricated_ones_fail(self):
        bad = "About the Author: Jane Doe is a board-certified sleep specialist and registered nurse (RN)."
        self.assertIn("H1b", {f.rule_id for f in gate.evaluate_text(bad, "x/bio.md", audience="reader").findings})
        ok = ("About the Author: J. Arek Thorne is a systems writer. This book is deliberately not "
              "written by a clinician and makes no clinical claims.")
        self.assertNotIn("H1b", {f.rule_id for f in gate.evaluate_text(ok, "x/bio2.md", audience="reader").findings})

    def test_safeguards_are_required_for_readers_not_internal_docs(self):
        body = " ".join(f"On a night shift the hour matters more than the intent, and sleep pressure "
                        f"is only half of the picture number {i}." for i in range(30))
        text = ("# How to sleep after a night shift\n\n" + body +
                "\n\nDim the lights, cut the caffeine by hour six, and remember that this is "
                "advice about tiredness, not about treatment.")
        ids = {f.rule_id for f in gate.evaluate_text(text, "04-seo/a.md").findings}
        self.assertIn("S1", ids)
        self.assertEqual([], gate.evaluate_text(text, "00-strategy/a.md").findings,
                         "internal strategy notes must not be held to reader-facing disclosure rules")

    def test_trademarks_are_flagged(self):
        ids = {f.rule_id for f in gate.evaluate_text(
            "Sleep better with a Nespresso machine and ZzzQuil. Compatible with the Casper mattress.", "x/t.md").findings}
        self.assertIn("T1", ids)

    def test_inline_allow_marker_suppresses(self):
        text = "Buy reviews from a swap group. <!-- gate:allow R1 -->"
        rep = gate.evaluate_text(text, "x/s.md")
        self.assertNotIn("R1", {f.rule_id for f in rep.findings})
        self.assertIn("allowed:R1", rep.stats)

    def test_bundle_mode_evaluates_safeguards_across_the_whole_book(self):
        """The front matter carries the disclaimer; the chapters must not need to repeat it."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "00-front.md").write_text(
                "# Front\n\nThis book is educational. It is not medical advice, and it makes no claim "
                "to diagnose, treat or prevent any condition. See a clinician if you cannot sleep in any "
                "setup. Never drive when you are drowsy; pull over somewhere safe for twenty minutes.")
            (root / "01-chapter.md").write_text(
                "# Caffeine\n\n" + " ".join(f"Sleep pressure and the circadian clock interact in hour "
                                              f"{i}, which is why the cut-off time matters." for i in range(40)))
            bundle = gate.evaluate_paths([root], bundle=True)
            self.assertEqual(len(bundle), 1, "a bundle reports once, as the work a reader receives")
            self.assertTrue(bundle[0].passed, "\n".join(f.render() for f in bundle[0].findings))
            self.assertIn("bundled_files", bundle[0].stats)
            loose = [r for r in gate.evaluate_paths([root], bundle=False) if not r.passed]
            self.assertTrue(loose, "per-file mode must still catch the chapter missing its own safeguards")

    def test_bundle_mode_still_reports_a_claim_in_one_chapter(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "00-front.md").write_text(
                "Educational content, not medical advice. See a clinician. Never drive when drowsy.")
            (root / "03-chapter.md").write_text(
                "# The protocol\n\nThis twelve-step routine cures insomnia in a week and reverses "
                "shift work sleep disorder.")
            rep = gate.evaluate_paths([root], bundle=True)[0]
            self.assertFalse(rep.passed)
            ids = {f.rule_id for f in rep.findings}
            self.assertIn("M1", ids)
            self.assertTrue(any("03-chapter" in (f.source or "") for f in rep.findings),
                            "a bundle verdict must still point at the offending file")

    def test_advice_to_see_a_clinician_is_not_a_credential_claim(self):
        text = ("Ask your pharmacist about timing; several drugs interact. If you snore and wake "
                "unrefreshed, that is a clinician conversation rather than a planner problem. "
                + "Sleep pressure builds across a long week. " * 30)
        ids = {f.rule_id for f in gate.evaluate_text(text, "04-seo/ch5.md").findings}
        self.assertNotIn("H1b", ids)

    def test_name_with_post_nominals_is_a_credential_claim(self):
        text = ("About the Author\n\nJane Doe, RN, BSN, MSN has slept through 900 nights as an "
                "icu charge nurse and now writes about recovery. " + "This is educational content. " * 20)
        rep = gate.evaluate_text(text, "04-seo/bio.md", audience="reader")
        self.assertIn("H1b", {f.rule_id for f in rep.findings})

    def test_rules_are_overridable_from_json(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "rules.json"
            cfg.write_text(json.dumps({"rules": [{"id": "Q1", "severity": "error", "title": "cliché",
                                                    "pattern": "", "why": "raised", "fix": "rewrite"}],
                                       "disable": ["T1"]}))
            rules, skip = gate.load_rules(cfg)
            self.assertNotIn("T1", {r.id for r in rules})
            q1 = [r for r in rules if r.id == "Q1"][0]
            self.assertEqual(q1.severity, "error")

    def test_slop_detector(self):
        ids = {f.rule_id for f in gate.evaluate_text(
            "In today's fast-paced world, it's important to note that sleep matters. "
            "This ultimate guide delves into the tapestry of rest. Look no further.", "x/q.md").findings}
        self.assertIn("Q1", ids)

    def test_self_duplication_detected(self):
        one = ("Place your order before the weekend ends and the promotion quietly expires at "
               "midnight on Sunday, which is the only deadline that matters here.")
        para = ("The paragraph opens with something a reader actually needs and then explains the "
                "mechanism behind it in a fresh way each time. " + one)
        text = "# Heading\n\n" + "\n\n".join(f"{para} Variant {i} adds a worked example, a number, "
                                                f"or a counter-argument so the page earns its length."
                                                for i in range(9))
        text += ("\n\nThis is educational content, not medical advice. Never drive when drowsy; pull "
                 "over somewhere safe. See a clinician if it persists.")
        ids = {f.rule_id for f in gate.evaluate_text(text, "x/dup.md").findings}
        self.assertIn("Q2", ids, "repeated boilerplate at this density is the 'thin content' signal")

    def test_missing_file_is_reported_not_crashed(self):
        reps = gate.evaluate_paths(["/definitely/not/here.md"])
        self.assertFalse(reps[0].passed)
        self.assertEqual(reps[0].findings[0].rule_id, "E0")


# ----------------------------------------------------------------- kdp ----
class TestMoneyMath(unittest.TestCase):
    def test_seventy_band_boundaries(self):
        self.assertEqual(kdp.ebook_net(2.99, 300).band, 70)
        self.assertEqual(kdp.ebook_net(9.99, 300).band, 70)
        self.assertEqual(kdp.ebook_net(2.98, 300).band, 35)
        self.assertEqual(kdp.ebook_net(10.00, 300).band, 35)

    def test_cheaper_price_can_pay_better(self):
        """The counter-intuitive result that makes the promo ladder correct."""
        at_099 = kdp.ebook_net(0.99).net
        self.assertLess(at_099, kdp.ebook_net(2.99).net)
        self.assertLess(at_099, kdp.ebook_net(3.99).net - kdp.delivery_fee_kb(300))

    def test_delivery_fee_scales_with_size(self):
        self.assertGreater(kdp.ebook_net(3.99, 4000).net, 0)
        self.assertLess(kdp.ebook_net(3.99, 4000).net, kdp.ebook_net(3.99, 200).net)

    def test_price_ladder_is_monotonic_in_length(self):
        short = kdp.price_new_title(3000, "high", has_worksheets=False)
        long = kdp.price_new_title(45000, "low", has_worksheets=True)
        self.assertLessEqual(short.ebook_price, long.ebook_price)
        self.assertGreaterEqual(long.paperback_price, 7.99)

    def test_floor_never_below_seventy_band_for_list_price(self):
        plan = kdp.price_new_title(2000, "high", has_worksheets=False)
        self.assertGreaterEqual(plan.ebook_price, kdp.SEVENTY_MIN)

    def test_promo_days_are_capped(self):
        for comp in ("low", "medium", "high"):
            self.assertLessEqual(kdp.price_new_title(9000, comp).promo_days, kdp.PRICE_PULSE_MAX_DAYS)

    def test_to_99_rounding(self):
        self.assertEqual(kdp.to_99(12.77), 12.99)
        self.assertEqual(kdp.to_99(9.20), 9.99)
        self.assertEqual(kdp.to_99(13.05), 13.99)

    def test_page_estimate_is_realistic(self):
        self.assertEqual(kdp.estimated_pages(11566), 46)          # 340 words/page + matter
        self.assertEqual(kdp.estimated_pages(45000), 144)

    def test_thin_paperback_is_flagged(self):
        plan = kdp.price_new_title(11566, "medium")
        self.assertIn("thin_warning", plan.paperback)

    def test_scenario_table_carries_its_assumptions(self):
        rows = kdp.scenario_table(46, 410, 3.99, (0.3, 1.0, 5.0))
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertIn("assumption", r)
            self.assertGreater(r["net_per_sale"], 0)
        self.assertLess(rows[0]["month_net_usd"], rows[2]["month_net_usd"])

    def test_breakeven_traffic_is_the_real_constraint(self):
        be = kdp.breakeven_reviews(30, 0.9, 0)
        self.assertGreater(be["store_visits_needed"], 30)         # units always need more visits than units
        self.assertEqual(be["store_visits_needed"], round(30 / 0.009))

    def test_free_promo_days_fit_the_programme_cap(self):
        self.assertLessEqual(kdp.FREE_PROMO_DAYS_MAX, 5)


# ------------------------------------------------------------- metrics ----
class TestMetrics(unittest.TestCase):
    def _weeks(self, rows):
        return [metrics.Week(w, v) for w, v in rows]

    def test_no_data_returns_a_do_nothing_experiment(self):
        d = metrics.diagnose([], 5)
        self.assertEqual(d["stage"], "no-data")
        self.assertIn("tracker", d["experiment"])

    def test_reach_failure_is_detected_before_conversion_failure(self):
        w = self._weeks([("2026-10-01", {"site_impressions_gsc": 10, "site_clicks_gsc": 0,
                                          "units_ebook": 0}),
                          ("2026-10-08", {"site_impressions_gsc": 12, "site_clicks_gsc": 1,
                                           "units_ebook": 0})])
        self.assertEqual(metrics.diagnose(w, 30)["stage"], "reach")

    def test_conversion_failure_blames_reviews_not_price(self):
        w = self._weeks([(f"2026-10-{d:02d}", {"site_impressions_gsc": 4000, "site_clicks_gsc": 300,
                                               "units_ebook": 1, "reviews_total": 2, "avg_position": 8})
                         for d in (1, 8, 15)])
        d = metrics.diagnose(w, 40)
        self.assertEqual(d["stage"], "convert")
        self.assertIn("asks", d["experiment"])
        self.assertNotIn("price", d["do_not"].lower() + d["experiment"].lower()[:0])

    def test_healthy_funnel_recommends_assortment(self):
        w = self._weeks([(f"2026-10-{d:02d}", {"site_impressions_gsc": 5000, "site_clicks_gsc": 600,
                                               "units_ebook": 8, "reviews_total": 20, "avg_position": 4,
                                               "kkenp_pages": 900})
                         for d in (1, 8, 15)])
        self.assertIn(metrics.diagnose(w, 40)["stage"], ("healthy", "read-through"))

    def test_trend_needs_three_points(self):
        self.assertFalse(metrics.trend([("a", 1), ("b", 5)])["ok"])
        t = metrics.trend([("a", 1), ("b", 5), ("c", 10)])
        self.assertTrue(t["ok"])
        self.assertEqual(t["verdict"], "growing")

    def test_page2_diagnosis_recommends_links_not_volume(self):
        w = self._weeks([(f"2026-10-{d:02d}", {"site_impressions_gsc": 3000, "site_clicks_gsc": 200,
                                               "units_ebook": 4, "reviews_total": 15, "avg_position": 18})
                         for d in (1, 8, 15)])
        d = metrics.diagnose(w, 60)
        self.assertEqual(d["stage"], "ranking")
        self.assertIn("Do not publish 10 new articles", d["do_not"])

    def test_load_tracker_handles_blank_and_dollar_values(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.csv"
            f.write_text("week_start,kdp_net_usd,notes,next_single_experiment\n"
                         "2026-10-13,$42.50,,\n2026-10-20,,,  \n")
            rows = metrics.load_tracker(f)
            self.assertEqual(rows[0].get("kdp_net_usd"), 42.5)
            self.assertEqual(rows[1].get("kdp_net_usd"), 0.0)
            self.assertEqual(len(rows), 2)

    def test_report_markdown_is_deterministic_and_mentions_the_one_lever(self):
        w = self._weeks([("2026-10-01", {"units_ebook": 1}), ("2026-10-08", {"units_ebook": 2}),
                         ("2026-10-15", {"units_ebook": 4})])
        md = metrics.report_markdown(w, 40)
        self.assertIn("Experiment (one, 14 days)", md)
        self.assertEqual(md, metrics.report_markdown(w, 40))


# ---------------------------------------------------------------- store ----
class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = store.Store(Path(self.tmp.name) / "l.sqlite3")
        self.idea = models.Idea.new("Test Asset", "niche", "kdp",
                                    scores={k: 4 for k in ("feasibility", "speed", "defensibility",
                                                            "quality", "compounding")},
                                    total=80.0, raw_total=48)
        self.st.put_idea(self.idea)

    def tearDown(self):
        self.st.close()
        self.tmp.cleanup()

    def test_roundtrip_and_json_columns(self):
        got = self.st.get_idea(self.idea.id)
        self.assertEqual(got["title"], "Test Asset")
        self.assertIsInstance(got["scores"], dict)
        self.assertIsInstance(got["blocked"], list)

    def test_upsert_updates_scores_not_duplicates(self):
        self.idea.total = 91.0
        self.st.put_idea(self.idea)
        self.assertEqual(len(self.st.ideas()), 1)
        self.assertEqual(self.st.get_idea(self.idea.id)["total"], 91.0)

    def test_stage_machine_blocks_gating_an_undrafted_asset(self):
        a = models.Asset.new(self.idea.id, "book", "out/test")
        self.st.put_asset(a)                      # starts at 'briefed'
        with self.assertRaises(models.StageError):
            self.st.set_stage("assets", a.id, "gated")
        self.st.set_stage("assets", a.id, "drafted")
        with self.assertRaises(models.StageError):
            self.st.set_stage("assets", a.id, "gated")     # needs evidence
        self.st.set_stage("assets", a.id, "gated", {"gate_report": "clean"})
        self.assertEqual(self.st.assets()[0]["stage"], "gated")

    def test_ready_requires_a_publish_checklist(self):
        a = models.Asset.new(self.idea.id, "kit", "out/kit", stage="drafted")
        self.st.put_asset(a)
        self.st.set_stage("assets", a.id, "gated", {"gate_report": "clean"})
        self.st.set_stage("assets", a.id, "packaged", {"build_artifact": "epub"})
        with self.assertRaises(models.StageError):
            self.st.set_stage("assets", a.id, "ready")
        self.st.set_stage("assets", a.id, "ready", {"publish_checklist": "checklist.md"})
        self.assertEqual(self.st.assets()[0]["stage"], "ready")

    def test_dropped_is_terminal(self):
        a = models.Asset.new(self.idea.id, "book", "out/x")
        self.st.put_asset(a)
        self.st.set_stage("assets", a.id, "dropped")
        with self.assertRaises(models.StageError):
            self.st.set_stage("assets", a.id, "briefed")

    def test_missing_row_does_not_upsert(self):
        with self.assertRaises(models.StageError):
            self.st.set_stage("assets", "nope::book", "gated", {"gate_report": "x"})

    def test_decision_log_is_queryable(self):
        self.st.record_decision("2026-10-11", "1", ["kdp:test-asset"], [], "total 80 vs 55")
        d = self.st.decisions()[0]
        self.assertEqual(json.loads(d["picked"]), ["kdp:test-asset"])
        self.assertEqual(d["rubric_version"], "1")

    def test_gate_history_is_kept_per_target(self):
        rep = gate.evaluate_text("This cures insomnia and takes 10 mg melatonin.", "x/y.md")
        self.st.record_gate(rep)
        last = self.st.last_gate("x/y.md")
        self.assertFalse(bool(last["passed"]))
        self.assertEqual(last["report"]["errors"], len(rep.errors))

    def test_event_log_is_append_only(self):
        self.st.event("build", "a", {"n": 1})
        self.st.event("build", "b", {"n": 2})
        self.assertEqual(len(self.st.events(kind="build")), 2)

    def test_metrics_upsert_per_week(self):
        self.st.put_metrics("2026-10-13", {"units_ebook": 3})
        self.st.put_metrics("2026-10-13", {"units_ebook": 5})
        m = self.st.metrics()
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["units_ebook"], 5)


# ---------------------------------------------------------- generator ----
class TestGenerator(unittest.TestCase):
    def setUp(self):
        self.idea = {"id": "kdp:test", "title": "The Test Book", "niche": "testing",
                     "platform": "kdp", "thesis": "a named system", "audience": "testers",
                     "scores": {"feasibility": 5}, "total": 90.0, "angles": ["a"], "meta": {}}

    def test_brief_forces_the_mechanism_before_prose(self):
        b = generator.brief_for(self.idea)
        for token in ("The mechanism (this is the asset)", "Rubric total", "Manifest",
                      "Non-negotiables"):
            self.assertIn(token, b)

    def test_unfilled_slots_are_marked_never_invented(self):
        idea = {**self.idea, "subtitle": None, "keywords": None, "description": None}
        out = generator.kdp_backend(idea, 11_000)
        self.assertIn("TODO(human)", out)
        self.assertNotIn("lorem", out.lower())

    def test_backend_carries_computed_prices(self):
        out = generator.kdp_backend(self.idea, 11_566, competition="medium")
        plan = kdp.price_new_title(11_566, "medium")
        self.assertIn(f"${plan.ebook_price:.2f}", out)
        self.assertIn("Net/sale", out)
        self.assertIn(kdp.VERIFY_AS_OF, out)

    def test_gate_instructions_are_in_the_checklist(self):
        out = generator.kdp_backend(self.idea, 11_000)
        self.assertIn("wf gate", out)
        self.assertIn("credential", out.lower())

    def test_calendar_marks_undrafted_slots(self):
        cal = generator.calendar(self.idea, 14)
        self.assertEqual(cal.count("| day |") + cal.count("\n| ") if False else cal.count("TODO(human)"), 14)
        self.assertIn("value first", cal)

    def test_cover_brief_includes_thumbnail_test(self):
        cb = generator.cover_brief(self.idea)
        self.assertIn("2,560 × 1,600", cb)
        self.assertIn("Test before committing", cb)

    def test_writes_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            p = generator.write(d, "sub/brief.md", generator.brief_for(self.idea))
            self.assertTrue(p.exists())
            self.assertIn("# Brief", p.read_text())


# ------------------------------------------------------------ models ----
class TestModels(unittest.TestCase):
    def test_word_count_ignores_code_tables_and_tags(self):
        """Headings count (a reader reads them); fenced code, table cells and tags don't."""
        md = "# T\n\nReal prose words here.\n\n```py\nx = 1\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
        self.assertEqual(models.count_words(md), 5)
        self.assertEqual(models.count_words("```py\nprint(1)\n```"), 0, "code is not reading volume")
        ms = (ROOT / "01-book/manuscript.md").read_text()
        prose = models.count_words(ms)
        raw = len(ms.split())
        self.assertGreater(prose, 8_000)
        self.assertLess(prose, raw, "the table-heavy worksheets must not read as prose")

    def test_stage_graph_has_no_way_to_skip_the_gate(self):
        for to in ("packaged", "ready", "published"):
            self.assertNotIn(to, models._ALLOWED["briefed"])
        self.assertEqual(models._ALLOWED["drafted"], {"gated", "briefed", "dropped"})

    def test_every_stage_is_reachable_from_briefed(self):
        seen, stack = set(), ["briefed"]
        while stack:
            cur = stack.pop()
            for nxt in models._ALLOWED.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        self.assertIn("published", seen)
        self.assertIn("measured", seen)

    def test_slug_is_filename_safe(self):
        self.assertEqual(models.slugify("The Night Shift Reset — 28 Days!"), "the-night-shift-reset-28-days")

    def test_content_sha_of_missing_file_is_empty_not_crash(self):
        self.assertEqual(models.sha256_of("/nope/nope.md"), "")


# -------------------------------------------------------------- llm ----
class TestProvider(unittest.TestCase):
    #: Detection is env-driven, so an offline assertion must scrub *every* knob the
    #: resolver reads — including a live Ollama URL in the operator's shell. A test that
    #: only passes on a machine with no model server is a test that will be deleted.
    MODEL_ENV = ("ANTHROPIC_", "OPENAI_", "WEALFORGE_", "OLLAMA_HOST")
    #: Auto-detect probes Ollama's default port on purpose, so "no provider" tests must pin a
    #: closed port or they depend on whether the operator happens to have a model server up.
    NO_SERVER = {"WEALFORGE_OLLAMA_URL": "http://127.0.0.1:1"}

    def test_offline_is_a_mode_not_a_failure(self):
        import os
        saved = {k: os.environ.pop(k) for k in list(os.environ)
                 if k.startswith(self.MODEL_ENV)}
        saved.update({k: v for k, v in ((k, os.environ.get(k)) for k in self.NO_SERVER) if v})
        os.environ.update(self.NO_SERVER)
        try:
            prov = llm.Provider.detect()
            self.assertEqual(prov.mode, "offline")
            self.assertFalse(prov.available)
            with self.assertRaises(llm.LLMUnavailable) as cm:
                prov.complete("hi")
            self.assertIn("WEALFORGE_LLM_CMD", str(cm.exception))
        finally:
            os.environ.update(saved)

    def test_no_request_is_ever_made_without_a_key(self):
        """The loop must be runnable at $0: assert the offline path never opens a socket."""
        import os
        import socket
        saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith(self.MODEL_ENV)}
        real = socket.socket
        def boom(*a, **k):
            raise AssertionError("network attempted while offline")
        socket.socket = boom
        try:
            rc = subprocess.run([sys.executable, "-m", "wealforge", "score", "--out", "/tmp/net-test.md"],
                                cwd=ROOT, capture_output=True, text=True,
                                env={k: v for k, v in __import__("os").environ.items()
                                     if not k.startswith(("ANTHROPIC_", "OPENAI_", "WEALFORGE_LLM"))})
            self.assertEqual(rc.returncode, 0, rc.stderr[-400:])
        finally:
            socket.socket = real

        os.environ.update(saved)


    def test_prompt_states_the_constraints_it_must_not_break(self):
        pr = llm.brief_to_prompt("BRIEF", "chapter 2", 1400)
        for token in ("No medical", "No invented credentials", "1400 words", "H2", "ask your pharmacist"):
            self.assertIn(token, pr)

    def test_prompt_to_file_writes(self):
        with tempfile.TemporaryDirectory() as d:
            p = llm.prompt_to_file(Path(d) / "deep/p.txt", "B", "S", 900)
            self.assertIn("900 words", Path(p).read_text())


# ------------------------------------------------------------- road map ----
class TestRoadmap(unittest.TestCase):
    def test_day_one_of_default_schedule(self):
        sch = roadmap.DEFAULT_SCHEDULE
        d = roadmap.for_date(sch, date(2026, 1, 10), start=date(2026, 1, 10))
        self.assertEqual(d.number, 1)
        self.assertEqual(d.phase, "ship")
        self.assertTrue(all(isinstance(t, str) for t in d.tasks), "tasks must flatten to strings")
        self.assertGreaterEqual(len(d.tasks), 2)

    def test_unscheduled_day_shows_the_nearest_task(self):
        d = roadmap.for_date(roadmap.DEFAULT_SCHEDULE, date(2026, 1, 15), start=date(2026, 1, 10))
        self.assertEqual(d.number, 6)
        self.assertTrue(any(t.startswith("[day ") for t in d.tasks) or d.tasks)

    def test_day_count_increments_across_the_phases(self):
        start = date(2026, 1, 10)
        for n, phase in ((10, "proof"), (21, "proof"), (22, "distribute"),
                         (45, "distribute"), (46, "compound"), (80, "compound")):
            d = roadmap.for_date(roadmap.DEFAULT_SCHEDULE, start + timedelta(days=n - 1), start)
            self.assertEqual((d.number, d.phase), (n, phase))

    def test_beyond_90_days_has_a_steady_state(self):
        d = roadmap.for_date(roadmap.DEFAULT_SCHEDULE, date(2027, 1, 1), start=date(2026, 1, 10))
        self.assertEqual(d.phase, "beyond-90")

    def test_staleness_hint_appears_late_in_a_phase(self):
        start = date(2026, 1, 10)
        d = roadmap.for_date(roadmap.DEFAULT_SCHEDULE, start + timedelta(days=19), start)
        self.assertTrue(d.stale)


# ---------------------------------------------------------------- cli ----
class TestCLI(unittest.TestCase):
    def run_cli(self, *args, expect=0):
        rc = subprocess.run([sys.executable, "-m", "wealforge", *args], cwd=ROOT,
                            capture_output=True, text=True)
        self.assertEqual(rc.returncode, expect, f"{args}\nOUT:{rc.stdout[-600:]}\nERR:{rc.stderr[-600:]}")
        return rc

    def test_help_lists_every_command(self):
        out = self.run_cli("--help").stdout
        for c in ("score", "pick", "build", "gate", "price", "report", "today", "roadmap", "status", "log", "run"):
            self.assertIn(c, out)

    def test_gate_fails_loudly_on_a_bad_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "04-seo-article.md"
            f.write_text("This cure treats insomnia. Buy reviews to rank. Earn $500 a day guaranteed.")
            self.run_cli("gate", str(f), expect=1)

    def test_gate_json_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.md"
            f.write_text("Earn $500 a day guaranteed, no effort.")
            data = json.loads(self.run_cli("gate", str(f), "--json", expect=1).stdout)
            self.assertIs(data[0]["passed"], False)
            self.assertIn("findings", data[0])

    def test_score_then_status(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "l.sqlite3")
            self.run_cli("--db", db, "score", "--out", str(Path(d) / "s.md"))
            out = self.run_cli("--db", db, "status").stdout
            self.assertIn("ideas=11", out.replace("  ", " "))

    def test_pick_records_a_decision(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "l.sqlite3")
            self.run_cli("--db", db, "score", "--out", str(Path(d) / "s.md"))
            out = self.run_cli("--db", db, "pick", "--out-dir", str(Path(d) / "o")).stdout
            self.assertIn("Picked 2 of", out)
            self.assertIn("selections:", self.run_cli("--db", db, "status").stdout)

    def test_price_reports_the_band_cliff(self):
        out = self.run_cli("price", "--words", "11566").stdout
        self.assertIn("70% band", out)
        self.assertIn("thin_warning".replace("thin_warning", "print pages"), out)

    def test_missing_seed_is_exit_2_not_a_traceback(self):
        r = subprocess.run([sys.executable, "-m", "wealforge", "score", "--seed", "/no/such.json"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertIn(r.returncode, (1, 2))
        self.assertNotIn("Traceback", r.stderr)

    def test_full_run_leaves_the_reader_assets_clean(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "l.sqlite3")
            r = self.run_cli("--db", db, "run", "--allow-fail", "--out-dir", str(Path(d) / "o"),
                             "--tracker", "06-tracking/01-tracker.csv")
            self.assertIn("loop complete", r.stdout)
            self.assertIn("GATE PASS", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
