#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""월별 원장 조립기 (자동화 파이프라인).

`데이터/`에 누적된 일별 공개 데이터에서 **월말 값**을 뽑아, 대시보드가 읽는 원장 양식
(§2)의 자동 채움 시트를 만들어 `자동수집_원장.xlsx`로 저장합니다.

자동으로 채우는 것 (공개 데이터):
  · 환율      : 월말 USD/KRW
  · 벤치마크  : 월말 S&P500 / NASDAQ / 다우
  · 월말보유  : tickers.txt의 (티커, 수량) × 월말 종가 → 평가액USD

당신이 채우는 것 (개인 데이터, 연 1회):
  · 연별스냅샷: 자산군별 연말평가액 + 당해순입금 (+ MMF·RP·현금 잔고)
  · 목표      : 목표금액·목표일·월저축액

즉 이 파일을 열어 '연별스냅샷'·'목표'만 채우면 원장이 완성됩니다.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "데이터")
OUT = os.path.join(HERE, "자동수집_원장.xlsx")
TICKERS = os.path.join(HERE, "tickers.txt")

HFILL = PatternFill("solid", fgColor="1F2A44")
HFONT = Font(color="FFFFFF", bold=True)
WRAP = Alignment(wrap_text=True, vertical="center")


def read_csv(name):
    p = os.path.join(DATA_DIR, name)
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def month_end_map(rows, valcols):
    """{YYYY-MM: {col: value}} — 각 월의 마지막 날짜 값."""
    best = {}
    for r in rows:
        d = r.get("date", "")
        if len(d) < 7:
            continue
        ym = d[:7]
        if ym not in best or d > best[ym]["date"]:
            best[ym] = r
    out = {}
    for ym, r in best.items():
        out[ym] = {c: r.get(c) for c in valcols}
    return out


