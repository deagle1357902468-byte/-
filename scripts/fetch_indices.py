#!/usr/bin/env python3
"""Record the latest US index closes: Nasdaq Composite, S&P 500, Dow Jones.

Primary source : CNBC quote web service (one request for all three indices,
                 includes market status and previous close).
Fallback source: Yahoo Finance chart API (per-symbol) if CNBC is unavailable.

Neither source needs an API key. The script uses only the Python standard
library, so it runs anywhere with network access and no install step.

Every recorded row carries ``market_time_et`` (the exchange-local timestamp of
the price) and ``market_status`` so you can always tell whether a value is a
finalized close or an intraday snapshot.

Outputs:
  data/index_closes.csv  - one appended row per index, per run (full history)
  data/latest.json       - the most recent run only (handy for dashboards)
"""
from __future__ import annotations

import csv
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# (CNBC symbol, human-readable label, Yahoo fallback symbol)
INDICES = [
    (".IXIC", "Nasdaq Composite", "^IXIC"),
    (".SPX", "S&P 500", "^GSPC"),
    (".DJI", "Dow Jones Industrial Average", "^DJI"),
]

YAHOO_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
CSV_PATH = os.path.join(DATA_DIR, "index_closes.csv")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")

CSV_HEADER = [
    "captured_kst",
    "index",
    "symbol",
    "close",
    "previous_close",
    "change",
    "change_pct",
    "open",
    "day_high",
    "day_low",
    "market_time_et",
    "market_status",
    "currency",
    "source",
]

KST = timezone(timedelta(hours=9))


def _ssl_context() -> ssl.SSLContext:
    """Honor an explicit CA bundle if the environment provides one."""
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if cafile and os.path.exists(cafile):
        return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


def _num(val) -> float | None:
    """Parse CNBC's comma/percent-formatted numeric strings into floats."""
    if val is None:
        return None
    s = str(val).strip().replace(",", "").replace("%", "")
    if s in ("", "UNCH", "N/A", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _get(url: str, ctx: ssl.SSLContext) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_cnbc() -> dict:
    """Return {cnbc_symbol: record} for all indices in a single request."""
    ctx = _ssl_context()
    symbols = "%7C".join(cnbc for cnbc, _, _ in INDICES)  # %7C == '|'
    url = (
        "https://quote.cnbc.com/quote-html-webservice/quote.htm?"
        f"symbols={symbols}&requestMethod=itv&noform=1&fund=1&exthrs=1&output=json"
    )
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            payload = _get(url, ctx)
            quotes = payload["ITVQuoteResult"]["ITVQuote"]
            if isinstance(quotes, dict):
                quotes = [quotes]
            out = {}
            for q in quotes:
                out[q.get("symbol")] = {
                    "close": _num(q.get("last")),
                    "previous_close": _num(q.get("previous_day_closing")),
                    "change": _num(q.get("change")),
                    "change_pct": _num(q.get("change_pct")),
                    "open": _num(q.get("open")),
                    "day_high": _num(q.get("high")),
                    "day_low": _num(q.get("low")),
                    "market_time_et": (q.get("last_timedate") or "").strip(),
                    "market_status": (q.get("curmktstatus") or "").strip(),
                    "currency": (q.get("currencyCode") or "USD").strip(),
                    "source": "cnbc",
                }
            return out
        except Exception as exc:  # noqa: BLE001 - retry on any transient error
            last_err = exc
            time.sleep(3 * (attempt + 1))
    print(f"CNBC fetch failed: {last_err}", file=sys.stderr)
    return {}


def fetch_yahoo(symbol: str) -> dict | None:
    """Fallback: fetch a single index from Yahoo Finance."""
    ctx = _ssl_context()
    last_err: Exception | None = None
    for attempt in range(3):
        host = YAHOO_HOSTS[attempt % len(YAHOO_HOSTS)]
        url = (
            f"https://{host}/v8/finance/chart/"
            f"{urllib.parse.quote(symbol)}?interval=1d&range=1d"
        )
        try:
            meta = _get(url, ctx)["chart"]["result"][0]["meta"]
            ts = meta.get("regularMarketTime")
            gmtoffset = meta.get("gmtoffset", 0)
            market_time_et = ""
            if ts:
                et = datetime.fromtimestamp(ts, tz=timezone(timedelta(seconds=gmtoffset)))
                market_time_et = et.isoformat()
            close = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose")
            change = round(close - prev, 2) if close is not None and prev else None
            change_pct = (
                round((close - prev) / prev * 100, 2)
                if close is not None and prev
                else None
            )
            return {
                "close": close,
                "previous_close": prev,
                "change": change,
                "change_pct": change_pct,
                "open": None,
                "day_high": meta.get("regularMarketDayHigh"),
                "day_low": meta.get("regularMarketDayLow"),
                "market_time_et": market_time_et,
                "market_status": "",
                "currency": meta.get("currency", "USD"),
                "source": "yahoo",
            }
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(5 * (attempt + 1))
    print(f"Yahoo fetch failed for {symbol}: {last_err}", file=sys.stderr)
    return None


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    captured_kst = datetime.now(timezone.utc).astimezone(KST).isoformat(timespec="seconds")

    cnbc = fetch_cnbc()
    rows: list[dict] = []
    latest = {"captured_kst": captured_kst, "indices": []}
    errors: list[str] = []

    for cnbc_sym, label, yh_sym in INDICES:
        rec = cnbc.get(cnbc_sym)
        if rec is None or rec.get("close") is None:
            rec = fetch_yahoo(yh_sym)  # fall back to Yahoo for this index
        if rec is None or rec.get("close") is None:
            errors.append(label)
            print(f"ERROR: no data available for {label}", file=sys.stderr)
            continue

        row = {
            "captured_kst": captured_kst,
            "index": label,
            "symbol": cnbc_sym,
            "close": rec.get("close"),
            "previous_close": rec.get("previous_close"),
            "change": rec.get("change"),
            "change_pct": rec.get("change_pct"),
            "open": rec.get("open"),
            "day_high": rec.get("day_high"),
            "day_low": rec.get("day_low"),
            "market_time_et": rec.get("market_time_et"),
            "market_status": rec.get("market_status"),
            "currency": rec.get("currency"),
            "source": rec.get("source"),
        }
        rows.append(row)
        latest["indices"].append(row)

        chg, pct = row["change"], row["change_pct"]
        delta = f"{chg:+.2f} / {pct:+.2f}%" if chg is not None and pct is not None else "change n/a"
        print(
            f"{label:32s} {str(row['close']):>12}  ({delta})  "
            f"{row['market_time_et']}  [{row['source']}]"
        )

    if not rows:
        print("No data fetched; leaving files unchanged.", file=sys.stderr)
        return 1

    write_header = not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)

    with open(LATEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(latest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"\nWrote {len(rows)} row(s) to {os.path.relpath(CSV_PATH, REPO_ROOT)}")
    if errors:
        # Partial success is still success: record what we could, flag the rest.
        print(f"Completed with missing data for: {', '.join(errors)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
