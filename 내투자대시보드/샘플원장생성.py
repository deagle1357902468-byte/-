#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""샘플 투자원장 생성기 (테스트/데모용).

SPEC.md §8(개발 환경 규칙)에 따라, 실제 원장 파일 없이 대시보드를 테스트할 수 있도록
24개월치 **더미 데이터**를 만들어 `투자원장_샘플.xlsx` 로 저장합니다.

- 시트 구조·헤더 위치는 §2를 그대로 따릅니다: 4행 헤더, 5행부터 데이터.
- 자산군은 §2가 정한 6종만 사용합니다.
- 숫자는 무작위이지만 seed 고정이라 실행할 때마다 동일하게 재현됩니다.
- 실제 금융 데이터가 아니라 **가짜 값**입니다. 그대로 커밋해도 안전합니다.

사용법:
    python3 샘플원장생성.py            # 투자원장_샘플.xlsx 생성 (24개월)
    python3 샘플원장생성.py 36 파일.xlsx  # 개월수/파일명 지정
"""
from __future__ import annotations

import random
import sys
from datetime import date

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

SEED = 20260724
random.seed(SEED)

# 자산군 6종 (§2). (자산군, 통화, 첫달 시드금액, 월평균수익률, 월변동성)
ASSETS = [
    ("해외주식",   "USD",     32400,   0.0090, 0.045),
    ("해외ETF",   "USD",     18200,   0.0080, 0.038),
    ("미국장기채", "USD",     11500,   0.0015, 0.030),
    ("MMF",       "KRW",   8_000_000, 0.0028, 0.0010),
    ("RP",        "KRW",   5_000_000, 0.0025, 0.0008),
    ("달러현금",   "USD",      3000,   0.0000, 0.0005),
]

# 대략적인 월 정기적립 (해당 통화). 첫 달은 시드금액이 곧 순입금.
MONTHLY_DEPOSIT = {
    "해외주식": 800, "해외ETF": 600, "미국장기채": 300,
    "MMF": 500_000, "RP": 0, "달러현금": 0,
}

HEADER_FILL = PatternFill("solid", fgColor="1F2A44")
HEADER_FONT = Font(color="FFFFFF", bold=True)
WRAP = Alignment(wrap_text=True, vertical="center")


def month_list(n: int, last=(2026, 7)) -> list[str]:
    """마지막 달을 last 로 두고 n개월치 'YYYY-MM' 문자열을 오름차순으로."""
    y, m = last
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


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


def build(months: list[str]):
    wb = openpyxl.Workbook()

    # ── 안내 ────────────────────────────────────────────────────────────
    g = wb.active
    g.title = "안내"
    g["A1"] = "투자 대시보드 원장 — 샘플(더미) 데이터"
    g["A1"].font = Font(bold=True, size=13)
    g["A2"] = "이 파일은 테스트/데모용 가짜 데이터입니다. 실제 값이 아닙니다."
    g["A2"].font = Font(italic=True, color="666666")
    g["A5"] = "자산군은 아래 6종만 사용합니다:"
    for i, (name, *_ ) in enumerate(ASSETS, start=6):
        g.cell(row=i, column=1, value=name)
    g.column_dimensions["A"].width = 46

    # ── 월별스냅샷 ──────────────────────────────────────────────────────
    ws = wb.create_sheet("월별스냅샷")
    write_header(
        ws, "월별 스냅샷 — 자산군별로 한 줄씩",
        "기준월은 YYYY-MM 텍스트. 금액은 해당 통화 기준 그대로 입력.",
        ["기준월\n(YYYY-MM)", "자산군", "통화", "기말평가액", "당월순입금", "계좌(선택)", "비고"],
        [14, 12, 8, 14, 14, 12, 24],
    )
    # 자산군별 평가액 궤적 시뮬레이션
    prev_ev = {a[0]: 0.0 for a in ASSETS}
    r = 5
    for mi, ym in enumerate(months):
        for (name, cur, seed_amt, mu, sig) in ASSETS:
            if mi == 0:
                ev = float(seed_amt)
                cf = ev  # 첫 달: 순입금 = 평가액
            else:
                ret = random.gauss(mu, sig)
                cf = float(MONTHLY_DEPOSIT[name])
                # 가끔 목돈 입금/인출
                if random.random() < 0.10:
                    cf += random.choice([-1, 1, 1]) * seed_amt * random.uniform(0.05, 0.20)
                ev = prev_ev[name] * (1 + ret) + cf
                ev = max(ev, 0.0)
            prev_ev[name] = ev
            acct = "미래에셋" if cur == "USD" else "NH투자"
            ev_v = round(ev, 2) if cur == "USD" else round(ev)
            cf_v = round(cf, 2) if cur == "USD" else round(cf)
            ws.cell(row=r, column=1, value=ym)
            ws.cell(row=r, column=2, value=name)
            ws.cell(row=r, column=3, value=cur)
            ws.cell(row=r, column=4, value=ev_v)
            ws.cell(row=r, column=5, value=cf_v)
            ws.cell(row=r, column=6, value=acct)
            ws.cell(row=r, column=7, value="" if mi else "첫 달이라 순입금=평가액")
            r += 1

    # ── 환율 ────────────────────────────────────────────────────────────
    fx_ws = wb.create_sheet("환율")
    write_header(
        fx_ws, "월말 환율", "월 1줄. USD/KRW 월말 종가.",
        ["기준월\n(YYYY-MM)", "USD/KRW\n(월말 종가)"], [14, 14],
    )
    fx = 1380.0
    fx_by_month = {}
    for i, ym in enumerate(months):
        fx += random.gauss(0, 14)
        fx = min(1480.0, max(1300.0, fx))
        fx_by_month[ym] = round(fx)
        fx_ws.cell(row=5 + i, column=1, value=ym)
        fx_ws.cell(row=5 + i, column=2, value=round(fx))

    # ── 벤치마크 ────────────────────────────────────────────────────────
    bm = wb.create_sheet("벤치마크")
    write_header(
        bm, "월말 벤치마크 지수", "월 1줄, 숫자 3개.",
        ["기준월\n(YYYY-MM)", "S&P 500", "NASDAQ\n종합", "다우존스\n산업평균"], [14, 12, 12, 14],
    )
    sp, nq, dj = 5400.0, 17600.0, 40200.0
    for i, ym in enumerate(months):
        sp *= 1 + random.gauss(0.010, 0.040)
        nq *= 1 + random.gauss(0.012, 0.050)
        dj *= 1 + random.gauss(0.008, 0.035)
        bm.cell(row=5 + i, column=1, value=ym)
        bm.cell(row=5 + i, column=2, value=round(sp, 2))
        bm.cell(row=5 + i, column=3, value=round(nq, 2))
        bm.cell(row=5 + i, column=4, value=round(dj, 2))

    # ── 월말보유 (분기별로만 몇 종목) ───────────────────────────────────
    hold = wb.create_sheet("월말보유")
    write_header(
        hold, "월말 종목별 보유 (선택)", "종목 비중 도넛차트용. 분기 1회로 충분.",
        ["기준월\n(YYYY-MM)", "티커", "종목명", "수량", "종가\n(USD)", "평가액\n(USD)", "자산군"],
        [14, 10, 26, 10, 12, 14, 12],
    )
    holdings = [
        ("VOO", "Vanguard S&P 500 ETF", 30, "해외ETF"),
        ("QQQ", "Invesco QQQ Trust", 14, "해외ETF"),
        ("AAPL", "Apple Inc.", 60, "해외주식"),
        ("MSFT", "Microsoft Corp.", 25, "해외주식"),
        ("TLT", "iShares 20+ Yr Treasury", 130, "미국장기채"),
    ]
    hr = 5
    last_ym = months[-1]
    for (tkr, nm, qty, cls) in holdings:
        px = round(random.uniform(80, 620), 2)
        hold.cell(row=hr, column=1, value=last_ym)
        hold.cell(row=hr, column=2, value=tkr)
        hold.cell(row=hr, column=3, value=nm)
        hold.cell(row=hr, column=4, value=qty)
        hold.cell(row=hr, column=5, value=px)
        hold.cell(row=hr, column=6, value=round(px * qty, 2))
        hold.cell(row=hr, column=7, value=cls)
        hr += 1

    # ── 목표 ────────────────────────────────────────────────────────────
    goal = wb.create_sheet("목표")
    write_header(
        goal, "재무 목표 · 복리 시뮬레이션 가정", "가정수익률은 대시보드 슬라이더 시작값.",
        ["목표명", "목표금액\n(KRW)", "목표일\n(YYYY-MM)", "월 저축액\n(KRW)", "가정 연복리\n수익률", "비고"],
        [16, 16, 12, 14, 14, 20],
    )
    goal.cell(row=5, column=1, value="10년 내 5억 만들기")
    goal.cell(row=5, column=2, value=500_000_000)
    goal.cell(row=5, column=3, value="2036-07")
    goal.cell(row=5, column=4, value=2_000_000)
    goal.cell(row=5, column=5, value=0.08)
    goal.cell(row=5, column=6, value="샘플 목표")

    return wb


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    out = sys.argv[2] if len(sys.argv) > 2 else "투자원장_샘플.xlsx"
    months = month_list(n)
    wb = build(months)
    wb.save(out)
    print(f"생성 완료: {out}  ({months[0]} ~ {months[-1]}, {n}개월)")


if __name__ == "__main__":
    main()
