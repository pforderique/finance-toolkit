"""Extract key Morningstar analyst data from saved PDFs using pdftotext.

Zero Claude tokens — pure Python extraction.

Usage:
  python -m trader_agent.tools.extract_pdf_data MSFT IQV NKE
  python -m trader_agent.tools.extract_pdf_data --all   # all tickers with PDFs

Output: JSON dict keyed by ticker.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ARTIFACTS_DIR = Path(__file__).parent.parent.parent / "ms_screener" / "artifacts"

_PDFTOTEXT = shutil.which("pdftotext") or "/opt/homebrew/bin/pdftotext"


def _pdf_to_text(pdf_path: Path) -> str:
    result = subprocess.run(
        [_PDFTOTEXT, "-layout", str(pdf_path), "-"],
        capture_output=True, text=True,
    )
    return result.stdout


def _find_analyst(text: str) -> str | None:
    # "Analyst Note Dan Romanoff, CPA, Senior Equity Analyst, 30 Apr 2026"
    m = re.search(r"Analyst Note ([A-Z][a-z]+(?: [A-Z][a-z]+)+(?:,? (?:CFA|CPA|MBA))?),", text)
    if m:
        return m.group(1).strip().rstrip(",")
    return None


def _find_fmv_date(text: str) -> str | None:
    # FMV date is on line after "Fair Value Estimate" header: "31 Jul 2025 08:51, UTC"
    m = re.search(r"Fair Value Estimate\s+Price/FVE.*?\n.*?(\d{1,2} [A-Za-z]+ \d{4})", text[:3000], re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: most recent analyst note date
    m = re.search(r"Analyst Note [^\n]+,\s*(\d{1,2} [A-Za-z]+ \d{4})", text)
    if m:
        return m.group(1).strip()
    return None


def _find_fair_value(text: str) -> str | None:
    m = re.search(r"Fair Value(?:\s+Estimate)?[:\s]+\$?([\d,]+(?:\.\d+)?)\s+USD", text[:2000])
    if m:
        return f"${m.group(1)}"
    m = re.search(r"Fair Value(?:\s+Estimate)?[:\s]+\$?([\d,]+(?:\.\d+)?)", text[:2000])
    if m:
        return f"${m.group(1)}"
    return None


def _find_moat_uncertainty(text: str) -> tuple[str | None, str | None]:
    # Header and values are on consecutive lines with whitespace alignment:
    # "...Economic MoatTM    ...    Uncertainty    ..."
    # "...Wide               ...    Medium         ..."
    lines = text.splitlines()
    for i, line in enumerate(lines[:200]):
        if "Economic MoatTM" in line and "Uncertainty" in line and i + 1 < len(lines):
            val_line = lines[i + 1]
            moat_m = re.search(r"\b(Wide|Narrow|None)\b", val_line)
            unc_m = re.search(r"\b(Low|Medium|High|Very High|Extreme)\b", val_line)
            moat = moat_m.group(1) if moat_m else None
            unc = unc_m.group(1) if unc_m else None
            return moat, unc
    return None, None


def _extract_bull_bear(text: str) -> tuple[str, str]:
    bull = bear = ""
    # Actual bulls section: "Bulls Say Dan Romanoff..." then "u " bullets
    bull_m = re.search(r"Bulls Say [^\n]+\n((?:[ \t]*u .+\n?){1,5})", text)
    if bull_m:
        lines = [l.strip().lstrip("u").strip() for l in bull_m.group(1).splitlines() if l.strip()]
        bull = " ".join(lines)[:250]
    bear_m = re.search(r"Bears Say [^\n]+\n((?:[ \t]*u .+\n?){1,5})", text)
    if bear_m:
        lines = [l.strip().lstrip("u").strip() for l in bear_m.group(1).splitlines() if l.strip()]
        bear = " ".join(lines)[:250]
    return bull, bear


def extract_ticker(ticker: str) -> dict | None:
    pdfs = sorted(ARTIFACTS_DIR.glob(f"{ticker}_*.pdf"), reverse=True)
    if not pdfs:
        return None

    pdf_path = pdfs[0]
    text = _pdf_to_text(pdf_path)
    if not text.strip():
        return None

    bull, bear = _extract_bull_bear(text)
    moat, uncertainty = _find_moat_uncertainty(text)
    return {
        "ticker": ticker,
        "pdf_file": pdf_path.name,
        "analyst": _find_analyst(text),
        "fmv_confirmed": _find_fmv_date(text),
        "fair_value": _find_fair_value(text),
        "moat": moat,
        "uncertainty": uncertainty,
        "bull": bull,
        "bear": bear,
    }


def main() -> None:
    args = sys.argv[1:]

    if "--all" in args:
        tickers = sorted({p.stem.split("_")[0] for p in ARTIFACTS_DIR.glob("*.pdf")})
    else:
        tickers = [t.upper() for t in args if not t.startswith("-")]

    if not tickers:
        print("Usage: extract_pdf_data TICKER [TICKER ...] | --all", file=sys.stderr)
        sys.exit(1)

    results = {}
    for ticker in tickers:
        data = extract_ticker(ticker)
        if data:
            results[ticker] = data
        else:
            results[ticker] = None

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
