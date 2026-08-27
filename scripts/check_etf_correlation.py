#!/usr/bin/env python3
"""Check today's KRX disclosures for NH-Amundi (HANARO) ETF correlation breaches.

한국거래소는 ETF의 기초지수와 순자산가치(NAV) 간 **상관계수가 0.9에 미달**하면
"ETF 상관계수 미달 발생" 공시를, 회복되면 "해소" 공시를 냅니다. 이 스크립트는
**작업 당일자 공시만** 확인합니다 (과거 데이터는 조회하지 않음).

Data source
-----------
KIND(kind.krx.co.kr)는 이 실행 환경에서 접근이 차단되어 있어, 동일한 거래소/코스콤
공시 피드를 그대로 중계하는 **네이버 증권 종목 공시**를 사용합니다.
  1) https://finance.naver.com/api/sise/etfItemList.nhn  -> 전체 ETF 목록에서
     종목명이 ``HANARO`` 로 시작하는 엔에이치아문디자산운용 ETF를 추립니다.
  2) 종목별 공시 목록에서 **당일 날짜** 행만 골라 상세 페이지에서 전체 제목을 읽습니다.
목록 제목은 잘려서 표시되므로 당일 행에 한해 상세 페이지를 열어 전체 제목을 확인합니다.

Outputs
-------
  data/etf_correlation_checks.csv  - 실행마다 append (위반 없으면 status=none 한 줄)
  data/etf_correlation_latest.json - 가장 최근 실행 결과

Exit code는 항상 0(정상 실행) / 1(데이터 수집 실패)입니다. 위반이 있어도 0입니다.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
CSV_PATH = os.path.join(DATA_DIR, "etf_correlation_checks.csv")
LATEST_PATH = os.path.join(DATA_DIR, "etf_correlation_latest.json")

ETF_LIST_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
NOTICE_LIST_URL = "https://finance.naver.com/item/news_notice.naver?code={code}&page={page}"
NOTICE_BASE = "https://finance.naver.com"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 엔에이치아문디자산운용 ETF 브랜드. 다른 운용사는 이 접두어를 쓰지 않습니다.
BRAND_PREFIX = "HANARO"
MANAGER = "엔에이치아문디자산운용"

# 상관계수 위반/해소 공시 판별 키워드
CORRELATION_KW = "상관계수"
RESOLVED_KW = ("해소", "해제", "종료")
# 함께 보고하면 유용한 인접 유형 (위반은 아니지만 지수 추종 품질 이슈)
RELATED_KW = ("괴리율", "추적오차", "자산구성내역 오류", "지수 이용")

CSV_HEADER = [
    "checked_kst",      # 실행 시각 (KST)
    "check_date",       # 확인 대상 영업일 (YYYY-MM-DD)
    "status",           # violation / resolved / related / none / error
    "ticker",
    "etf_name",
    "title",            # 공시 전체 제목
    "disclosure_date",
    "url",
    "manager",
]


def _ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if cafile and os.path.exists(cafile):
        return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


_CTX = _ssl_context()


def fetch(url: str, retries: int = 3) -> str:
    """GET a Naver Finance page. Pages are EUC-KR encoded."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": "https://finance.naver.com/"}
            )
            with urllib.request.urlopen(req, timeout=30, context=_CTX) as resp:
                return resp.read().decode("euc-kr", "replace")
        except Exception as exc:  # noqa: BLE001 - retry any transient failure
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url}: {last}")


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def list_hanaro_etfs() -> list[tuple[str, str]]:
    """Return [(ticker, name)] for every listed NH-Amundi (HANARO) ETF."""
    data = json.loads(fetch(ETF_LIST_URL))
    items = data["result"]["etfItemList"]
    etfs = [
        (it["itemcode"], it["itemname"])
        for it in items
        if it.get("itemname", "").startswith(BRAND_PREFIX)
    ]
    return sorted(etfs)


def todays_notice_links(code: str, date_dotted: str, max_pages: int = 5) -> list[str]:
    """Links to disclosures filed on `date_dotted` (YYYY.MM.DD) for one ticker.

    한 종목이 하루에 10건(목록 한 페이지 분량)을 넘게 공시하는 경우를 대비해,
    페이지가 전부 당일 공시로 채워졌을 때만 다음 페이지를 이어서 읽습니다.
    """
    found: list[str] = []
    for page in range(1, max_pages + 1):
        html = fetch(NOTICE_LIST_URL.format(code=code, page=page))
        rows = re.findall(
            r'href="(/item/news_notice_read\.naver\?no=\d+&code=\d+&page_notice=\d+)"'
            r'.*?<td class="date">\s*([\d.]+)\s*</td>',
            html,
            re.S,
        )
        if not rows:
            break
        today = [link.replace("&amp;", "&") for link, date in rows if date.strip() == date_dotted]
        found += today
        if len(today) < len(rows):
            break  # 당일이 아닌 공시가 섞였다 = 당일분은 여기서 끝
    return found


