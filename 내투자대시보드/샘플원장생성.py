#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""샘플 투자원장 생성기 (테스트/데모용) — 연별 버전.

SPEC.md §8(개발 환경 규칙)에 따라, 실제 원장 없이 대시보드를 테스트할 수 있도록
**더미 데이터**를 만들어 `투자원장_샘플.xlsx` 로 저장합니다.

구조(중요):
  · 개인 성과는 **연별**입니다 — `연별스냅샷` 시트에 (연도·자산군·연말평가액·그해순입금).
  · 공개 시장 데이터(환율·벤치마크·시장지표)와 월말보유·배당은 **월별 그대로** 둡니다.
    → 대시보드가 연말값을 자동 샘플링하고, 심리게이지(Fear&Greed)는 월별 벤치마크로 계산합니다.

- 시트 헤더는 4행, 데이터는 5행부터(§2).
- 자산군은 §2가 정한 6종만 사용합니다.
- seed 고정이라 실행할 때마다 동일하게 재현됩니다. 전부 **가짜 값**이라 커밋해도 안전.

사용법:
    python3 샘플원장생성.py               # 투자원장_샘플.xlsx (7년, 2019~2025)
    python3 샘플원장생성.py 8 파일.xlsx    # 연수/파일명 지정
"""
from __future__ import annotations

import random
import sys

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

SEED = 20260725
random.seed(SEED)

LAST_YEAR = 2025

# 자산군 6종 (§2). (자산군, 통화, 시드 연말평가액, 연평균수익률, 연변동성, 연 순입금)
ASSETS = [
    ("해외주식",   "USD",     14000,   0.115, 0.170,   6000),
    ("해외ETF",   "USD",      9000,   0.100, 0.150,   4200),
    ("미국장기채", "USD",      8000,   0.015, 0.110,   2400),
    ("MMF",       "KRW",   7_000_000, 0.033, 0.006, 4_000_000),
    ("RP",        "KRW",   5_000_000, 0.030, 0.005,       0),
    ("달러현금",   "USD",      2500,   0.000, 0.003,       0),
]

HEADER_FILL = PatternFill("solid", fgColor="1F2A44")
HEADER_FONT = Font(color="FFFFFF", bold=True)
WRAP = Alignment(wrap_text=True, vertical="center")


def year_list(n: int, last: int = LAST_YEAR) -> list[int]:
    return list(range(last - n + 1, last + 1))


def months_span(years: list[int]) -> list[str]:
    out = []
    for y in years:
        for m in range(1, 13):
            out.append(f"{y}-{m:02d}")
    return out


def write_header(ws, title, subtitle, headers, widths):
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = subtitle
    ws["A2"].font = Font(italic=True, color="666666")
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=4, column=j, value=h)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = WRAP
    for j, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = w
    ws.freeze_panes = "A5"


def build(years: list[int]):
    months = months_span(years)
    wb = openpyxl.Workbook()

    # ── 안내 ────────────────────────────────────────────────────────────
    g = wb.active
    g.title = "안내"
    g["A1"] = "투자 대시보드 원장 — 샘플(더미) 데이터 · 연별"
    g["A1"].font = Font(bold=True, size=13)
    g["A2"] = "테스트/데모용 가짜 데이터입니다. 개인 성과는 '연별스냅샷'에 연 1회만 채우면 됩니다."
    g["A2"].font = Font(italic=True, color="666666")
    g["A5"] = "자산군은 아래 6종만 사용합니다:"
    for i, (name, *_ ) in enumerate(ASSETS, start=6):
        g.cell(row=i, column=1, value=name)
    g.column_dimensions["A"].width = 52

    # ── 연별스냅샷 (개인 성과 · 직접 입력) ──────────────────────────────
    ws = wb.create_sheet("연별스냅샷")
    write_header(
        ws, "연별 스냅샷 — 자산군별로 연 1줄",
        "기준연은 YYYY 텍스트. 금액은 해당 통화 기준 그대로. 연말평가액 + 그해 순입금.",
        ["기준연\n(YYYY)", "자산군", "통화", "연말평가액", "당해순입금", "계좌(선택)", "비고"],
        [12, 12, 8, 15, 15, 12, 26],
    )
    prev_ev = {a[0]: 0.0 for a in ASSETS}
    r = 5
    for yi, year in enumerate(years):
        for (name, cur, seed_amt, mu, sig, dep) in ASSETS:
            if yi == 0:
                ev = float(seed_amt)
                cf = ev  # 첫 해: 순입금 = 평가액
            else:
                ret = random.gauss(mu, sig)
                if year == 2022 and name in ("해외주식", "해외ETF", "미국장기채"):
                    ret = -abs(random.gauss(0.17, 0.05))   # 2022 위험자산 하락(낙폭 데모)
                cf = float(dep)
                if random.random() < 0.25:            # 가끔 목돈 입금/인출
                    cf += random.choice([-1, 1, 1]) * seed_amt * random.uniform(0.10, 0.40)
                ev = max(prev_ev[name] * (1 + ret) + cf, 0.0)
            prev_ev[name] = ev
            acct = "미래에셋" if cur == "USD" else "NH투자"
            ev_v = round(ev, 2) if cur == "USD" else round(ev)
            cf_v = round(cf, 2) if cur == "USD" else round(cf)
            ws.cell(row=r, column=1, value=str(year))   # 텍스트 "YYYY"
            ws.cell(row=r, column=2, value=name)
            ws.cell(row=r, column=3, value=cur)
            ws.cell(row=r, column=4, value=ev_v)
            ws.cell(row=r, column=5, value=cf_v)
            ws.cell(row=r, column=6, value=acct)
            ws.cell(row=r, column=7, value="첫 해라 순입금=평가액" if yi == 0 else "")
            r += 1

    # ── 환율 (월별 · 자동수집 대상) ─────────────────────────────────────
    fx_ws = wb.create_sheet("환율")
    write_header(
        fx_ws, "월말 환율 (월별 · 자동수집)", "USD/KRW 월말 종가. 성과 환산은 연말값을 자동 사용.",
        ["기준월\n(YYYY-MM)", "USD/KRW\n(월말 종가)"], [14, 14],
    )
    fx = 1150.0
    for i, ym in enumerate(months):
        fx = min(1480.0, max(1050.0, fx + random.gauss(3.0, 16)))
        fx_ws.cell(row=5 + i, column=1, value=ym)
        fx_ws.cell(row=5 + i, column=2, value=round(fx))

    # ── 벤치마크 (월별 · 심리게이지+비교용) ─────────────────────────────
    bm = wb.create_sheet("벤치마크")
    write_header(
        bm, "월말 벤치마크 지수 (월별 · 자동수집)", "월 1줄, 숫자 3개. 심리게이지가 이 월별값을 씁니다.",
        ["기준월\n(YYYY-MM)", "S&P 500", "NASDAQ\n종합", "다우존스\n산업평균"], [14, 12, 12, 14],
    )
    sp, nq, dj = 2600.0, 7000.0, 24000.0
    for i, ym in enumerate(months):
        sp *= 1 + random.gauss(0.009, 0.040)
        nq *= 1 + random.gauss(0.011, 0.050)
        dj *= 1 + random.gauss(0.007, 0.035)
        bm.cell(row=5 + i, column=1, value=ym)
        bm.cell(row=5 + i, column=2, value=round(sp, 2))
        bm.cell(row=5 + i, column=3, value=round(nq, 2))
        bm.cell(row=5 + i, column=4, value=round(dj, 2))

    # ── 월말보유 (월별 · 종목 비중/가격수익률용) ───────────────────────
    hold = wb.create_sheet("월말보유")
    write_header(
        hold, "월말 종목별 보유 (월별 · 선택)", "종목 비중 도넛·가격수익률용. 자동수집이 채웁니다.",
        ["기준월\n(YYYY-MM)", "티커", "종목명", "수량", "종가\n(USD)", "평가액\n(USD)", "자산군"],
        [14, 10, 26, 10, 12, 14, 12],
    )
    # (티커, 종목명, 시작수량, 시작가, 월평균수익률, 월변동성, 자산군)
    holdings_def = [
        ("VOO", "Vanguard S&P 500 ETF", 20, 250, 0.010, 0.041, "해외ETF"),
        ("QQQ", "Invesco QQQ Trust", 10, 170, 0.012, 0.053, "해외ETF"),
        ("AAPL", "Apple Inc.", 40, 55, 0.011, 0.056, "해외주식"),
        ("MSFT", "Microsoft Corp.", 18, 110, 0.012, 0.048, "해외주식"),
        ("TLT", "iShares 20+ Yr Treasury", 90, 130, -0.002, 0.031, "미국장기채"),
    ]
    hr = 5
    for (tkr, nm, qty0, px0, mu, sig, cls) in holdings_def:
        px = float(px0)
        qty = qty0
        for mi, ym in enumerate(months):
            if mi > 0:
                px = max(px * (1 + random.gauss(mu, sig)), 1.0)
                if random.random() < 0.12:
                    qty += random.randint(1, 3)
            px_r = round(px, 2)
            hold.cell(row=hr, column=1, value=ym)
            hold.cell(row=hr, column=2, value=tkr)
            hold.cell(row=hr, column=3, value=nm)
            hold.cell(row=hr, column=4, value=qty)
            hold.cell(row=hr, column=5, value=px_r)
            hold.cell(row=hr, column=6, value=round(px_r * qty, 2))
            hold.cell(row=hr, column=7, value=cls)
            hr += 1

    # ── 목표 ────────────────────────────────────────────────────────────
    goal = wb.create_sheet("목표")
    write_header(
        goal, "재무 목표 · 복리 시뮬레이션 가정", "가정수익률은 대시보드 슬라이더 시작값.",
        ["목표명", "목표금액\n(KRW)", "목표일\n(YYYY-MM)", "월 저축액\n(KRW)", "가정 연복리\n수익률", "비고"],
        [16, 16, 12, 14, 14, 20],
    )
    goal.cell(row=5, column=1, value="1조 만들기")
    goal.cell(row=5, column=2, value=1_000_000_000_000)   # 기본 목표: 1조원
    goal.cell(row=5, column=3, value="2055-12")
    goal.cell(row=5, column=4, value=2_000_000)
    goal.cell(row=5, column=5, value=0.10)
    goal.cell(row=5, column=6, value="기본 목표 (샘플)")

    # ── 배당 (월별 · 분기별 · 대시보드가 연 단위로 합산) ────────────────
    dv = wb.create_sheet("배당")
    write_header(
        dv, "배당 수령 (월별 · 선택)", "종목별로 배당 받은 달에 한 줄. 대시보드가 연 단위로 합산합니다.",
        ["기준월\n(YYYY-MM)", "티커", "배당금\n(USD)", "비고"], [14, 10, 14, 18],
    )
    dyield = {"VOO": 0.013, "QQQ": 0.006, "AAPL": 0.005, "MSFT": 0.008, "TLT": 0.038}
    dr = 5
    for (tkr, nm, qty0, px0, mu, sig, cls) in holdings_def:
        y = dyield.get(tkr, 0.0)
        if y <= 0:
            continue
        for mi, ym in enumerate(months):
            if mi % 3 != 2:            # 분기마다
                continue
            amt = px0 * qty0 * (1 + 0.02 * mi / 3) * (y / 4)  # 대략 성장 반영
            dv.cell(row=dr, column=1, value=ym)
            dv.cell(row=dr, column=2, value=tkr)
            dv.cell(row=dr, column=3, value=round(amt, 2))
            dr += 1

    # ── 시장지표 (월별 · 미국채10년·금·WTI) ────────────────────────────
    mk = wb.create_sheet("시장지표")
    write_header(
        mk, "월말 시장지표 (월별 · 자동수집)", "미국채 10년 금리 · 금 · WTI 원유 월말 값.",
        ["기준월\n(YYYY-MM)", "미국채10년\n(%)", "금\n(USD/oz)", "WTI\n(USD/bbl)"], [14, 12, 12, 12],
    )
    y10, gold, wti = 2.6, 1500.0, 60.0
    for i, ym in enumerate(months):
        y10 = min(6.0, max(0.5, y10 + random.gauss(0.01, 0.10)))
        gold *= 1 + random.gauss(0.006, 0.03)
        wti = min(130.0, max(20.0, wti * (1 + random.gauss(0.004, 0.07))))
        mk.cell(row=5 + i, column=1, value=ym)
        mk.cell(row=5 + i, column=2, value=round(y10, 2))
        mk.cell(row=5 + i, column=3, value=round(gold))
        mk.cell(row=5 + i, column=4, value=round(wti, 2))

    return wb


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    out = sys.argv[2] if len(sys.argv) > 2 else "투자원장_샘플.xlsx"
    years = year_list(n)
    wb = build(years)
    wb.save(out)
    print(f"생성 완료: {out}  (연별스냅샷 {years[0]}~{years[-1]}, {n}년 · 월별 보조데이터 {n*12}개월)")


if __name__ == "__main__":
    main()
