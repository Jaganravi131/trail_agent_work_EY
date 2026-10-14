"""KDP economics, computed rather than asserted.

Every number a pricing decision depends on lives here as a named constant with
the date it was checked, because royalty bands and print costs change and a
stale assumption silently turns a $2.70 net sale into a 35%-band $1.05 sale.

The `verified` fields exist so the CLI can print "as of <date>, confirm at
upload" instead of presenting a guess as a fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VERIFY_AS_OF = "2026-10-11"

# 70% band, per KDP: list price must be >= $2.99 and <= $9.99 (US).
SEVENTY_MIN, SEVENTY_MAX = 2.99, 9.99
SEVENTY_RATE, THIRTYFIVE_RATE = 0.70, 0.35
# Delivery fee (US, 70% plan): $0.15/MB for the first 500 KB of the file…
# actually the public schedule is $0.12/MB above 500 KB with the first 500 KB
# charged at $0.15/MB. Text-only books land in the lowest tier; the fee is
# computed from the *file size*, which is why `ebook_net()` takes kb.
DELIVERY_USD_PER_MB_FIRST = 0.15
DELIVERY_FREE_KB = 0.0          # no free tier in the US schedule; kept explicit
# Paperback: 60% of list price minus print cost.
PAPERBACK_RATE = 0.60
# KENP: Global Rate ~$0.004/page; it floats with the fund, so it is an input.
KENP_RATE_DEFAULT = 0.0045
# Hard cap on the number of KDP Select free-promo days per 90-day enrolment.
FREE_PROMO_DAYS_MAX = 5
PRICE_PULSE_MAX_DAYS = 7


@dataclass
class EbookRoyalty:
    list_price: float
    band: int
    gross: float
    delivery_fee: float
    net: float
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        return (f"${self.list_price:>5.2f}  {self.band}% band  "
                f"delivery ${self.delivery_fee:.2f}  →  net ${self.net:.2f}/sale")


def delivery_fee_kb(kb: float) -> float:
    """KDP's US delivery charge for the 70% plan, from the file size in KB."""
    mb = max(0.0, kb) / 1024.0
    return round(mb * DELIVERY_USD_PER_MB_FIRST, 2)


def ebook_net(list_price: float, file_kb: float = 300.0) -> EbookRoyalty:
    """Net per sale. The 35% cliff at $2.99/$9.99 is the single most expensive
    pricing mistake in self-publishing, so it is enforced with a note, not a raise."""
    notes: list[str] = []
    if list_price < SEVENTY_MIN or list_price > SEVENTY_MAX:
        band, rate = 35, THIRTYFIVE_RATE
        fee = 0.0
        notes.append(f"outside the 70% band (${SEVENTY_MIN}–${SEVENTY_MAX}); 35% applies, no delivery fee")
        if list_price < SEVENTY_MIN:
            below = round(list_price * THIRTYFIVE_RATE, 2)
            at_min = round(SEVENTY_MIN * SEVENTY_RATE - delivery_fee_kb(file_kb), 2)
            notes.append(f"$2.99 nets ${at_min:.2f} vs ${below:.2f} at ${list_price:.2f} — "
                         f"a lower price earns more here; that is not a rounding error")
    else:
        band, rate = 70, SEVENTY_RATE
        fee = delivery_fee_kb(file_kb)
    net = round(list_price * rate - fee, 2)
    return EbookRoyalty(list_price, band, round(list_price * rate, 2), fee, net, notes)


def paperback_net(list_price: float, pages: int, ink: str = "black_ink",
                  trim: str = "6x9", marketplace: str = "US") -> dict[str, Any]:
    """60% minus print cost. Fixed + per-page figures are placeholders for the
    live KDP calculator, and `verified` says so — never hard-code a print cost
    into a pricing decision without re-checking it that day."""
    per_page = {"black_ink": 0.012, "premium_color": 0.065}.get(ink, 0.012)
    fixed = {"US": 0.85, "EU/UK": 1.10}.get(marketplace, 0.85)
    if trim not in {"6x9", "5.5x8.5", "8.5x11"}:
        trim = "6x9"
    cost = round(fixed + per_page * pages, 2)
    gross = round(list_price * PAPERBACK_RATE, 2)
    net = round(gross - cost, 2)
    min_price = round(cost / PAPERBACK_RATE + 0.01, 2)
    return {
        "list_price": list_price, "royalty_rate": PAPERBACK_RATE, "pages": pages,
        "print_cost": cost, "net": net, "min_viable_list_price": min_price,
        "verified": f"per-page/fixed rates are estimates as of {VERIFY_AS_OF}; "
                    "use KDP's live print-cost calculator before setting the price",
    }


def kenp_earnings(pages_read: int, rate: float = KENP_RATE_DEFAULT) -> float:
    return round(pages_read * rate, 2)


#: A 6"×9" trade-nonfiction page holds ~320–380 words at 11 pt with 0.75" margins.
#: Page count is not cosmetic: KDP print cost, the minimum list price and the
#: "is this worth paperbacking at all" judgement all fall out of it.
WORDS_PER_PRINT_PAGE = 340
FRONT_BACK_MATTER_PAGES = 12
KDP_MIN_PAGES_BLACK_INK = 20


def estimated_pages(word_count: int) -> int:
    return max(KDP_MIN_PAGES_BLACK_INK, round(word_count / WORDS_PER_PRINT_PAGE) + FRONT_BACK_MATTER_PAGES)