def notice_title(link: str) -> str:
    """Full (untruncated) disclosure title from the detail page."""
    html = fetch(NOTICE_BASE + link)
    m = re.search(r'<strong class="c p15">(.*?)</strong>', html, re.S)
    return strip_tags(m.group(1)) if m else ""


def classify(title: str) -> str | None:
    if CORRELATION_KW in title:
        return "resolved" if any(k in title for k in RESOLVED_KW) else "violation"
    if any(k in title for k in RELATED_KW):
        return "related"
    return None


def check(date_kst: datetime, workers: int = 8) -> dict:
    date_iso = date_kst.strftime("%Y-%m-%d")
    date_dotted = date_kst.strftime("%Y.%m.%d")
    etfs = list_hanaro_etfs()
    if not etfs:
        raise RuntimeError("HANARO ETF 목록을 가져오지 못했습니다.")
    print(f"[{date_iso}] {MANAGER} ETF {len(etfs)}종목 당일 공시 확인 중...")

    with ThreadPoolExecutor(workers) as ex:
        per_ticker = list(
            ex.map(lambda e: (e, todays_notice_links(e[0], date_dotted)), etfs)
        )
        pending = [((code, name), link) for (code, name), links in per_ticker for link in links]
        titles = list(ex.map(lambda p: notice_title(p[1]), pending))

    findings: list[dict] = []
    for ((code, name), link), title in zip(pending, titles):
        kind = classify(title)
        if kind is None:
            continue  # 분배락/LP 변경 등 상관계수와 무관한 공시는 제외
        findings.append(
            {
                "status": kind,
                "ticker": code,
                "etf_name": name,
                "title": title,
                "disclosure_date": date_iso,
                "url": NOTICE_BASE + link,
            }
        )

    findings.sort(key=lambda f: ({"violation": 0, "resolved": 1, "related": 2}[f["status"]], f["ticker"]))
    return {
        "checked_kst": datetime.now(timezone.utc).astimezone(KST).isoformat(timespec="seconds"),
        "check_date": date_iso,
        "manager": MANAGER,
        "etf_count": len(etfs),
        "disclosures_today": len(pending),
        "findings": findings,
    }


def report(result: dict) -> None:
    violations = [f for f in result["findings"] if f["status"] == "violation"]
    resolved = [f for f in result["findings"] if f["status"] == "resolved"]
    related = [f for f in result["findings"] if f["status"] == "related"]

    print(f"\n당일 공시 총 {result['disclosures_today']}건 (전체 유형 기준)")
    if violations:
        print(f"\n🚨 상관계수 미달(위반) 공시 {len(violations)}건")
        for f in violations:
            print(f"  - [{f['ticker']}] {f['etf_name']}\n      {f['title']}\n      {f['url']}")
    else:
        print("\n✅ 상관계수 미달(위반) 공시 없음")
    if resolved:
        print(f"\n☑️  상관계수 미달 해소 공시 {len(resolved)}건")
        for f in resolved:
            print(f"  - [{f['ticker']}] {f['etf_name']} : {f['title']}")
    if related:
        print(f"\nℹ️  참고(괴리율/추적오차 등) {len(related)}건")
        for f in related:
            print(f"  - [{f['ticker']}] {f['etf_name']} : {f['title']}")


def persist(result: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    rows = []
    base = {
        "checked_kst": result["checked_kst"],
        "check_date": result["check_date"],
        "manager": result["manager"],
    }
    if result["findings"]:
        for f in result["findings"]:
            rows.append({**base, **{k: f.get(k, "") for k in
                                    ("status", "ticker", "etf_name", "title",
                                     "disclosure_date", "url")}})
    else:
        # 위반이 없었다는 사실도 기록으로 남깁니다.
        rows.append({**base, "status": "none", "ticker": "", "etf_name": "",
                     "title": "상관계수 관련 공시 없음", "disclosure_date": result["check_date"],
                     "url": ""})

    write_header = not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_HEADER)
        if write_header:
            w.writeheader()
        w.writerows(rows)

    with open(LATEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"\n{len(rows)}행 기록 -> {os.path.relpath(CSV_PATH, REPO_ROOT)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="확인할 날짜 (YYYY-MM-DD, 기본: 오늘 KST)")
    ap.add_argument("--workers", type=int, default=8, help="동시 요청 수 (기본 8)")
    ap.add_argument("--no-write", action="store_true", help="파일에 기록하지 않고 출력만")
    args = ap.parse_args()

    if args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=KST)
    else:
        day = datetime.now(timezone.utc).astimezone(KST)

    try:
        result = check(day, workers=args.workers)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: 공시 확인 실패: {exc}", file=sys.stderr)
        return 1

    report(result)
    if not args.no_write:
        persist(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
