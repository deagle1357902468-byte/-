#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""일별 공개 데이터 수집기 (자동화 파이프라인).

GitHub Actions가 매 거래일 실행합니다. 개인 데이터는 다루지 않고, **공개 시장 데이터만**
모아서 이 폴더의 `데이터/`에 누적합니다. 표준 라이브러리만 사용합니다.

수집 항목
  · 환율        USD/KRW (Yahoo `KRW=X`)                 → 데이터/환율_일별.csv
  · 벤치마크    S&P500 / NASDAQ / 다우 (Yahoo)          → 데이터/벤치마크_일별.csv
  · 종목 시세   tickers.txt의 티커들 (Yahoo)            → 데이터/시세_일별.csv
  · 시장심리    CNN Fear & Greed (실패 시 건너뜀)       → 데이터/fear_greed_일별.csv

같은 날짜는 덮어써서 중복을 막습니다(upsert). 한 소스가 실패해도 나머지는 기록합니다.
"""
from __future__ import annotations

import csv
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
YAHOO_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "데이터")
TICKERS = os.path.join(HERE, "tickers.txt")


def _ctx() -> ssl.SSLContext:
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=ca) if ca and os.path.exists(ca) else ssl.create_default_context()


def _get(url: str, headers=None, tries=3):
    ctx = _ctx()
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (i + 1))
    raise last


def yahoo_daily_close(symbol: str) -> tuple[str, float] | None:
    """(YYYY-MM-DD, close) — 가장 최근 거래일 종가."""
    for i in range(3):
        host = YAHOO_HOSTS[i % len(YAHOO_HOSTS)]
        url = (f"https://{host}/v8/finance/chart/{urllib.parse.quote(symbol)}"
               "?interval=1d&range=5d")
        try:
            res = _get(url)["chart"]["result"][0]
            ts = res["timestamp"]
            closes = res["indicators"]["quote"][0]["close"]
            for t, c in zip(reversed(ts), reversed(closes)):
                if c is not None:
                    d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
                    return d, round(float(c), 4)
        except Exception as exc:  # noqa: BLE001
            print(f"  yahoo {symbol} 실패({i}): {exc}", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None


def cnn_fear_greed() -> tuple[str, float, str] | None:
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    try:
        d = _get(url, headers={"Referer": "https://edition.cnn.com/", "Accept": "*/*"})
        fg = d["fear_and_greed"]
        return datetime.now(timezone.utc).strftime("%Y-%m-%d"), round(float(fg["score"]), 1), str(fg.get("rating", ""))
    except Exception as exc:  # noqa: BLE001
        print(f"  CNN F&G 실패(건너뜀): {exc}", file=sys.stderr)
        return None


def read_tickers() -> list[str]:
    out = []
    if os.path.exists(TICKERS):
        for line in open(TICKERS, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line.split()[0])
    return out


def upsert_csv(path: str, header: list[str], key_idx: int, row: list):
    """key_idx 열(보통 날짜/날짜+티커)이 같으면 덮어쓰고, 없으면 추가."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows, found = [], False
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            r = list(csv.reader(f))
        if r:
            rows = r[1:]
    def keyof(rr):
        return tuple(rr[i] for i in (key_idx if isinstance(key_idx, list) else [key_idx]))
    k = keyof([str(x) for x in row])
    for i, rr in enumerate(rows):
        if keyof(rr) == k:
            rows[i] = [str(x) for x in row]
            found = True
            break
    if not found:
        rows.append([str(x) for x in row])
    rows.sort()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    print("== 일별 수집 시작 ==")
    # 환율
    fx = yahoo_daily_close("KRW=X")
    if fx:
        upsert_csv(os.path.join(DATA_DIR, "환율_일별.csv"), ["date", "usdkrw"], 0, [fx[0], fx[1]])
        print(f"  환율 {fx[0]} USD/KRW={fx[1]}")
    # 벤치마크
    bm = {}
    for name, sym in [("sp", "^GSPC"), ("nasdaq", "^IXIC"), ("dow", "^DJI")]:
        r = yahoo_daily_close(sym)
        if r:
            bm[name] = r
    if bm:
        # 세 지수 날짜가 같다고 가정(같은 거래일). 대표 날짜는 sp 기준.
        d = (bm.get("sp") or next(iter(bm.values())))[0]
        upsert_csv(os.path.join(DATA_DIR, "벤치마크_일별.csv"), ["date", "sp", "nasdaq", "dow"], 0,
                   [d, bm.get("sp", ("", ""))[1], bm.get("nasdaq", ("", ""))[1], bm.get("dow", ("", ""))[1]])
        print(f"  벤치마크 {d} sp={bm.get('sp')}")
    # 종목 시세
    for tk in read_tickers():
        r = yahoo_daily_close(tk)
        if r:
            upsert_csv(os.path.join(DATA_DIR, "시세_일별.csv"), ["date", "ticker", "close"], [0, 1], [r[0], tk, r[1]])
            print(f"  시세 {tk} {r[0]}={r[1]}")
        time.sleep(0.4)  # 예의상 간격
    # 시장지표 (미국채10년·금·WTI)
    mk = {}
    for name, sym in [("us10y", "^TNX"), ("gold", "GC=F"), ("wti", "CL=F")]:
        r = yahoo_daily_close(sym)
        if r:
            mk[name] = r
        time.sleep(0.3)
    if mk:
        d = next(iter(mk.values()))[0]
        upsert_csv(os.path.join(DATA_DIR, "시장지표_일별.csv"), ["date", "us10y", "gold", "wti"], 0,
                   [d, mk.get("us10y", ("", ""))[1], mk.get("gold", ("", ""))[1], mk.get("wti", ("", ""))[1]])
        print(f"  시장지표 {d} {mk}")
    # Fear & Greed
    fg = cnn_fear_greed()
    if fg:
        upsert_csv(os.path.join(DATA_DIR, "fear_greed_일별.csv"), ["date", "score", "rating"], 0, list(fg))
        print(f"  Fear&Greed {fg[0]} score={fg[1]} ({fg[2]})")
    print("== 완료 ==")


if __name__ == "__main__":
    main()
