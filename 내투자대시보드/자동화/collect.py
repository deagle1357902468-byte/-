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


def fed_policy_rate(years_back: int = 4):
    """미국 기준금리 — 뉴욕 연준 공식 API.

    유일하게 **히스토리를 쌓는** 지표입니다(나머지 시장지표는 당일 값만 씁니다).
    반환: (최신 dict, 월별 히스토리 list) — 각 항목 {date, target_from, target_to, effr}
    """
    today = datetime.now(timezone.utc).date()
    start = today.replace(year=today.year - years_back)
    url = ("https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json"
           f"?startDate={start}&endDate={today}&type=rate")
    try:
        d = _get_json(url)
        rows = d.get("refRates") or []
    except Exception as exc:  # noqa: BLE001
        print(f"  기준금리 실패(건너뜀): {exc}", file=sys.stderr)
        return None, []
    recs = []
    for r in rows:
        dt = r.get("effectiveDate")
        if not dt:
            continue
        recs.append({
            "date": dt,
            "target_from": _price(r.get("targetRateFrom")),
            "target_to": _price(r.get("targetRateTo")),
            "effr": _price(r.get("percentRate")),
        })
    recs.sort(key=lambda x: x["date"])
    if not recs:
        return None, []
    # 히스토리는 월말값으로 솎아냄(차트용, 가벼움)
    by_month = {}
    for r in recs:
        by_month[r["date"][:7]] = r
    hist = [by_month[k] for k in sorted(by_month)]
    return recs[-1], hist


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
    snap = {"asOf": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    # 환율
    fx = fx_usdkrw()
    if fx:
        upsert_csv(os.path.join(DATA_DIR, "환율_일별.csv"), ["date", "usdkrw"], 0, [fx[0], fx[1]])
        snap["fx"] = {"date": fx[0], "usdkrw": fx[1]}
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
        snap["index"] = {"date": d, **{k: v[1] for k, v in bm.items()}}
        print(f"  벤치마크 {d} sp={bm.get('sp')} nasdaq={bm.get('nasdaq')} dow={bm.get('dow')}")
    # 종목 시세
    quotes = {}
    for tk in read_tickers():
        r = stock_close(tk)
        if r:
            upsert_csv(os.path.join(DATA_DIR, "시세_일별.csv"), ["date", "ticker", "close"], [0, 1], [r[0], tk, r[1]])
            quotes[tk] = {"date": r[0], "close": r[1]}
            print(f"  시세 {tk} {r[0]}={r[1]}")
        else:
            print(f"  시세 {tk} 실패(코드 확인)", file=sys.stderr)
        time.sleep(0.3)  # 예의상 간격
    if quotes:
        snap["quotes"] = quotes
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
        snap["indicators"] = {"date": d, **{k: v[1] for k, v in mk.items()}}
        print(f"  시장지표 {d} {mk}")
    # 미국 기준금리 (유일하게 히스토리를 쌓는 지표)
    latest, hist = fed_policy_rate()
    if latest:
        for h in hist:
            upsert_csv(os.path.join(DATA_DIR, "기준금리_월별.csv"),
                       ["date", "target_from", "target_to", "effr"], 0,
                       [h["date"], h["target_from"], h["target_to"], h["effr"]])
        snap["policyRate"] = {"current": latest, "history": hist}
        print(f"  기준금리 {latest['date']} 목표 {latest['target_from']}~{latest['target_to']}% · EFFR {latest['effr']}% (히스토리 {len(hist)}개월)")
    # Fear & Greed (실제 CNN 지수)
    fg = cnn_fear_greed()
    if fg:
        upsert_csv(os.path.join(DATA_DIR, "fear_greed_일별.csv"), ["date", "score", "rating"], 0, list(fg))
        snap["fg"] = {"date": fg[0], "score": fg[1], "rating": fg[2]}
        print(f"  Fear&Greed {fg[0]} score={fg[1]} ({fg[2]})")
    # 당일 스냅샷 저장 — build_dashboard.py 가 이 값을 HTML에 심습니다
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "시장스냅샷.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print(f"  스냅샷 저장: 데이터/시장스냅샷.json (asOf {snap['asOf']})")
    print("== 완료 ==")


if __name__ == "__main__":
    main()
