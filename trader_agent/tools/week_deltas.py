"""7-day "What Changed" delta feed for the morning brief.

Answers the question the brief was failing to answer: *did anything actually move
this week?* — with FMV up/down called out explicitly rather than buried.

Sources (in priority order):
  1. Daily full-universe snapshots  `trader_agent/logs/YYYY-MM-DD_scores.json`
     (written by scorer._save_snapshot). Full coverage, daily granularity.
  2. `Data_Changes` sheet -> /tmp/fmv_history.db (history_db.py). Official
     Morningstar revision rows; used to CONFIRM snapshot-derived FMV/star moves
     and to backfill revisions that fall in a snapshot gap (weekend/holiday).

Materiality model — the user is a 3-30 year value investor, so a delta only
counts if it changes the *thesis*, not the *quote*:

  material  FMV revision, star change, moat change, uncertainty change,
            analyst-note refresh, new coverage, and conviction moves that are
            driven by any of those.
  price     Conviction/band moves where stars + FMV + moat are all unchanged —
            i.e. the price wiggled across a threshold. Only surfaced when the
            move enters or exits a decision tier (BUY or better, SELL side).
  noise     WATCH<->SKIP flapping with no rating change at all. Never listed
            per-line; collapsed into a single footnote so the feed stays honest
            without being spammed.

Usage:
    python -m trader_agent.tools.week_deltas                 # JSON, 7d, today
    python -m trader_agent.tools.week_deltas --render        # terminal preview
    python -m trader_agent.tools.week_deltas --asof 2026-07-17 --days 7
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs"
DB_PATH = "/tmp/fmv_history.db"

DEFAULT_WINDOW_DAYS = 7

# Decision tiers — a price-driven move in/out of these is worth a line.
# WATCH and SKIP are both "do nothing", so flapping between them is noise.
_DECISION_TIERS = {"STRONG BUY", "BUY", "TRIM", "SELL", "STRONG SELL"}
_TIER_RANK = {
    "STRONG BUY": 0, "BUY": 1, "WATCH": 2, "SKIP": 3,
    "TRIM": 4, "SELL": 5, "STRONG SELL": 6,
}
# Lower is better (less uncertain).
_UNCERTAINTY_RANK = {"Low": 0, "Medium": 1, "High": 2, "Very High": 3, "Extreme": 4}
_MOAT_RANK = {"None": 0, "Narrow": 1, "Wide": 2}

# Sort priority within a single day: thesis-movers before price-movers.
_KIND_PRIORITY = {
    "fmv": 0,
    "stars": 1,
    "moat": 2,
    "uncertainty": 3,
    "conviction": 4,
    "new_coverage": 5,
    "rating_refresh": 6,
    "band_cross": 7,
}

# FMV moves smaller than this are rounding/quant-jitter, not a revision.
_FMV_EPSILON_PCT = 0.5


# --------------------------------------------------------------------------
# snapshot loading
# --------------------------------------------------------------------------

def _snapshot_dates(logs_dir: Path) -> list[str]:
    out = []
    for f in logs_dir.glob("*_scores.json"):
        stem = f.name.split("_scores.json")[0]
        try:
            datetime.strptime(stem, "%Y-%m-%d")
        except ValueError:
            continue
        out.append(stem)
    return sorted(out)


def _load_snapshot(logs_dir: Path, day: str) -> dict[str, dict]:
    path = logs_dir / f"{day}_scores.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {r["ticker"]: r for r in rows if r.get("ticker")}


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------

def _age_label(as_of: date, day: str) -> tuple[int, str]:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    n = (as_of - d).days
    return n, "today" if n <= 0 else f"{n}d ago"


def _money(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v >= 1000 or float(v).is_integer():
        return f"${v:,.0f}"
    return f"${v:,.2f}"


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# per-ticker diff between two consecutive snapshots
# --------------------------------------------------------------------------

def _diff_ticker(day: str, prev: dict, cur: dict) -> list[dict]:
    """Return the list of change records between two snapshots of one ticker."""
    ticker = cur["ticker"]
    company = cur.get("company") or prev.get("company") or ""
    changes: list[dict] = []

    p_fmv, c_fmv = _num(prev.get("fmv")), _num(cur.get("fmv"))
    p_stars, c_stars = prev.get("stars"), cur.get("stars")
    p_moat, c_moat = prev.get("moat"), cur.get("moat")
    p_unc, c_unc = prev.get("uncertainty"), cur.get("uncertainty")
    p_rdate, c_rdate = prev.get("ratings_date"), cur.get("ratings_date")

    def rec(**kw) -> dict:
        base = {"date": day, "ticker": ticker, "company": company,
                "severity": "material", "driver": "rating",
                "confirmed_by_sheet": False}
        base.update(kw)
        return base

    # --- FMV revision -----------------------------------------------------
    fmv_moved = False
    if p_fmv and c_fmv and p_fmv > 0:
        pct = (c_fmv - p_fmv) / p_fmv * 100
        if abs(pct) >= _FMV_EPSILON_PCT:
            fmv_moved = True
            up = pct > 0
            changes.append(rec(
                kind="fmv",
                label="FMV raised" if up else "FMV cut",
                arrow="↑" if up else "↓",
                direction="up" if up else "down",
                **{"from": p_fmv, "to": c_fmv},
                from_display=_money(p_fmv), to_display=_money(c_fmv),
                pct=round(pct, 1),
                text=f"FMV {'↑' if up else '↓'} {pct:+.1f}% "
                     f"({_money(p_fmv)} → {_money(c_fmv)})",
            ))

    # --- star rating ------------------------------------------------------
    stars_moved = False
    if isinstance(p_stars, int) and isinstance(c_stars, int) and p_stars != c_stars \
            and p_stars > 0 and c_stars > 0:
        stars_moved = True
        up = c_stars > p_stars
        changes.append(rec(
            kind="stars",
            label="Star rating up" if up else "Star rating down",
            arrow="↑" if up else "↓",
            direction="up" if up else "down",
            **{"from": p_stars, "to": c_stars},
            from_display=f"{p_stars}★", to_display=f"{c_stars}★",
            pct=None,
            text=f"Stars {'↑' if up else '↓'} {p_stars}★ → {c_stars}★",
        ))

    # --- moat -------------------------------------------------------------
    moat_moved = False
    if p_moat and c_moat and p_moat != c_moat:
        moat_moved = True
        up = _MOAT_RANK.get(c_moat, -1) > _MOAT_RANK.get(p_moat, -1)
        changes.append(rec(
            kind="moat",
            label="Moat upgraded" if up else "Moat downgraded",
            arrow="↑" if up else "↓",
            direction="up" if up else "down",
            **{"from": p_moat, "to": c_moat},
            from_display=p_moat, to_display=c_moat, pct=None,
            text=f"Moat {'↑' if up else '↓'} {p_moat} → {c_moat}",
        ))

    # --- uncertainty (arrow = thesis direction, so lower uncertainty is ↑) --
    unc_moved = False
    if p_unc and c_unc and p_unc != c_unc:
        unc_moved = True
        better = _UNCERTAINTY_RANK.get(c_unc, 9) < _UNCERTAINTY_RANK.get(p_unc, 9)
        changes.append(rec(
            kind="uncertainty",
            label="Uncertainty lowered" if better else "Uncertainty raised",
            arrow="↑" if better else "↓",
            direction="up" if better else "down",
            **{"from": p_unc, "to": c_unc},
            from_display=p_unc, to_display=c_unc, pct=None,
            text=f"Uncertainty {'↓' if better else '↑'} {p_unc} → {c_unc}",
        ))

    # --- analyst note refresh --------------------------------------------
    # Consumes ratings_date as published; does not parse or alter it.
    if p_rdate and c_rdate and str(p_rdate).strip() != str(c_rdate).strip():
        # Where the new date came from, and whether FMV moved with it — the two
        # things that decide how much weight a refresh deserves.
        src = cur.get("ratings_date_source")
        if fmv_moved:
            how = "FMV revised"
        elif src:
            how = f"via {src}"
        else:
            how = "source unknown"
        changes.append(rec(
            kind="rating_refresh",
            label="Analyst note refreshed",
            arrow="↻", direction="flat",
            **{"from": p_rdate, "to": c_rdate},
            from_display=str(p_rdate), to_display=str(c_rdate), pct=None,
            source=src, how=how,
            text=f"Rating date {p_rdate} → {c_rdate} ({how})",
        ))

    rating_moved = fmv_moved or stars_moved or moat_moved or unc_moved

    # --- conviction -------------------------------------------------------
    p_conv, c_conv = prev.get("conviction"), cur.get("conviction")
    if p_conv and c_conv and p_conv != c_conv:
        up = _TIER_RANK.get(c_conv, 9) < _TIER_RANK.get(p_conv, 9)
        pair = {p_conv, c_conv}
        is_flap = pair <= {"WATCH", "SKIP"}
        touches_decision = bool(pair & _DECISION_TIERS)

        if rating_moved:
            severity, driver = "material", "rating"
        elif is_flap or not touches_decision:
            severity, driver = "noise", "price"
        else:
            severity, driver = "price", "price"

        suffix = "" if driver == "rating" else "  <i>price-driven</i>"
        changes.append(rec(
            kind="conviction", severity=severity, driver=driver,
            label="Conviction upgraded" if up else "Conviction downgraded",
            arrow="↑" if up else "↓",
            direction="up" if up else "down",
            **{"from": p_conv, "to": c_conv},
            from_display=p_conv, to_display=c_conv, pct=None,
            text=f"Conviction {'↑' if up else '↓'} {p_conv} → {c_conv}{suffix}",
        ))

    # --- price crossing the FMV line -------------------------------------
    # Only when it is NOT already explained by a conviction line, and only
    # across the FMV parity line (a real valuation boundary, not a wiggle).
    p_pct, c_pct = _num(prev.get("pct_of_fmv")), _num(cur.get("pct_of_fmv"))
    if p_pct is not None and c_pct is not None and p_conv == c_conv:
        crossed_up = p_pct < 100 <= c_pct
        crossed_down = p_pct >= 100 > c_pct
        if crossed_up or crossed_down:
            changes.append(rec(
                kind="band_cross", severity="price", driver="price",
                label="Price crossed above FMV" if crossed_up else "Price crossed below FMV",
                arrow="↑" if crossed_up else "↓",
                direction="up" if crossed_up else "down",
                **{"from": p_pct, "to": c_pct},
                from_display=f"{p_pct:.0f}% of FMV", to_display=f"{c_pct:.0f}% of FMV",
                pct=None,
                text=f"Price crossed {'above' if crossed_up else 'below'} FMV "
                     f"({p_pct:.0f}% → {c_pct:.0f}% of FMV)  <i>price-driven</i>",
            ))

    return changes


# --------------------------------------------------------------------------
# Data_Changes corroboration
# --------------------------------------------------------------------------

def _sheet_revisions(window_start: str, window_end: str, db_path: str = DB_PATH) -> list[dict]:
    """Official Morningstar revision rows inside the window, from Data_Changes.

    NOTE: the sheet's `previous_rating_date` / `current_rating_date` columns
    currently carry uncertainty strings, not dates. That parsing pipeline is
    owned elsewhere, so those two columns are deliberately NOT consumed here.
    """
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = con.execute(
            """
            SELECT date, ticker, company, previous_fair_value, current_fair_value,
                   fair_value_delta, previous_stars, current_stars
            FROM fmv_history
            WHERE date IS NOT NULL AND date >= ? AND date <= ?
            ORDER BY date ASC
            """,
            (window_start, window_end),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    return [
        {"date": r[0], "ticker": (r[1] or "").upper(), "company": r[2],
         "previous_fmv": r[3], "current_fmv": r[4], "delta": r[5],
         "previous_stars": r[6], "current_stars": r[7]}
        for r in rows
    ]


def _merge_sheet_revisions(changes: list[dict], sheet_rows: list[dict]) -> tuple[list[dict], int]:
    """Mark snapshot changes confirmed by the sheet; backfill ones we missed."""
    seen = {(c["date"], c["ticker"], c["kind"]) for c in changes}
    by_tk = {}
    for c in changes:
        if c["kind"] in ("fmv", "stars"):
            by_tk.setdefault((c["ticker"], c["kind"]), []).append(c)

    added = 0
    for row in sheet_rows:
        tk = row["ticker"]

        for kind in ("fmv", "stars"):
            if kind == "fmv":
                pv, cv = _num(row["previous_fmv"]), _num(row["current_fmv"])
                if not pv or not cv or pv <= 0:
                    continue
                pct = (cv - pv) / pv * 100
                if abs(pct) < _FMV_EPSILON_PCT:
                    continue
            else:
                pv, cv = row["previous_stars"], row["current_stars"]
                if not isinstance(pv, int) or not isinstance(cv, int) or pv == cv:
                    continue
                pct = None

            existing = by_tk.get((tk, kind))
            if existing:
                for c in existing:
                    c["confirmed_by_sheet"] = True
                continue
            if (row["date"], tk, kind) in seen:
                continue

            # Snapshot gap — the revision is real but no snapshot pair caught it.
            up = (cv > pv)
            if kind == "fmv":
                text = (f"FMV {'↑' if up else '↓'} {pct:+.1f}% "
                        f"({_money(pv)} → {_money(cv)})")
                from_d, to_d = _money(pv), _money(cv)
            else:
                text = f"Stars {'↑' if up else '↓'} {pv}★ → {cv}★"
                from_d, to_d = f"{pv}★", f"{cv}★"

            changes.append({
                "date": row["date"], "ticker": tk, "company": row["company"] or "",
                "kind": kind, "severity": "material", "driver": "rating",
                "label": ("FMV raised" if up else "FMV cut") if kind == "fmv"
                         else ("Star rating up" if up else "Star rating down"),
                "arrow": "↑" if up else "↓", "direction": "up" if up else "down",
                "from": pv, "to": cv, "from_display": from_d, "to_display": to_d,
                "pct": round(pct, 1) if pct is not None else None,
                "text": text, "confirmed_by_sheet": True,
            })
            added += 1

    return changes, added


# --------------------------------------------------------------------------
# main builder
# --------------------------------------------------------------------------

def build_week_deltas(
    as_of: date | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
    logs_dir: Path = LOGS_DIR,
    db_path: str = DB_PATH,
) -> dict:
    as_of = as_of or date.today()
    window_start = as_of - timedelta(days=days)
    ws, we = window_start.isoformat(), as_of.isoformat()

    all_days = [d for d in _snapshot_dates(logs_dir) if d <= we]
    in_window = [d for d in all_days if d > ws]
    before = [d for d in all_days if d <= ws]

    if not in_window:
        return {
            "as_of": we, "window_days": days, "window_start": ws,
            "has_changes": False, "changes": [], "counts": {},
            "summary": "No scored snapshots in the last "
                       f"{days} days — cannot compute changes.",
            "noise_summary": None, "degraded": True,
            "sources": {"snapshots": 0, "sheet_rows": 0, "sheet_backfilled": 0},
        }

    # Baseline: last snapshot at/before the window edge, so a change landing on
    # the first in-window day is still caught.
    walk = ([before[-1]] if before else []) + in_window
    changes: list[dict] = []

    prev_snap = _load_snapshot(logs_dir, walk[0])
    first_seen = set(prev_snap)
    for day in walk[1:]:
        cur_snap = _load_snapshot(logs_dir, day)
        for ticker, cur in cur_snap.items():
            prev = prev_snap.get(ticker)
            if prev is None:
                if ticker not in first_seen and cur.get("conviction") != "SKIP":
                    changes.append({
                        "date": day, "ticker": ticker,
                        "company": cur.get("company") or "",
                        "kind": "new_coverage", "severity": "material",
                        "driver": "rating", "label": "New coverage",
                        "arrow": "＋", "direction": "up",
                        "from": None, "to": cur.get("conviction"),
                        "from_display": "—", "to_display": cur.get("conviction"),
                        "pct": None, "confirmed_by_sheet": False,
                        "text": f"New coverage — {cur.get('conviction')}, "
                                f"{cur.get('stars')}★, FMV {_money(_num(cur.get('fmv')))}",
                    })
                first_seen.add(ticker)
                continue
            changes.extend(_diff_ticker(day, prev, cur))
        prev_snap = cur_snap

    sheet_rows = _sheet_revisions(ws, we, db_path)
    changes, backfilled = _merge_sheet_revisions(changes, sheet_rows)

    # keep only the window
    changes = [c for c in changes if ws < c["date"] <= we]

    for c in changes:
        n, label = _age_label(as_of, c["date"])
        c["age_days"] = n
        c["age_label"] = label

    listed = [c for c in changes if c["severity"] != "noise"]
    noise = [c for c in changes if c["severity"] == "noise"]

    # today first, then reverse-chronological; thesis-movers before price-movers;
    # inside a kind, biggest magnitude first so the largest revisions lead.
    listed.sort(key=lambda c: (
        c["age_days"],
        0 if c["severity"] == "material" else 1,
        _KIND_PRIORITY.get(c["kind"], 9),
        -abs(c.get("pct") or 0),
        c["ticker"],
    ))

    counts: dict[str, int] = {}
    for c in listed:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1

    today_n = sum(1 for c in listed if c["age_days"] <= 0)
    material_n = sum(1 for c in listed if c["severity"] == "material")

    bits = []
    order = ["fmv", "stars", "moat", "uncertainty", "conviction",
             "new_coverage", "rating_refresh", "band_cross"]
    names = {
        "fmv": "FMV revision", "stars": "star change", "moat": "moat change",
        "uncertainty": "uncertainty change", "conviction": "conviction move",
        "new_coverage": "new coverage", "rating_refresh": "analyst-note refresh",
        "band_cross": "FMV-band cross",
    }
    for k in order:
        n = counts.get(k, 0)
        if n:
            bits.append(f"{n} {names[k]}{'s' if n != 1 else ''}")

    if material_n:
        summary = (f"{len(listed)} change{'s' if len(listed) != 1 else ''} in the last "
                   f"{days} days ({material_n} material, {today_n} today): "
                   + ", ".join(bits) + ".")
    elif listed:
        # Nothing touched the thesis; only price moved things around.
        summary = (f"No FMV, star-rating, moat, or uncertainty changes in the last "
                   f"{days} days — ratings held flat all week. The "
                   f"{len(listed)} item{'s' if len(listed) != 1 else ''} below "
                   f"{'are' if len(listed) != 1 else 'is'} price-driven only.")
    else:
        summary = (f"No FMV, star-rating, moat, or conviction changes in the last "
                   f"{days} days — ratings have been stable all week.")

    noise_summary = None
    if noise:
        tks = sorted({c["ticker"] for c in noise})
        shown = ", ".join(tks[:8]) + (f" +{len(tks) - 8} more" if len(tks) > 8 else "")
        noise_summary = (
            f"{len(noise)} WATCH↔SKIP flip{'s' if len(noise) != 1 else ''} "
            f"({shown}) suppressed — price crossed a threshold, no rating change."
        )

    return {
        "as_of": we,
        "window_days": days,
        "window_start": ws,
        "has_changes": bool(listed),
        "summary": summary,
        "changes": listed,
        "counts": counts,
        "today_count": today_n,
        "material_count": material_n,
        "noise_summary": noise_summary,
        "degraded": False,
        "sources": {
            "snapshots": len(walk),
            "snapshot_days": walk,
            "sheet_rows": len(sheet_rows),
            "sheet_backfilled": backfilled,
        },
    }


# --------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------

_ARROW_COLOR = {"up": "#1a7f37", "down": "#cf222e", "flat": "#57606a"}


def group_changes(changes: list[dict]) -> list[dict]:
    """Collapse a flat change list into one entry per (day, ticker).

    `changes` is already sorted by the build step, so first-seen order inside a
    day is the ranking we want — a ticker inherits the rank of its strongest
    change, and its individual changes stay in that same order.
    """
    groups: list[dict] = []
    index: dict[tuple[int, str], dict] = {}
    for c in changes:
        key = (c["age_days"], c["ticker"])
        g = index.get(key)
        if g is None:
            g = {
                "age_days": c["age_days"],
                "age_label": c["age_label"],
                "date": c["date"],
                "ticker": c["ticker"],
                "company": c.get("company"),
                "items": [],
                "material": False,
                "confirmed_by_sheet": False,
            }
            index[key] = g
            groups.append(g)
        g["items"].append(c)
        # A ticker reads as material if any of its changes touched the thesis.
        g["material"] = g["material"] or c["severity"] == "material"
        g["confirmed_by_sheet"] = g["confirmed_by_sheet"] or bool(c.get("confirmed_by_sheet"))
    return groups


def render_html(wd: dict) -> str:
    """HTML block for the morning-brief email."""
    if not wd:
        return "<p style='color:#888;font-size:13px'>Change feed unavailable.</p>"

    summary = wd.get("summary", "")
    material_n = wd.get("material_count", 0)

    head = (f"<div style='font-size:12px;color:#57606a;margin:0 0 6px 0'>"
            f"Last {wd.get('window_days', 7)} days · since {wd.get('window_start', '')}"
            f"</div>")
    # Headline first — this is the answer to "did anything move?"
    head += (f"<p style='margin:0 0 10px 0;font-size:13px;color:#24292f'>"
             f"{'' if material_n else '<b>Nothing material moved.</b> '}{summary}</p>")

    if not wd.get("has_changes"):
        body = ""
        if wd.get("noise_summary"):
            body = (f"<p style='margin:0;font-size:11px;color:#8c959f'>"
                    f"{wd['noise_summary']}</p>")
        return head + body

    rows = ""
    last_age = None
    for g in group_changes(wd["changes"]):
        if g["age_days"] != last_age:
            last_age = g["age_days"]
            is_today = g["age_days"] <= 0
            band = "#dafbe1" if is_today else "#f6f8fa"
            fg = "#1a7f37" if is_today else "#57606a"
            tag = "TODAY" if is_today else g["age_label"].upper()
            rows += (
                f"<tr><td colspan='3' style='background:{band};color:{fg};"
                f"font-size:11px;font-weight:700;letter-spacing:.04em;"
                f"padding:5px 10px;border-top:1px solid #d0d7de'>"
                f"{tag} <span style='font-weight:400;color:#8c959f'>· {g['date']}</span>"
                f"</td></tr>"
            )

        # One row per ticker; every change it had stacks in the middle cell.
        lines = ""
        for c in g["items"]:
            color = _ARROW_COLOR.get(c.get("direction"), "#57606a")
            weight = "700" if c["severity"] == "material" else "400"
            dim = "" if c["severity"] == "material" else "opacity:.72;"
            lines += (f"<div style='color:{color};font-weight:{weight};{dim}"
                      f"padding:1px 0'>{c['text']}</div>")

        chk = (" <span style='color:#1a7f37' title='confirmed by Data_Changes'>✓</span>"
               if g["confirmed_by_sheet"] else "")
        rows += f"""
        <tr>
          <td style='padding:5px 10px;white-space:nowrap;font-weight:700;font-size:13px;vertical-align:top'>
            {g['ticker']}{chk}</td>
          <td style='padding:5px 10px;font-size:12px'>{lines}</td>
          <td style='padding:5px 10px;font-size:11px;color:#8c959f;white-space:nowrap;text-align:right;vertical-align:top'>
            ({g['age_label']})</td>
        </tr>"""

    table = (f"<table style='border-collapse:collapse;width:100%;font-family:sans-serif'>"
             f"{rows}</table>")

    foot = ""
    if wd.get("noise_summary"):
        foot = (f"<p style='margin:8px 0 0 0;font-size:11px;color:#8c959f'>"
                f"{wd['noise_summary']}</p>")

    return head + table + foot


def render_text(wd: dict) -> str:
    """Plain-text render for terminal verification."""
    lead = "" if wd.get("material_count") else "Nothing material moved. "
    out = [f"WHAT CHANGED — last {wd.get('window_days')} days "
           f"(since {wd.get('window_start')}, as of {wd.get('as_of')})",
           "=" * 78,
           lead + (wd.get("summary") or "")]
    if not wd.get("has_changes"):
        if wd.get("noise_summary"):
            out.append(f"  note: {wd['noise_summary']}")
        return "\n".join(out)

    last_age = None
    for g in group_changes(wd["changes"]):
        if g["age_days"] != last_age:
            last_age = g["age_days"]
            tag = "TODAY" if g["age_days"] <= 0 else g["age_label"].upper()
            out.append("")
            out.append(f"-- {tag}  ({g['date']}) " + "-" * (58 - len(tag)))
        chk = " ✓" if g["confirmed_by_sheet"] else ""
        for i, c in enumerate(g["items"]):
            txt = c["text"].replace("  <i>price-driven</i>", "  [price-driven]")
            if i == 0:
                out.append(f"  {g['ticker']:<7}{chk:<2} {txt}   ({g['age_label']})")
            else:
                # continuation lines hang under the ticker
                out.append(f"  {'':<7}{'':<2} {txt}")

    out.append("")
    if wd.get("noise_summary"):
        out.append(wd["noise_summary"])
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="7-day What Changed delta feed")
    ap.add_argument("--asof", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--render", action="store_true", help="print text instead of JSON")
    ap.add_argument("--html", action="store_true", help="print the HTML block")
    args = ap.parse_args()

    as_of = datetime.strptime(args.asof, "%Y-%m-%d").date() if args.asof else date.today()
    wd = build_week_deltas(as_of=as_of, days=args.days)

    if args.render:
        print(render_text(wd))
    elif args.html:
        print(render_html(wd))
    else:
        json.dump(wd, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