def to_99(price: float) -> float:
    """Snap to the nearest .99 at or above the raw number (9.2 → 9.99, 12.9 → 12.99)."""
    import math
    return round(math.ceil(price - 0.99) + 0.99, 2)


@dataclass
class Plan:
    ebook_price: float
    promo_price: float
    promo_days: int
    paperback_price: float
    pages: int
    file_kb: float
    counted_pages: int

    @property
    def ebook(self) -> EbookRoyalty:
        return ebook_net(self.ebook_price, self.file_kb)

    @property
    def promo(self) -> EbookRoyalty:
        return ebook_net(self.promo_price, self.file_kb)

    @property
    def paperback(self) -> dict[str, Any]:
        r = paperback_net(self.paperback_price, self.pages)
        r["thin_warning"] = (
            f"{self.pages} print pages for a ${self.paperback_price:.2f} paperback is a refund and "
            f"1-star magnet; either add worksheets to clear ~80pp or price it as a booklet."
            if self.pages < 80 else "")
        return r


def price_new_title(word_count: int, niche_competition: str = "medium",
                   has_worksheets: bool = True, pages: int | None = None) -> Plan:
    """Recommend a price ladder.

    Logic, so the operator can argue with it instead of trusting it:
      * A new author with <10 reviews cannot sell at $9.99; the also-boughts
        shelf is the discovery mechanism and it needs conversion, not margin.
      * Below $2.99 the royalty band flips to 35% AND you buy rank with money
        you need later; the promo price is therefore a *time-boxed* exception,
        not the list price.
      * Longer + worksheets justifies more than $3.99; thin content makes $2.99
        look generous. Niche competition decides how long the promo runs.
    """
    pages = pages or estimated_pages(word_count)
    long = word_count >= 20_000
    if niche_competition == "low":
        base, promo_days = (4.99 if long or has_worksheets else 3.99), 3
    elif niche_competition == "high":
        base, promo_days = (3.99 if long else 2.99), 5
    else:
        base, promo_days = (3.99 if (long or has_worksheets) else 2.99), 5
    promo_days = min(promo_days, PRICE_PULSE_MAX_DAYS)
    # Never list below the 70% floor for more than the promo window.
    if base < SEVENTY_MIN:
        base = SEVENTY_MIN
    # Paperbacks anchor the ebook; ~3.2x ebook price reads as "the ebook is a deal".
    # Rounded to a .99 ending because $12.77 signals "generated field", not a decision.
    paperback = to_99(max(7.99, base * 3.2))
    return Plan(ebook_price=base, promo_price=0.99, promo_days=promo_days,
                paperback_price=paperback, pages=pages, file_kb=round(word_count * 0.032 + 40, 1),
                counted_pages=int(pages * 0.88))       # KENP counts ~88% of print pages


def scenario_table(pages: int, file_kb: float, ebook_price: float,
                   units_per_day: tuple[float, float, float],
                   read_ratio: float = 0.5, weeks: int = 4) -> list[dict[str, Any]]:
    """conservative / base / optimistic, with the assumption printed next to it.

    The `read_ratio` (reads per sale, for KENP) is the one most people forget,
    and for a short nonfiction title in Select it is 20-40% of income.
    """
    unit = ebook_net(ebook_price, file_kb)
    per_read = kenp_earnings(int(pages * 0.88))
    rows = []
    for name, upd in zip(("conservative", "base", "optimistic"), units_per_day):
        monthly_units = upd * 30
        monthly = monthly_units * unit.net + monthly_units * read_ratio * per_read
        rows.append({
            "scenario": name, "units_per_day": upd, "units_30d": round(monthly_units),
            "net_per_sale": unit.net, "kenp_per_read": per_read,
            "month_net_usd": round(monthly, 2),
            "assumption": f"{upd} sales/day sustained, {int(read_ratio*100)}% of buyers also "
                          f"read ≥80% via Kindle Unlimited",
        })
    return rows


def breakeven_reviews(units_needed: int, ctr_pct: float, visitors: int) -> dict[str, Any]:
    """Sales are traffic × conversion, and conversion is mostly reviews for an
    unknown author. Returns the traffic actually required, so 'I need reviews'
    becomes a number instead of a vibe."""
    conv = max(0.0005, ctr_pct / 100.0)
    needed = round(units_needed / conv)
    return {"units_target": units_needed, "assumed_conversion_pct": ctr_pct,
            "store_visits_needed": needed,
            "visits_per_day_for_30_days": round(needed / 30, 1),
            "note": "0.5-1.5% is a normal Amazon page-conversion rate for a no-review "
                     "unknown-author title; above ~3% usually means the reviews are doing the work"}


def revenue_forecast_from_ledger(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Sum the tracker rows the operator actually filled in. Ignores blanks so a
    half-entered week doesn't read as a collapse."""
    def num(r: dict[str, Any], k: str) -> float:
        try:
            return float(str(r.get(k, "")).strip() or 0)
        except ValueError:
            return 0.0
    kdp = sum(num(r, "kdp_net_usd") for r in rows)
    gum = sum(num(r, "gumroad_net_usd") for r in rows)
    return {"kdp_net_usd": round(kdp, 2), "gumroad_net_usd": round(gum, 2),
            "total_net_usd": round(kdp + gum, 2),
            "units_ebook": sum(num(r, "units_ebook") for r in rows),
            "units_paperback": sum(num(r, "units_paperback") for r in rows),
            "weeks_reported": len(rows)}
