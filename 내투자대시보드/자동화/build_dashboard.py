#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""대시보드 빌더 — 그날의 시장 데이터를 심고, 라이브러리를 인라인해 단독 실행본을 만듭니다.

매일 21시(KST) 루틴이 `collect.py` 다음에 이 스크립트를 실행합니다.

  collect.py  →  데이터/시장스냅샷.json  →  build_dashboard.py  →  대시보드_오프라인.html

하는 일 두 가지
  1) `대시보드.html` 의 MARKET_DATA 자리에 **그날 시장 스냅샷**을 심습니다.
     · 환율·지수·시장지표·Fear&Greed = 그날 값만(누적 없음)
     · 미국 기준금리 = 목표범위·EFFR 히스토리 포함(유일한 누적 지표)
  2) SheetJS·Chart.js를 인라인해서 **인터넷 없이 더블클릭으로 열리는** 파일로 굽습니다.

개인 원장(xlsx)은 전혀 건드리지 않습니다. 시장 데이터는 원장과 완전히 분리돼 있어,
원장 없이도 '오늘의 시장' 탭을 볼 수 있습니다.

사용법:
    python3 build_dashboard.py            # 대시보드_오프라인.html 생성
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "대시보드.html")
OUT = os.path.join(ROOT, "대시보드_오프라인.html")
SNAP = os.path.join(HERE, "데이터", "시장스냅샷.json")
LIB = os.path.join(HERE, "lib")

CDN_XLSX = "https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"
CDN_CHART = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"

MARKER = re.compile(r"/\*__MARKET_DATA__\*/.*?/\*__END_MARKET_DATA__\*/", re.S)


def load_snapshot():
    if not os.path.exists(SNAP):
        print(f"  ⚠ 스냅샷 없음({SNAP}) — 시장 데이터 없이 빌드합니다.", file=sys.stderr)
        return None
    with open(SNAP, encoding="utf-8") as f:
        return json.load(f)


def inject_market(html: str, snap) -> str:
    """MARKET_DATA 자리에 스냅샷 JSON을 심습니다."""
    if not MARKER.search(html):
        raise SystemExit("대시보드.html 에서 MARKET_DATA 마커를 찾지 못했습니다.")
    payload = "null" if snap is None else json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    # re.sub 의 백슬래시/그룹 해석을 피하려고 함수형 치환 사용
    return MARKER.sub(lambda _m: f"/*__MARKET_DATA__*/ {payload} /*__END_MARKET_DATA__*/", html, count=1)


def inline_libs(html: str) -> str:
    for cdn, name in ((CDN_XLSX, "xlsx.full.min.js"), (CDN_CHART, "chart.umd.min.js")):
        path = os.path.join(LIB, name)
        if not os.path.exists(path):
            raise SystemExit(f"라이브러리 없음: {path}")
        with open(path, encoding="utf-8") as f:
            code = f.read()
        tag = f'<script src="{cdn}"></script>'
        if tag not in html:
            raise SystemExit(f"대시보드.html 에서 스크립트 태그를 찾지 못했습니다: {cdn}")
        html = html.replace(tag, "<script>\n" + code + "\n</script>", 1)
    html = html.replace("<title>개인 투자 대시보드</title>",
                        "<title>개인 투자 대시보드 (오프라인 단독 실행)</title>", 1)
    if '<script src="http' in html:
        raise SystemExit("외부 스크립트 태그가 남아 있습니다.")
    return html


def main():
    with open(SRC, encoding="utf-8") as f:
        html = f.read()
    snap = load_snapshot()
    html = inject_market(html, snap)
    html = inline_libs(html)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    size = os.path.getsize(OUT) / 1024
    if snap:
        pr = (snap.get("policyRate") or {}).get("current") or {}
        fg = snap.get("fg") or {}
        print(f"생성: {OUT} ({size:.0f} KB)")
        print(f"  시장 스냅샷 {snap.get('asOf')} · F&G {fg.get('score')} · "
              f"기준금리 {pr.get('target_from')}~{pr.get('target_to')}% · "
              f"기준금리 히스토리 {len((snap.get('policyRate') or {}).get('history') or [])}개월")
    else:
        print(f"생성: {OUT} ({size:.0f} KB) — 시장 데이터 없음")


if __name__ == "__main__":
    main()