def read_tickers():
    out = []
    if os.path.exists(TICKERS):
        for line in open(TICKERS, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split()
            out.append((p[0], float(p[1]) if len(p) > 1 else 0.0, p[2] if len(p) > 2 else ""))
    return out


def header(ws, title, sub, cols, widths):
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = sub
    ws["A2"].font = Font(italic=True, color="666666")
    for j, c in enumerate(cols, 1):
        cell = ws.cell(row=4, column=j, value=c)
        cell.fill = HFILL
        cell.font = HFONT
        cell.alignment = WRAP
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = w
    ws.freeze_panes = "A5"


def main():
    fx = month_end_map(read_csv("환율_일별.csv"), ["usdkrw"])
    bm = month_end_map(read_csv("벤치마크_일별.csv"), ["sp", "nasdaq", "dow"])
    # 시세는 (ticker별) 월말
    px_rows = read_csv("시세_일별.csv")
    by_ticker = defaultdict(list)
    for r in px_rows:
        by_ticker[r.get("ticker", "")].append(r)
    px_me = {t: month_end_map(rows, ["close"]) for t, rows in by_ticker.items()}
    tickers = read_tickers()

    wb = openpyxl.Workbook()
    g = wb.active
    g.title = "안내"
    g["A1"] = "자동수집 원장 (공개 데이터 자동 채움)"
    g["A1"].font = Font(bold=True, size=13)
    g["A2"] = "'연별스냅샷'과 '목표'만 채우면 대시보드에 넣을 수 있습니다. (환율·벤치마크·월말보유·시장지표는 자동)"
    g["A2"].font = Font(italic=True, color="666666")
    g.column_dimensions["A"].width = 60

    # 연별스냅샷 — 헤더만 (사용자 입력, 연 1회)
    ws = wb.create_sheet("연별스냅샷")
    header(ws, "연별 스냅샷 — 자산군별로 연 1줄 (직접 입력)",
           "기준연(YYYY)·자산군·통화·연말평가액·당해순입금. 자산군 6종만. 연 1회만 채우면 됩니다.",
           ["기준연\n(YYYY)", "자산군", "통화", "연말평가액", "당해순입금", "계좌(선택)", "비고"],
           [12, 12, 8, 15, 15, 12, 24])

    # 환율 (자동)
    fxs = wb.create_sheet("환율")
    header(fxs, "월말 환율 (자동수집)", "USD/KRW 월말 종가.",
           ["기준월\n(YYYY-MM)", "USD/KRW\n(월말 종가)"], [14, 14])
    for i, ym in enumerate(sorted(fx)):
        fxs.cell(row=5 + i, column=1, value=ym)
        fxs.cell(row=5 + i, column=2, value=_num(fx[ym]["usdkrw"]))

    # 벤치마크 (자동)
    bms = wb.create_sheet("벤치마크")
    header(bms, "월말 벤치마크 (자동수집)", "월말 종가.",
           ["기준월\n(YYYY-MM)", "S&P 500", "NASDAQ\n종합", "다우존스\n산업평균"], [14, 12, 12, 14])
    for i, ym in enumerate(sorted(bm)):
        bms.cell(row=5 + i, column=1, value=ym)
        bms.cell(row=5 + i, column=2, value=_num(bm[ym]["sp"]))
        bms.cell(row=5 + i, column=3, value=_num(bm[ym]["nasdaq"]))
        bms.cell(row=5 + i, column=4, value=_num(bm[ym]["dow"]))

    # 월말보유 (자동: 티커×수량×월말가)
    hs = wb.create_sheet("월말보유")
    header(hs, "월말 종목별 보유 (자동수집)", "tickers.txt의 수량 × 월말 종가.",
           ["기준월\n(YYYY-MM)", "티커", "종목명", "수량", "종가\n(USD)", "평가액\n(USD)", "자산군"],
           [14, 10, 22, 10, 12, 14, 12])
    r = 5
    all_months = sorted(set().union(*[set(px_me.get(t, {})) for t, _, _ in tickers]) if tickers else set())
    for ym in all_months:
        for tk, qty, cls in tickers:
            m = px_me.get(tk, {}).get(ym)
            if not m:
                continue
            px = _num(m["close"])
            if px is None:
                continue
            hs.cell(row=r, column=1, value=ym)
            hs.cell(row=r, column=2, value=tk)
            hs.cell(row=r, column=3, value="")
            hs.cell(row=r, column=4, value=qty)
            hs.cell(row=r, column=5, value=round(px, 2))
            hs.cell(row=r, column=6, value=round(px * qty, 2))
            hs.cell(row=r, column=7, value=cls)
            r += 1

    # 목표 — 헤더만
    gs = wb.create_sheet("목표")
    header(gs, "재무 목표 (직접 입력)", "목표금액·목표일·월저축액·가정수익률.",
           ["목표명", "목표금액\n(KRW)", "목표일\n(YYYY-MM)", "월 저축액\n(KRW)", "가정 연복리\n수익률", "비고"],
           [16, 16, 12, 14, 14, 20])

    # 시장지표 (자동)
    mkt = month_end_map(read_csv("시장지표_일별.csv"), ["us10y", "gold", "wti"])
    if mkt:
        mks = wb.create_sheet("시장지표")
        header(mks, "월말 시장지표 (자동수집)", "미국채10년·금·WTI.",
               ["기준월\n(YYYY-MM)", "미국채10년\n(%)", "금\n(USD/oz)", "WTI\n(USD/bbl)"], [14, 12, 12, 12])
        for i, ym in enumerate(sorted(mkt)):
            mks.cell(row=5 + i, column=1, value=ym)
            mks.cell(row=5 + i, column=2, value=_num(mkt[ym]["us10y"]))
            mks.cell(row=5 + i, column=3, value=_num(mkt[ym]["gold"]))
            mks.cell(row=5 + i, column=4, value=_num(mkt[ym]["wti"]))

    wb.save(OUT)
    print(f"생성: {OUT}  (환율 {len(fx)}개월 · 벤치마크 {len(bm)}개월 · 종목 {len(tickers)}개)")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
