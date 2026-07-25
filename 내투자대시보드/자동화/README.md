# 자동화 파이프라인 (공개 데이터 자동 수집)

내가 직접 기입하는 것 **외의** 공개 데이터를 서버(GitHub Actions)에서 자동으로 모읍니다.
개인 금융 데이터는 여기서 다루지 않습니다.

## 흐름

```
[매일 12:00 UTC = 21:00 KST]  collect.py
   환율(USD/KRW) · 벤치마크(S&P·NASDAQ·다우) · 종목시세 · 시장지표 · Fear&Greed
        └─►  데이터/*.csv 에 누적 (같은 날짜는 덮어쓰기)

[매년 초 / 필요 시]  build_ledger.py
   데이터/*.csv 의 월말 값 →  자동수집_원장.xlsx
        환율 · 벤치마크 · 월말보유(수량×월말가) 시트를 자동 채움
```

## 내가 하는 일은 딱 두 가지

1. **`tickers.txt`** 에 보유 종목을 적는다 — `티커  수량  자산군` (한 줄에 하나)
2. **연 1회** `자동수집_원장.xlsx` 를 열어 **`연별스냅샷`**(자산군별 연말평가액·당해순입금 + MMF·RP·현금 잔고)과 **`목표`** 만 채운다

그 파일을 **`대시보드.html`** 에 끌어놓으면 끝. 나머지(환율·지수·시세·심리)는 자동입니다.

## 켜는 법

저장소 **Settings → Actions** 활성화 후, `.github/workflows/`의 두 워크플로가 예약 실행됩니다.
Actions 탭에서 **Run workflow**로 즉시 실행도 가능합니다.

- `collect-daily.yml` — 일별 수집
- `collect-monthly.yml` — 월별 원장 조립

## 데이터 출처 (모두 공개·무료·키 불필요)

| 항목 | 출처 |
|---|---|
| 환율 USD/KRW | Naver 금융 (`marketindex/exchange/FX_USDKRW`) |
| 벤치마크 | Naver 금융 (`index/.INX`·`.IXIC`·`.DJI`) |
| 종목 시세 | Naver 금융 (`stock/{티커}.O` 등) |
| 시장지표 | Naver 금융 (미국채10년 `bond/US10YT=RR` · 국제금 `metals/GCcv1` · WTI `energy/CLcv1`) |
| Fear & Greed | CNN Business (실제 지수, 실패 시 그날은 건너뜀) |

> Yahoo가 rate-limit(429)으로 막혀 **Naver 금융 API로 교체**했습니다. Naver·CNN은 이 수집
> 환경에서 실동작을 확인했습니다(환율·지수·종목·미국채/금/WTI·CNN F&G 모두 실값 수신).

## 회사 PC로 전달

회사 PC는 GitHub·시세 사이트가 막혀 있을 수 있으므로, 완성된 `자동수집_원장.xlsx`(또는
데이터를 넣어 다시 구운 오프라인 대시보드)를 **회사에서 받을 수 있는 경로**(이 채팅·이메일·
허용된 클라우드)로 전달하는 방식이 현실적입니다. 자세한 건 대시보드 **개발기록** 탭 참고.

> ✅ Naver 시세와 CNN Fear & Greed는 이 수집 환경에서 **실제로 fetch됨**을 확인했습니다.
> 다만 회사 PC 자체는 GitHub/시세가 막혀 있으니, 완성 파일을 위 '회사 PC로 전달' 경로로 받으세요.
