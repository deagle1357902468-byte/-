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
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
CSV_PATH = os.path.join(DATA_DIR, "etf_correlation_checks.csv")
LATEST_PATH = os.path.join(DATA_DIR, "etf_correlation_latest.json")

KIND_ETF_URL = "https://kind.krx.co.kr/disclosure/disclosurebystocktype.do"
KIND_VIEWER = "https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={acpt}&docno=&viewerhost=&viewerport="

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

# 상관계수 기준은 상품 유형에 따라 다릅니다.
#   패시브(지수추종) ETF : 0.9 미만이면 미달
#   액티브 ETF          : 0.7 미만이면 미달
# 액티브 ETF는 거래소 규정상 종목명에 '액티브'를 반드시 표기하므로 이름으로 구분합니다.
ACTIVE_KW = "액티브"
THRESHOLD = {"passive": 0.9, "active": 0.7}

# 관리종목 지정 요건: 상관계수 미달이 **3개월(달력 기준)** 지속되면 해당.
# 영업일이 아니라 달력일로 세므로, 위반 시작일에 3개월을 더한 날짜를 기한으로 씁니다.
WATCH_MONTHS = 3

CSV_HEADER = [
    "checked_kst",      # 실행 시각 (KST)
    "check_date",       # 확인 대상 영업일 (YYYY-MM-DD)
    "status",           # violation / resolved / related / none / error
    "ticker",
    "etf_name",
    "title",            # 공시 전체 제목
    "disclosure_date",
    "url",
    "etf_type",         # passive(기준 0.9) / active(기준 0.7)
    "threshold",        # 해당 유형의 상관계수 기준값
    "calendar_days",    # 위반 시작일부터 경과한 달력일 (3개월 요건 기준)
    "deadline_3m",      # 3개월(달력) 도달일
    "days_to_3m",       # 도달까지 남은 달력일 (음수면 이미 경과)
    "streak_days",      # 위반이 연속된 영업일 수 (오늘 포함), 위반이 아니면 빈 값
    "streak_since",     # 그 연속 구간이 시작된 날짜
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


def parse_kind_rows(html: str) -> list[dict]:
    """KIND 'ETF 공시' 목록 HTML에서 행을 뽑습니다.

    컬럼: 번호 | 시간(YYYY-MM-DD HH:MM) | 종목명 | 공시제목 | 제출인(운용사)
    공시 원문은 ``openDisclsViewer('접수번호','')`` 의 접수번호로 링크합니다.
    """
    rows: list[dict] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 5:
            continue
        when = strip_tags(tds[1])
        if not re.match(r"\d{4}-\d{2}-\d{2}", when):
            continue
        acpt = re.search(r"openDisclsViewer\('(\d+)'", tr)
        rows.append({
            "datetime": when,
            "date": when[:10],
            "etf_name": strip_tags(tds[2]),
            "title": strip_tags(tds[3]),
            "submitter": strip_tags(tds[4]),
            "url": KIND_VIEWER.format(acpt=acpt.group(1)) if acpt else "",
        })
    return rows


def kind_request(from_date: str, to_date: str, page: int, report_nm: str = "") -> str:
    """KIND 'ETF 공시' 목록 한 페이지를 받아옵니다 (날짜 범위 지정 가능)."""
    body = urllib.parse.urlencode({
        "method": "searchDisclosureByStockTypeEtfSub",
        "forward": "disclosurebystocktype_etf_sub",
        "currentPageSize": "100",
        "pageIndex": str(page),
        "orderMode": "1",
        "orderStat": "D",
        "etfIsuSrtCd": "",
        "reportCd": "",
        "reportTmp": "",
        "etfIsuSrtNm": "",
        "reportNm": report_nm,
        "fromDate": from_date,
        "toDate": to_date,
    }).encode()
    req = urllib.request.Request(
        KIND_ETF_URL,
        data=body,
        headers={
            "User-Agent": UA,
            "Referer": KIND_ETF_URL + "?method=searchDisclosureByStockTypeEtf",
            "Origin": "https://kind.krx.co.kr",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
            "Accept-Language": "ko,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    with urllib.request.urlopen(req, timeout=30, context=_CTX) as resp:
        return resp.read().decode("utf-8", "replace")


def kind_violation_dates(etf_name: str, before: str, max_pages: int = 10) -> list[str]:
    """KIND에서 이 ETF의 지난 상관계수 미달 공시 날짜를 최근 6개월치 모읍니다.

    연속 일수의 시작점을 잡는 용도입니다. 실패하면 빈 목록을 돌려주고,
    호출부가 네이버 이력으로 대체합니다.
    """
    yesterday = (datetime.strptime(before, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    dates: set[str] = set()
    for page in range(1, max_pages + 1):
        try:
            html = kind_request(add_months(before, -6), yesterday, page, report_nm=CORRELATION_KW)
        except Exception:  # noqa: BLE001 - 이력 조회 실패는 치명적이지 않습니다
            break
        rows = parse_kind_rows(html)
        if not rows:
            break
        for r in rows:
            if r["etf_name"] == etf_name and classify(r["title"]) == "violation":
                dates.add(r["date"])
    return sorted(dates, reverse=True)


def fetch_kind_etf_disclosures(date_iso: str, max_pages: int = 20) -> list[dict] | None:
    """KIND의 'ETF 공시' 목록(최신순)에서 **오늘자 엔에이치아문디** 건을 모읍니다.

    목록은 전체 ETF 공시를 최신순으로 보여주므로, 종목을 하나씩 훑을 필요 없이
    앞에서부터 읽다가 어제 날짜가 나오면 멈춥니다.

    이 실행 환경에서는 kind.krx.co.kr이 403으로 차단돼 요청 파라미터를 실제 응답으로
    검증하지 못했습니다. 응답을 못 받거나 해석하지 못하면 None을 돌려주고 네이버
    경로로 넘어갑니다. (KIND가 열려 있는 사내망에서는 이 경로가 그대로 쓰입니다.)
    """
    collected: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            html = kind_request(date_iso, date_iso, page)
        except Exception as exc:  # noqa: BLE001 - 차단/장애 시 네이버로 폴백
            print(f"  KIND 접근 실패({exc}) -> 네이버 공시로 전환", file=sys.stderr)
            return None

        rows = parse_kind_rows(html)
        if not rows:
            if page == 1:
                print("  KIND 응답을 해석하지 못함 -> 네이버 공시로 전환", file=sys.stderr)
                return None
            break

        collected += [r for r in rows if r["date"] == date_iso]
        if any(r["date"] < date_iso for r in rows):
            break  # 최신순이므로 어제 건이 보이면 오늘자는 끝
    # 제출인(운용사) 기준으로 우리 회사 건만 남깁니다.
    return [r for r in collected
            if "아문디" in r["submitter"] or BRAND_PREFIX in r["etf_name"]]


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
            r'href="(/item/news_notice_read\.naver\?no=\d+&code=[0-9A-Za-z]+&page_notice=\d+)"'
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


def etf_type(*texts: str) -> str:
    """종목명/공시제목으로 액티브 여부를 판정합니다."""
    return "active" if any(ACTIVE_KW in (t or "") for t in texts) else "passive"


def classify(title: str) -> str | None:
    if CORRELATION_KW in title:
        return "resolved" if any(k in title for k in RESOLVED_KW) else "violation"
    if any(k in title for k in RELATED_KW):
        return "related"
    return None


def load_history() -> tuple[list[str], dict[str, set[str]]]:
    """과거 실행 기록을 읽어 (확인한 날짜 목록, 날짜별 위반 종목코드)를 돌려줍니다.

    "영업일 연속"의 기준은 달력이 아니라 **이 스크립트가 실제로 확인한 날짜**입니다.
    루틴은 평일에만 도는데다 공휴일에는 공시 자체가 없으므로, 기록에 남은 확인일을
    영업일 달력으로 삼으면 주말·휴일 때문에 연속이 끊기는 오판을 피할 수 있습니다.
    (루틴이 하루 걸러 실패해 기록이 비어도 그날은 그냥 건너뛴 것으로 봅니다.)
    """
    if not os.path.exists(CSV_PATH):
        return [], {}
    dates: list[str] = []
    seen: set[str] = set()
    violations: dict[str, set[str]] = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            day = (row.get("check_date") or "").strip()
            if not day:
                continue
            if day not in seen:
                seen.add(day)
                dates.append(day)
            if row.get("status") == "violation":
                key = (row.get("ticker") or row.get("etf_name") or "").strip()
                if key:
                    violations.setdefault(day, set()).add(key)
    dates.sort()
    return dates, violations


def add_months(date_iso: str, months: int) -> str:
    """달력 기준으로 개월을 더합니다 (말일은 해당 월의 마지막 날로 맞춤)."""
    y, m, d = (int(x) for x in date_iso.split("-"))
    m += months
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    for day in range(d, 27, -1):  # 3/31 + 1개월 -> 4/30
        try:
            return datetime(y, m, day).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return datetime(y, m, d).strftime("%Y-%m-%d")


def calendar_span(since_iso: str, today_iso: str) -> tuple[int, str, int]:
    """(경과 달력일, 3개월 도달일, 도달까지 남은 달력일)."""
    since = datetime.strptime(since_iso, "%Y-%m-%d")
    today = datetime.strptime(today_iso, "%Y-%m-%d")
    deadline_iso = add_months(since_iso, WATCH_MONTHS)
    deadline = datetime.strptime(deadline_iso, "%Y-%m-%d")
    return (today - since).days + 1, deadline_iso, (deadline - today).days


# 위반 공시가 하루 걸러 이어질 때, 사이에 낀 날이 휴장일인지 판단하는 여유치.
# 금->월(3일) + 공휴일 1~2일까지는 "끊기지 않았다"로 봅니다.
MAX_GAP_DAYS = 4


def violation_dates_from_feed(ticker: str, before: str, max_pages: int = 4) -> list[str]:
    """네이버 공시 이력에서 이 종목의 상관계수 미달 공시 날짜를 모읍니다.

    우리 CSV에 기록이 쌓이기 전(첫 실행 등)에도 "며칠째 이어지는지"를 알아야 하므로,
    공시 피드 자체를 과거로 훑어 시작일을 잡습니다.
    """
    dates: list[str] = []
    for page in range(1, max_pages + 1):
        try:
            html = fetch(NOTICE_LIST_URL.format(code=ticker, page=page))
        except Exception:  # noqa: BLE001 - 이력 조회 실패는 치명적이지 않습니다
            break
        rows = re.findall(
            r'href="(/item/news_notice_read\.naver\?no=\d+&code=[0-9A-Za-z]+&page_notice=\d+)"'
            r'.*?<td class="date">\s*([\d.]+)\s*</td>',
            html,
            re.S,
        )
        if not rows:
            break
        stop = False
        for link, dotted in rows:
            day = dotted.strip().replace(".", "-")
            if day >= before:
                continue
            if day < add_months(before, -6):
                stop = True  # 6개월보다 오래된 이력은 볼 필요가 없습니다
                break
            title = notice_title(link.replace("&amp;", "&"))
            if classify(title) == "violation":
                dates.append(day)
        if stop:
            break
    return sorted(set(dates), reverse=True)


def streak_for(ticker: str, check_date: str, dates: list[str],
               violations: dict[str, set[str]],
               feed_dates: list[str] | None = None) -> tuple[int, str]:
    """오늘 포함, 이 종목의 위반이 몇 영업일째 이어지는지와 시작일.

    **최신 날짜에서 과거로 거꾸로** 세어 나가다가 위반이 끊긴 지점에서 멈춥니다.
    즉 항상 "지금 이어지고 있는 구간"만 세며, 그보다 더 과거에 있던 별개의 위반
    구간은 (중간에 회복된 적이 있으므로) 합산하지 않습니다.

    근거는 두 가지를 함께 씁니다.
      1) 우리 실행 기록(CSV) — 그날 확인했는데 위반이 없었다면 거기서 확실히 끊깁니다.
      2) 공시 피드의 과거 이력 — 기록이 없는 구간(첫 실행 등)을 메웁니다.
         피드에는 "위반이 없던 날"이 남지 않으므로, 위반 공시 사이의 간격이
         MAX_GAP_DAYS(주말+공휴일) 이내면 이어진 것으로 봅니다.
    """
    days = 1
    since = check_date
    checked_clean = {d for d in dates if ticker not in violations.get(d, set())}

    # 1) 우리 기록으로 확실히 이어지는 만큼 먼저 셉니다.
    for day in reversed([d for d in dates if d < check_date]):
        if day in checked_clean:
            return days, since  # 확인했는데 위반이 없던 날 -> 여기서 끊김
        days += 1
        since = day

    # 2) 기록이 없는 더 과거 구간은 공시 피드로 이어붙입니다.
    for day in feed_dates or []:
        if day >= since:
            continue
        if day in checked_clean:
            break
        gap = (datetime.strptime(since, "%Y-%m-%d") - datetime.strptime(day, "%Y-%m-%d")).days
        if gap > MAX_GAP_DAYS:
            break  # 사이가 너무 벌어짐 = 중간에 회복된 별개 구간
        days += 1
        since = day
    return days, since


def check(date_kst: datetime, workers: int = 8, source: str = "auto") -> dict:
    date_iso = date_kst.strftime("%Y-%m-%d")
    date_dotted = date_kst.strftime("%Y.%m.%d")

    used = "kind"
    findings: list[dict] = []
    scanned = 0

    kind_rows = None
    if source in ("auto", "kind"):
        print(f"[{date_iso}] KIND ETF 공시(최신순)에서 {MANAGER} 건을 확인합니다...")
        kind_rows = fetch_kind_etf_disclosures(date_iso)

    if kind_rows is not None:
        name_by_ticker = {}
        try:
            name_by_ticker = {name: code for code, name in list_hanaro_etfs()}
        except Exception:  # noqa: BLE001 - 티커 매핑 실패는 치명적이지 않습니다
            pass
        scanned = len(kind_rows)
        for row in kind_rows:
            kind_ = classify(row["title"])
            if kind_ is None:
                continue
            ticker = next(
                (code for name, code in name_by_ticker.items()
                 if name and name in row["etf_name"] + row["title"]),
                "",
            )
            findings.append({
                "status": kind_,
                "ticker": ticker,
                "etf_name": row["etf_name"],
                "etf_type": etf_type(row["etf_name"], row["title"]),
                "title": row["title"],
                "disclosure_date": date_iso,
                "url": row["url"],
            })
    else:
        if source == "kind":
            raise RuntimeError("KIND 조회에 실패했습니다 (--source kind).")
        used = "naver"
        etfs = list_hanaro_etfs()
        if not etfs:
            raise RuntimeError("HANARO ETF 목록을 가져오지 못했습니다.")
        print(f"[{date_iso}] {MANAGER} ETF {len(etfs)}종목 당일 공시 확인 중...")
        with ThreadPoolExecutor(workers) as ex:
            per_ticker = list(ex.map(lambda e: (e, todays_notice_links(e[0], date_dotted)), etfs))
            pending = [((c, n), l) for (c, n), links in per_ticker for l in links]
            titles = list(ex.map(lambda p: notice_title(p[1]), pending))
        scanned = len(pending)
        for ((code, name), link), title in zip(pending, titles):
            kind_ = classify(title)
            if kind_ is None:
                continue  # 분배락/LP 변경 등 상관계수와 무관한 공시는 제외
            findings.append({
                "status": kind_,
                "ticker": code,
                "etf_name": name,
                "etf_type": etf_type(name, title),
                "title": title,
                "disclosure_date": date_iso,
                "url": NOTICE_BASE + link,
            })

    for f in findings:
        f.setdefault("etf_type", "passive")
        f["threshold"] = THRESHOLD[f["etf_type"]]

    # 연속 영업일 수: 중간에 "위반 없음"으로 확인된 날이 끼면 처음부터 다시 셉니다.
    # (상관계수가 회복되면 그 이전 구간은 상장폐지 판단에서 의미가 없기 때문)
    dates, history = load_history()
    for f in findings:
        if f["status"] == "violation":
            feed = kind_violation_dates(f["etf_name"], date_iso) if used == "kind" else []
            if not feed and f["ticker"]:
                feed = violation_dates_from_feed(f["ticker"], date_iso)
            f["streak_days"], f["streak_since"] = streak_for(
                f["ticker"] or f["etf_name"], date_iso, dates, history, feed
            )
            # 3개월 요건은 영업일이 아니라 달력일 기준입니다.
            f["calendar_days"], f["deadline_3m"], f["days_to_3m"] = calendar_span(
                f["streak_since"], date_iso
            )

    findings.sort(key=lambda f: ({"violation": 0, "resolved": 1, "related": 2}[f["status"]], f["ticker"]))
    return {
        "checked_kst": datetime.now(timezone.utc).astimezone(KST).isoformat(timespec="seconds"),
        "check_date": date_iso,
        "manager": MANAGER,
        "source": used,
        "disclosures_today": scanned,
        "findings": findings,
    }


def report(result: dict) -> None:
    violations = [f for f in result["findings"] if f["status"] == "violation"]
    resolved = [f for f in result["findings"] if f["status"] == "resolved"]
    related = [f for f in result["findings"] if f["status"] == "related"]

    print(f"\n당일 공시 {result['disclosures_today']}건 확인 (출처: {result.get('source','')})")
    if violations:
        print(f"\n🚨 상관계수 미달(위반) 공시 {len(violations)}건")
        for f in violations:
            days, since = f.get("streak_days", 1), f.get("streak_since", result["check_date"])
            cont = f"{days}영업일째" + (f" (최초 {since})" if days > 1 else " (오늘 최초)")
            kind_ko = "액티브" if f["etf_type"] == "active" else "패시브"
            left = f["days_to_3m"]
            span = (f"달력 {f['calendar_days']}일 경과 / 3개월 요건 "
                    + (f"{left}일 남음 ({f['deadline_3m']})" if left > 0
                       else f"⚠️ 도달 ({f['deadline_3m']})"))
            print(f"  - [{f['ticker']}] {f['etf_name']} [{kind_ko}·기준 {f['threshold']}] — {cont}"
                  f"\n      {span}\n      {f['title']}\n      {f['url']}")
    else:
        print("\n✅ 상관계수 미달(위반) 공시 없음")
    if resolved:
        print(f"\n☑️  상관계수 미달 해소 공시 {len(resolved)}건")
        for f in resolved:
            kind_ko = "액티브" if f["etf_type"] == "active" else "패시브"
            print(f"  - [{f['ticker']}] {f['etf_name']} [{kind_ko}] : {f['title']}")
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
                                     "disclosure_date", "url", "etf_type",
                                     "threshold", "calendar_days", "deadline_3m",
                                     "days_to_3m", "streak_days", "streak_since")}})
    else:
        # 위반이 없었다는 사실도 기록으로 남깁니다.
        rows.append({**base, "status": "none", "ticker": "", "etf_name": "",
                     "title": "상관계수 관련 공시 없음", "disclosure_date": result["check_date"],
                     "url": "", "etf_type": "", "threshold": "",
                     "calendar_days": "", "deadline_3m": "", "days_to_3m": "",
                     "streak_days": "", "streak_since": ""})

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
    ap.add_argument("--source", choices=("auto", "kind", "naver"), default="auto",
                    help="공시 출처 (기본 auto: KIND 먼저, 실패 시 네이버)")
    ap.add_argument("--no-write", action="store_true", help="파일에 기록하지 않고 출력만")
    args = ap.parse_args()

    if args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=KST)
    else:
        day = datetime.now(timezone.utc).astimezone(KST)

    try:
        result = check(day, workers=args.workers, source=args.source)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: 공시 확인 실패: {exc}", file=sys.stderr)
        return 1

    report(result)
    if not args.no_write:
        persist(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
