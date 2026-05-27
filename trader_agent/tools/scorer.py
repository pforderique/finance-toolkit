"""Scoring, filters, conviction tiers, and sizing hints."""

import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime


_DATE_FORMATS = [
    "%Y-%m-%d",
    "%b %d, %Y",
    "%d %b %Y",
    "%d %b %Y %H:%M, UTC",
]

MOAT_WEIGHT = {"Wide": 1.0, "Narrow": 0.85, "None": 0.65}
UNCERT_WEIGHT = {
    "Low": 1.0,
    "Medium": 0.85,
    "High": 0.70,
    "Very High": 0.55,
    "Extreme": 0.40,
}


def ratings_age_days(ratings_date_str: str | None) -> int | None:
    if not ratings_date_str:
        return None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(ratings_date_str.strip(), fmt).date()
            return (date.today() - parsed).days
        except ValueError:
            continue
    return None


def is_stale(ratings_date_str: str | None) -> bool:
    age = ratings_age_days(ratings_date_str)
    return age is None or age > 180


def conviction_tier(stars: int | None, stale: bool) -> str:
    """
    Stars are the primary signal. Freshness is the confidence modifier.
      ★5 fresh  → STRONG BUY
      ★5 stale  → BUY
      ★4 fresh  → BUY
      ★4 stale  → WATCH
      ★3        → WATCH
      ★1-2      → SKIP
    """
    if stars == 5:
        return "BUY" if stale else "STRONG BUY"
    if stars == 4:
        return "WATCH" if stale else "BUY"
    if stars == 3:
        return "WATCH"
    return "SKIP"


def sizing_hint(conviction: str, moat: str, uncertainty: str) -> str:
    if conviction == "STRONG BUY" and moat == "Wide" and uncertainty == "Low":
        return "consider larger position"
    if conviction in ("STRONG BUY", "BUY"):
        if uncertainty in ("High", "Very High"):
            return "small starter position only"
        return "standard position"
    if conviction == "WATCH":
        return "monitor, not yet"
    return ""


def parse_stars(raw: str | None) -> int | None:
    if raw is None:
        return None
    cleaned = str(raw).strip()
    count = cleaned.count("★")
    if count > 0:
        return count
    try:
        val = int(cleaned)
        if 1 <= val <= 5:
            return val
    except (ValueError, TypeError):
        pass
    return None


def parse_discount(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    is_pct = s.endswith("%")
    try:
        val = float(s.rstrip("%"))
        if is_pct:
            val = val / 100.0
        return val
    except (ValueError, TypeError):
        return None


def passes_prefilter(row: dict) -> tuple[bool, str | None]:
    stars = parse_stars(row.get("stars"))
    discount = parse_discount(row.get("discount") or row.get("price_to_fmv"))

    if stars is None or stars <= 2:
        return False, f"{stars}-star exclude" if stars else "no star rating"
    if discount is None or discount >= 1.0:
        return False, "overvalued or missing discount"
    return True, None


def _safe_float(val) -> float | None:
    try:
        return float(str(val).strip().lstrip("$").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _normalize(row: dict) -> dict:
    out = {k.lower().strip(): v for k, v in row.items()}
    if "price_change (%)" in out:
        out["price_change_pct"] = out.pop("price_change (%)")
    return out


@dataclass
class ScoredStock:
    ticker: str
    company: str
    discount: float          # price/FMV ratio (e.g. 0.89 = 11% off)
    discount_pct: float      # 1 - discount, as percentage off FMV (e.g. 0.11)
    fair_value: float
    last_price: float
    moat: str
    uncertainty: str
    stars: int
    ratings_date: str | None
    ratings_age_days: int | None
    stale_rating: bool
    conviction: str
    sizing_hint: str
    fmv_upgraded: bool
    price_change_pct: float | None
    filter_reason: str | None


def score_all(rows: list[dict]) -> list[ScoredStock]:
    results = []
    for raw_row in rows:
        row = _normalize(raw_row)
        ticker = str(row.get("ticker", "")).strip()
        company = str(row.get("company", row.get("name", ""))).strip()
        moat = str(row.get("moat", "")).strip() or None
        uncertainty = str(row.get("uncertainty", "")).strip() or None
        ratings_date = row.get("ratings_date") or row.get("ratingsdate") or None
        raw_discount = row.get("discount") or row.get("price_to_fmv")
        discount = parse_discount(raw_discount)
        stars = parse_stars(row.get("stars"))
        fair_value = _safe_float(row.get("fair_value") or row.get("fairvalue"))
        last_price = _safe_float(row.get("last_price") or row.get("price"))
        price_change_pct = _safe_float(row.get("price_change_pct") or row.get("pricechangepct"))

        age = ratings_age_days(ratings_date)
        stale = is_stale(ratings_date)

        passed, reason = passes_prefilter(row)
        if not passed:
            results.append(ScoredStock(
                ticker=ticker, company=company,
                discount=discount or 0.0, discount_pct=1 - (discount or 1.0),
                fair_value=fair_value or 0.0, last_price=last_price or 0.0,
                moat=moat or "", uncertainty=uncertainty or "",
                stars=stars or 0, ratings_date=ratings_date,
                ratings_age_days=age, stale_rating=stale,
                conviction="SKIP", sizing_hint="",
                fmv_upgraded=False, price_change_pct=price_change_pct,
                filter_reason=reason,
            ))
            continue

        conv = conviction_tier(stars, stale)
        hint = sizing_hint(conv, moat or "", uncertainty or "")

        results.append(ScoredStock(
            ticker=ticker, company=company,
            discount=discount, discount_pct=round(1 - discount, 4),
            fair_value=fair_value or 0.0, last_price=last_price or 0.0,
            moat=moat or "", uncertainty=uncertainty or "",
            stars=stars or 0, ratings_date=ratings_date,
            ratings_age_days=age, stale_rating=stale,
            conviction=conv, sizing_hint=hint,
            fmv_upgraded=False, price_change_pct=price_change_pct,
            filter_reason=None,
        ))

    # sort: conviction tier first, then by discount_pct descending within tier
    tier_order = {"STRONG BUY": 0, "BUY": 1, "WATCH": 2, "SKIP": 3}
    results.sort(key=lambda s: (tier_order.get(s.conviction, 9), -(s.discount_pct or 0)))
    return results


if __name__ == "__main__":
    from trader_agent.tools.loader import load_screener

    actionable_only = "--actionable-only" in sys.argv

    sheet_id = os.environ["SHEET_ID"]
    rows = load_screener(sheet_id)
    scored = score_all(rows)

    if actionable_only:
        skip_count = sum(1 for s in scored if s.conviction == "SKIP")
        output = [asdict(s) for s in scored if s.conviction != "SKIP"]
        # inject stats so agent knows total
        meta = {
            "_stats": {
                "total": len(scored),
                "strong_buy": sum(1 for s in scored if s.conviction == "STRONG BUY"),
                "buy": sum(1 for s in scored if s.conviction == "BUY"),
                "watch": sum(1 for s in scored if s.conviction == "WATCH"),
                "skipped": skip_count,
            },
            "stocks": output,
        }
        json.dump(meta, sys.stdout, indent=2, default=str)
    else:
        json.dump([asdict(s) for s in scored], sys.stdout, indent=2, default=str)

    sys.stdout.write("\n")
