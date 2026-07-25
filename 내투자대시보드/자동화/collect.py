#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""일별 공개 데이터 수집기 (자동화 파이프라인) — Naver 기반.

매일 실행되어 **공개 시장 데이터만** 모아 `데이터/`에 누적합니다. 개인 데이터는 다루지 않습니다.
표준 라이브러리만 사용합니다.

시세 출처는 **Naver 금융 API**(api.stock.naver.com)입니다. (Yahoo가 rate-limit(429)으로 막혀
Naver로 교체) 시장심리는 CNN Fear & Greed를 그대로 씁니다.

수집 항목
  · 환율        USD/KRW (Naver marketindex)              → 데이터/환율_일별.csv
  · 벤치마크    S&P500 / NASDAQ / 다우 (Naver index)      → 데이터/벤치마크_일별.csv
  · 종목 시세   tickers.txt (Naver stock)                 → 데이터/시세_일별.csv
  · 시장지표    미국채10년 · 국제금 · WTI (Naver)          → 데이터/시장지표_일별.csv
  · 시장심리    CNN Fear & Greed (실제 지수)              → 데이터/fear_greed_일별.csv

같은 날짜는 덮어써서 중복을 막습니다(upsert). 한 소스가 실패해도 나머지는 기록합니다.
"""
from __future__ import annotations

import csv
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NAVER = "https://api.stock.naver.com"
NAVER_HEADERS = {"Referer": "https://m.stock.naver.com/", "Accept": "application/json"}

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "데이터")
TICKERS = os.path.join(HERE, "tickers.txt")


def _ctx() -> ssl.SSLContext:
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=ca) if ca and os.path.exists(ca) else ssl.create_default_context()


def _get_json(url: str, headers=None, tries=3):
    ctx = _ctx()
    h = {"User-Agent": UA}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (i + 1))
    raise last


def naver(path: str):
    """Naver JSON GET. 실패하면 None."""
    try:
        return _get_json(NAVER + path, NAVER_HEADERS)
    except Exception:  # noqa: BLE001
        return None


def _price(v):
    """'1,463.10' → 1463.10 / None."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _date_of(iso, fallback=None):
    """'2026-07-24T17:05:00-04:00' → '2026-07-24'. 없으면 fallback(오늘 UTC)."""
    if isinstance(iso, str) and len(iso) >= 10 and iso[4] == "-":
        return iso[:10]
    return fallback or datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fx_usdkrw():
    d = naver("/marketindex/exchange/FX_USDKRW")
    ci = (d or {}).get("exchangeInfo") or d or {}
    px = _price(ci.get("closePrice"))
    if px is None:
        return None
    return _date_of(ci.get("localTradedAt")), px


def index_close(code: str):
    d = naver(f"/index/{code}/basic")
    if not isinstance(d, dict):
        return None
    px = _price(d.get("closePrice"))
    if px is None:
        return None
    return _date_of(d.get("localTradedAt")), px


def stock_close(ticker: str):
    """Naver 종목 코드 접미사(.O/무접미/.N/.K)를 차례로 시도."""
    for code in (f"{ticker}.O", ticker, f"{ticker}.N", f"{ticker}.K"):
        d = naver(f"/stock/{code}/basic")
        if isinstance(d, dict):
            px = _price(d.get("closePrice"))
            if px is not None:
                return _date_of(d.get("localTradedAt")), px
    return None


def marketindex_close(path: str):
    d = naver(path)
    if not isinstance(d, dict):
        return None
    px = _price(d.get("closePrice"))
    if px is None:
        return None
    return _date_of(d.get("localTradedAt")), px


def cnn_fear_greed():
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    try:
        d = _get_json(url, {"Referer": "https://edition.cnn.com/", "Accept": "*/*"})
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


def upsert_csv(path: str, header: list[str], key_idx, row: list):
    """key_idx 열(날짜/날짜+티커)이 같으면 덮어쓰고, 없으면 추가."""
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
    print("== 일별 수집 시작 (Naver) ==")
    # 환율
    fx = fx_usdkrw()
    if fx:
        upsert_csv(os.path.join(DATA_DIR, "환율_일별.csv"), ["date", "usdkrw"], 0, [fx[0], fx[1]])
        print(f"  환율 {fx[0]} USD/KRW={fx[1]}")
    else:
        print("  환율 실패", file=sys.stderr)
    # 벤치마크
    bm = {}
    for name, code in [("sp", ".INX"), ("nasdaq", ".IXIC"), ("dow", ".DJI")]:
        r = index_close(code)
        if r:
            bm[name] = r
    if bm:
        d = (bm.get("sp") or next(iter(bm.values())))[0]
        upsert_csv(os.path.join(DATA_DIR, "벤치마크_일별.csv"), ["date", "sp", "nasdaq", "dow"], 0,
                   [d, bm.get("sp", ("", ""))[1], bm.get("nasdaq", ("", ""))[1], bm.get("dow", ("", ""))[1]])
        print(f"  벤치마크 {d} sp={bm.get('sp')} nasdaq={bm.get('nasdaq')} dow={bm.get('dow')}")
    # 종목 시세
    for tk in read_tickers():
        r = stock_close(tk)
        if r:
            upsert_csv(os.path.join(DATA_DIR, "시세_일별.csv"), ["date", "ticker", "close"], [0, 1], [r[0], tk, r[1]])
            print(f"  시세 {tk} {r[0]}={r[1]}")
        else:
            print(f"  시세 {tk} 실패(코드 확인)", file=sys.stderr)
        time.sleep(0.3)  # 예의상 간격
    # 시장지표 (미국채10년·국제금·WTI)
    mk = {}
    for name, path in [("us10y", "/marketindex/bond/US10YT=RR"),
                       ("gold", "/marketindex/metals/GCcv1"),
                       ("wti", "/marketindex/energy/CLcv1")]:
        r = marketindex_close(path)
        if r:
            mk[name] = r
        time.sleep(0.3)
    if mk:
        d = next(iter(mk.values()))[0]
        upsert_csv(os.path.join(DATA_DIR, "시장지표_일별.csv"), ["date", "us10y", "gold", "wti"], 0,
                   [d, mk.get("us10y", ("", ""))[1], mk.get("gold", ("", ""))[1], mk.get("wti", ("", ""))[1]])
        print(f"  시장지표 {d} {mk}")
    # Fear & Greed (실제 CNN 지수)
    fg = cnn_fear_greed()
    if fg:
        upsert_csv(os.path.join(DATA_DIR, "fear_greed_일별.csv"), ["date", "score", "rating"], 0, list(fg))
        print(f"  Fear&Greed {fg[0]} score={fg[1]} ({fg[2]})")
    print("== 완료 ==")


if __name__ == "__main__":
    main()
