# stock_universe

주식 117개 종목 데이터 정제 파이프라인. `bond_universe`, `crypto_universe`와 동일한 컨벤션을
따른다 — 코드는 이 레포(GitHub)에서 추적하고, 원본/가공 데이터는 Google Drive에서 관리한다
(`data/`는 `.gitignore` 대상).

## 실행 방법

```bash
cd stock_universe
mkdir -p data/raw
# Drive `raw/` 폴더의 xlsx 8개를 data/raw/ 에 다운로드
python scripts/refine_stock_data.py
# 결과: data/processed/{assets,stock_financials,stock_valuation,stock_price_detail}.csv
```

원본 8개 파일 중 `Fin_stock.xlsx`(56.6MB), `stock_price-volume_npq.xlsx`(10.9MB)는 Drive
커넥터의 10MB 다운로드 제한 때문에 브라우저에서 직접 받아야 한다. 나머지는 스크립트 상단
docstring에 소스별 용량과 시트 구조를 정리해뒀다.

## 산출물 → Drive 업로드 위치

`data/processed/*.csv`는 `캡스톤디자인/Data/processed/stock_universe/`에 올린다.

## ARCHITECTURE.md(IISE-CD-StockGame) 대비 확인 결과

DB 담당자가 `stock_price_detail` 등 migration을 작성할 때 참고할 사항:

- `assets`, `stock_financials`, `stock_valuation`은 DDL 컬럼과 1:1로 맞는다.
- `stock_price_detail`은 `close_price`, `volume`, `shares_outstanding`, `market_cap`,
  `foreign_qty`, `inst_qty`, `indiv_qty`까지는 채워지지만 **`open_price`/`high_price`/
  `low_price`는 원본 어디에도 없다**(FnGuide DataGuide 원천이 종가만 제공). 세 컬럼은
  NULL 허용으로 두거나 별도 시세 소스를 붙여야 한다.
- `foreign_qty`/`inst_qty`/`indiv_qty`는 원본 컬럼명이 "순매수수량"이다. 매수-매도
  순증감(음수 가능)이며 "총매수수량"이 아니다. DDL 컬럼명은 그대로 써도 되지만 의미를
  혼동하지 않도록 서버 쪽에 공유가 필요하다.
- `short_sale_amount`/`short_balance_amount`/`short_balance_ratio`(차입공매도) 3개
  컬럼은 현재 ARCHITECTURE.md DDL에 없는 추가 컬럼이다. `stock_price_detail`에 컬럼을
  추가하거나, 필요 없으면 import 시 드롭하면 된다.
- `masked_name`/`is_masked`는 비워뒀다(섹션 6 마스킹은 최종 적재 단계에서 `maskingService`가
  채우는 것이 맞다).
