"""Scoring, filters, conviction tiers, and sizing hints."""

import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Optional, Tuple


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

# Trim trigger: sell when price/FMV >= threshold (accounts for uncertainty band + staleness)
_TRIM_THRESHOLDS = {
    #              (fresh, stale)
    "Low":       (1.00, 1.10),
    "Medium":    (1.05, 1.15),
    "High":      (1.15, 1.25),
    "Very High": (1.30, 1.40),
}


def trim_threshold(uncertainty: str, stale: bool) -> float:
    pair = _TRIM_THRESHOLDS.get(uncertainty, (1.05, 1.15))
    return pair[1] if stale else pair[0]


def ratings_age_days(ratings_date_str: Optional[str]) -> Optional[int]:
    if not ratings_date_str:
        return None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(ratings_date_str.strip(), fmt).date()
            return (date.today() - parsed).days
        except ValueError:
            continue
    return None


def is_stale(ratings_date_str: Optional[str]) -> bool:
    age = ratings_age_days(ratings_date_str)
    return age is None or age > 180


def conviction_tier(stars: Optional[int], stale: bool) -> str:
    """
    Stars are the primary signal. Freshness is the confidence modifier.
      ★5 fresh  → STRONG BUY     ★5 stale  → BUY
      ★4 fresh  → BUY            ★4 stale  → WATCH
      ★3        → WATCH
      ★2 fresh  → SELL           ★2 stale  → SELL (sizing demoted to sm)
      ★1        → STRONG SELL
    TRIM is applied separately in score_all() based on price vs FMV threshold.
    """
    if stars == 5:
        return "BUY" if stale else "STRONG BUY"
    if stars == 4:
        return "WATCH" if stale else "BUY"
    if stars == 3:
        return "WATCH"
    if stars == 2:
        return "SELL"
    if stars == 1:
        return "STRONG SELL"
    return "SKIP"


def sizing_hint(conviction: str, moat: str, uncertainty: str, stale: bool = False) -> str:
    # Buy side
    if conviction == "STRONG BUY" and moat == "Wide" and uncertainty == "Low":
        return "lg"
    if conviction in ("STRONG BUY", "BUY"):
        if uncertainty in ("High", "Very High"):
            return "sm"
        return "md"
    if conviction == "WATCH":
        return "monitor"
    # Sell side — mirror buy logic; stale 2-star gets sm (less confident)
    if conviction == "STRONG SELL":
        return "lg"
    if conviction == "SELL":
        if stale or uncertainty in ("High", "Very High"):
            return "sm"
        return "md"
    if conviction == "TRIM":
        return "sm"
    return ""


def parse_stars(raw: Optional[str]) -> Optional[int]:
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


def parse_discount(raw: Optional[str]) -> Optional[float]:
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


def passes_prefilter(row: dict) -> Tuple[bool, Optional[str]]:
    stars = parse_stars(row.get("stars"))

    if stars is None:
        return False, "no star rating"
    if stars <= 2:
        return True, None  # sell signals — pass regardless of discount
    discount = parse_discount(row.get("discount") or row.get("price_to_fmv"))
    if discount is None or discount >= 1.0:
        return False, "overvalued or missing discount"
    return True, None


def _safe_float(val) -> Optional[float]:
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
    ratings_date: Optional[str]
    ratings_age_days: Optional[int]
    stale_rating: bool
    conviction: str
    sizing_hint: str
    fmv_upgraded: bool
    price_change_pct: Optional[float]
    filter_reason: Optional[str]


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

        # TRIM trigger: WATCH stocks trading above uncertainty-adjusted FMV threshold
        if conv == "WATCH" and discount is not None:
            if discount >= trim_threshold(uncertainty or "", stale):
                conv = "TRIM"

        hint = sizing_hint(conv, moat or "", uncertainty or "", stale)

        results.append(ScoredStock(
            ticker=ticker, company=company,
            discount=discount if discount is not None else 0.0,
            discount_pct=round(1 - discount, 4) if discount is not None else 0.0,
            fair_value=fair_value or 0.0, last_price=last_price or 0.0,
            moat=moat or "", uncertainty=uncertainty or "",
            stars=stars or 0, ratings_date=ratings_date,
            ratings_age_days=age, stale_rating=stale,
            conviction=conv, sizing_hint=hint,
            fmv_upgraded=False, price_change_pct=price_change_pct,
            filter_reason=None,
        ))

    # sort: buy side first (best conviction), then sell side by urgency, then skip
    tier_order = {
        "STRONG BUY": 0, "BUY": 1, "WATCH": 2,
        "TRIM": 3, "STRONG SELL": 4, "SELL": 5, "SKIP": 6,
    }
    results.sort(key=lambda s: (tier_order.get(s.conviction, 9), -(s.discount_pct or 0)))
    return results


def _save_snapshot(scored: list) -> None:
    from pathlib import Path
    logs_dir = Path(__file__).parent.parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")
    snapshot = [
        # uncertainty + ratings_date are consumed as-published by week_deltas.py
        # to detect uncertainty moves and analyst-note refreshes.
        {"ticker": s.ticker, "company": s.company, "conviction": s.conviction,
         "stars": s.stars, "fmv": s.fair_value, "last_price": s.last_price,
         "pct_of_fmv": round(s.discount * 100, 1), "moat": s.moat,
         "uncertainty": s.uncertainty, "ratings_date": s.ratings_date}
        for s in scored
    ]
    (logs_dir / f"{today}_scores.json").write_text(
        json.dumps(snapshot, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    from trader_agent.tools.loader import load_screener

    actionable_only = "--actionable-only" in sys.argv

    sheet_id = os.environ["SHEET_ID"]
    rows = load_screener(sheet_id)
    scored = score_all(rows)
    _save_snapshot(scored)

    if actionable_only:
        skip_count = sum(1 for s in scored if s.conviction == "SKIP")
        output = [asdict(s) for s in scored if s.conviction != "SKIP"]
        meta = {
            "_stats": {
                "total": len(scored),
                "strong_buy": sum(1 for s in scored if s.conviction == "STRONG BUY"),
                "buy": sum(1 for s in scored if s.conviction == "BUY"),
                "watch": sum(1 for s in scored if s.conviction == "WATCH"),
                "trim": sum(1 for s in scored if s.conviction == "TRIM"),
                "sell": sum(1 for s in scored if s.conviction == "SELL"),
                "strong_sell": sum(1 for s in scored if s.conviction == "STRONG SELL"),
                "skipped": skip_count,
            },
            "stocks": output,
        }
        json.dump(meta, sys.stdout, indent=2, default=str)
    else:
        json.dump([asdict(s) for s in scored], sys.stdout, indent=2, default=str)

    sys.stdout.write("\n")
