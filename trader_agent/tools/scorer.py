"""Scoring, filters, conviction tiers, and sizing hints."""

import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone

from ms_screener.src.config import DEFAULT_SNAPSHOT_TAB


MOAT_WEIGHT = {"Wide": 1.0, "Narrow": 0.85, "None": 0.65}
UNCERT_WEIGHT = {
    "Low": 1.0,
    "Medium": 0.85,
    "High": 0.70,
    "Very High": 0.55,
    "Extreme": 0.40,
}

_DATE_FORMATS = [
    "%Y-%m-%d",
    "%b %d, %Y",
    "%d %b %Y",
    "%d %b %Y %H:%M, UTC",
]


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


def freshness_weight(ratings_date_str: str | None) -> float:
    age = ratings_age_days(ratings_date_str)
    if age is None or age > 180:
        return 0.70
    if age > 90:
        return 0.85
    return 1.0


def buy_score(
    discount: float | None,
    moat: str | None,
    uncertainty: str | None,
    ratings_date: str | None,
) -> float | None:
    if discount is None:
        return None
    mw = MOAT_WEIGHT.get(moat)
    uw = UNCERT_WEIGHT.get(uncertainty)
    if mw is None or uw is None:
        return None
    fw = freshness_weight(ratings_date)
    return discount * mw * uw * fw


def passes_prefilter(row: dict) -> tuple[bool, str | None]:
    stars = parse_stars(row.get("stars"))
    discount = parse_discount(row.get("discount") or row.get("price_to_fmv"))
    moat = row.get("moat")
    uncertainty = row.get("uncertainty")

    if stars == 1:
        return False, "1-star hard exclude"
    if stars == 2 and (discount is None or discount >= 0.70):
        return False, "2-star insufficient discount"
    if discount is None or discount >= 1.0:
        return False, "overvalued or missing discount"
    if not moat or moat not in MOAT_WEIGHT:
        return False, "moat unknown"
    if not uncertainty:
        return False, "uncertainty missing"
    return True, None


def parse_stars(raw: str | None) -> int | None:
    if raw is None:
        return None
    cleaned = str(raw).strip()
    # count star chars
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


@dataclass
class ScoredStock:
    ticker: str
    company: str
    discount: float
    fair_value: float
    last_price: float
    moat: str
    uncertainty: str
    stars: int
    ratings_date: str | None
    ratings_age_days: int | None
    freshness_weight: float
    buy_score: float
    conviction: str
    sizing_hint: str
    stale_rating: bool
    fmv_upgraded: bool
    price_change_pct: float | None
    filter_reason: str | None


def conviction_tier(score: float) -> str:
    if score <= 0.50:
        return "STRONG BUY"
    if score <= 0.65:
        return "BUY"
    if score <= 0.75:
        return "WATCH"
    return "SKIP"


def sizing_hint(conviction: str, moat: str, uncertainty: str) -> str:
    if conviction == "STRONG BUY" and moat == "Wide" and uncertainty == "Low":
        return "consider larger position"
    if conviction == "BUY":
        if uncertainty in ("High", "Very High"):
            return "small starter position only"
        return "standard position"
    if conviction == "WATCH":
        return "monitor, not yet"
    return ""


def _safe_float(val) -> float | None:
    try:
        return float(str(val).strip().lstrip("$").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _normalize(row: dict) -> dict:
    """Lowercase all keys and map known sheet column variants."""
    out = {k.lower().strip(): v for k, v in row.items()}
    # sheet-specific renames
    if "ratings_date" not in out and "ratings_date" not in row:
        out["ratings_date"] = out.pop("ratings_date", None)
    if "price_change (%)" in out:
        out["price_change_pct"] = out.pop("price_change (%)")
    return out


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

        passed, reason = passes_prefilter(row)

        age = ratings_age_days(ratings_date)
        fw = freshness_weight(ratings_date)
        stale = (age is None or age > 180)

        if not passed:
            results.append(ScoredStock(
                ticker=ticker,
                company=company,
                discount=discount or 0.0,
                fair_value=fair_value or 0.0,
                last_price=last_price or 0.0,
                moat=moat or "",
                uncertainty=uncertainty or "",
                stars=stars or 0,
                ratings_date=ratings_date,
                ratings_age_days=age,
                freshness_weight=fw,
                buy_score=None,
                conviction="SKIP",
                sizing_hint="",
                stale_rating=stale,
                fmv_upgraded=False,
                price_change_pct=price_change_pct,
                filter_reason=reason,
            ))
            continue

        score = buy_score(discount, moat, uncertainty, ratings_date)
        if score is None:
            results.append(ScoredStock(
                ticker=ticker,
                company=company,
                discount=discount or 0.0,
                fair_value=fair_value or 0.0,
                last_price=last_price or 0.0,
                moat=moat or "",
                uncertainty=uncertainty or "",
                stars=stars or 0,
                ratings_date=ratings_date,
                ratings_age_days=age,
                freshness_weight=fw,
                buy_score=None,
                conviction="SKIP",
                sizing_hint="",
                stale_rating=stale,
                fmv_upgraded=False,
                price_change_pct=price_change_pct,
                filter_reason="score computation failed",
            ))
            continue

        conv = conviction_tier(score)
        if conv == "STRONG BUY" and stale:
            conv = "BUY"
        hint = sizing_hint(conv, moat, uncertainty)

        results.append(ScoredStock(
            ticker=ticker,
            company=company,
            discount=discount,
            fair_value=fair_value or 0.0,
            last_price=last_price or 0.0,
            moat=moat,
            uncertainty=uncertainty,
            stars=stars or 0,
            ratings_date=ratings_date,
            ratings_age_days=age,
            freshness_weight=fw,
            buy_score=score,
            conviction=conv,
            sizing_hint=hint,
            stale_rating=stale,
            fmv_upgraded=False,
            price_change_pct=price_change_pct,
            filter_reason=None,
        ))

    results.sort(key=lambda s: (s.buy_score is None, s.buy_score or 9999))
    return results


if __name__ == "__main__":
    from trader_agent.tools.loader import load_screener

    sheet_id = os.environ["SHEET_ID"]
    rows = load_screener(sheet_id)
    scored = score_all(rows)
    json.dump([asdict(s) for s in scored], sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
