# 뉴스 데이터 계약서 (News Data Contract)

> **목적**: 데이터 파이프라인이 생산하는 "게임용 뉴스" 데이터의 **형식과 구조**를 정의한다.
> 게임 팀(프론트/게임로직/백엔드)은 이 문서만 보고도 실제 데이터가 도착하기 전에 로더·렌더링·이벤트 처리를 미리 구현할 수 있다.
> **실제 데이터 파일은 Google Drive에 있다 → [§6 데이터 위치](#6-데이터-위치--상태).**

작성 기준일: 2026-06-24 · 생성 경로: `news_generator/data/interim/game_publish_calendar/`
(이 폴더는 `.gitignore`로 git에 올라가지 않음 — 데이터는 Drive로 전달, 본 계약서만 git에 둔다.)

---

## 0. 한눈에 보기

| 파일 | 종류 | 건수* | 날짜 범위* | 핵심 식별자 |
|---|---|---|---|---|
| `market_news.game.jsonl` | **거시/시장 뉴스** (지수·금리·유가·환율·섹터) | 9,169 | 2013-01-03 ~ 2023-12-28 | `news_id` |
| `stock_news.game.jsonl` | **개별종목 공시 뉴스** | 1,629 | 2013-01-23 ~ 2023-12-29 | `news_id` |
| `annual_earnings_news.game.jsonl` | 개별종목 **연간 실적** 뉴스 | 1,032 | 2013-01-23 ~ 2024-03-28 | `news_id` |
| `split_articles.game.jsonl` | 공시→실적/반응 **분할 기사** | 794 | 2013-01-25 ~ 2023-12-15 | `article_id` |

\* 2026-06-22 빌드 기준. **건수·본문 내용은 잠정값**(거시뉴스 전기간 재생성 진행 중). **스키마(필드 구조)는 확정 계약**이다.

### 공통 규칙 (4개 파일 전부 해당)
- **포맷**: JSON Lines (`.jsonl`) — **한 줄 = 뉴스 한 건**(독립 JSON 객체). 파일 전체를 한 번에 파싱하지 말고 줄 단위로 읽는다.
- **인코딩**: UTF-8. 원본은 한글이 `\uXXXX`로 이스케이프되어 있을 수 있으나 `JSON.parse`하면 정상 한글.
- **본문 필드**: 모든 파일이 `news_lines` (문자열 배열)를 가진다 → **게임 화면에 그대로 출력**할 완성형 기사 문장. 문단/문장을 배열 원소로 나눠 담는다.
- **게임 발행일**: 모든 파일이 `game_publish_date` (`YYYY-MM-DD`)를 가진다 → **이 날짜가 속한 턴에 노출**한다. (§5 참조)
- 누락 필드는 "해당 없음"을 의미. 같은 파일 안에서도 일부 필드는 특정 카테고리에만 존재(아래 표의 "필수" 열 조건 참조).

---

## 1. `market_news.game.jsonl` — 거시/시장 뉴스

지수·금리·유가·환율 같은 **매크로 지표 변동**과 **섹터(업종) 등락**을 다룬다. 특정 종목이 아니라 시장 전체/업종에 영향.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `news_id` | string | ✓ | 고유 ID. 예: `market__2013-01-03__sector__1948`, `market__2013-01-07__macro__0` |
| `category` | enum | ✓ | `market_sector` (업종 등락) \| `market_macro` (매크로 지표) |
| `publish_date` | date | ✓ | 원본 사건 발생일 `YYYY-MM-DD` |
| `game_publish_date` | date | ✓ | 게임 노출일 (거래일 보정 적용, §5) |
| `event_type` | enum | ✓ | `sector_leader`, `sector_laggard`, `rate_move`, `spread_move`, `market_close_move`, `oil_move`, `fx_move`, `safe_asset_move` |
| `direction` | enum | ✓ | `positive` \| `negative` \| `neutral` — 호재/악재/중립 |
| `strength` | int | ✓ | 뉴스 강도. 현재 데이터는 **4 또는 5**(클수록 강함). 게임에서 색/배지/영향계수에 활용 가능 |
| `news_lines` | string[] | ✓ | 출력용 기사 문장 |
| `market` | enum | `market_sector`일 때 | `KOSPI` \| `KOSDAQ` |
| `sector` | string | `market_sector`일 때 | 업종명. 예: `운송장비·부품` |
| `asset_id` | string | `market_macro`일 때 | 지표명. 예: `원/달러 환율`, `미국 국채 10년 금리`, `Dubai 유가`, `NASDAQ` |

**카테고리별 구성**: `market_sector` 7,221건 / `market_macro` 1,948건.
**event_type 분포**: sector_leader 3,857 · sector_laggard 3,364 · rate_move 512 · spread_move 472 · market_close_move 463 · oil_move 352 · fx_move 108 · safe_asset_move 41.

```jsonc
// market_sector 예시
{"news_id":"market__2013-01-03__sector__1948","category":"market_sector","publish_date":"2013-01-03",
 "market":"KOSDAQ","sector":"운송장비·부품","event_type":"sector_laggard","direction":"negative","strength":4,
 "news_lines":["KOSDAQ 운송장비·부품 업종이 시장 내 수익률 하위권을 기록했다 (시장 대비 -1.96%p)."],
 "game_publish_date":"2013-01-03"}
// market_macro 예시 (asset_id 존재, market/sector 없음)
{"news_id":"market__2013-01-07__macro__0","category":"market_macro","publish_date":"2013-01-07",
 "asset_id":"원/달러 환율","event_type":"fx_move","direction":"negative","strength":5,
 "news_lines":["원/달러 환율이 전일 대비 2.71% 내렸다."],"game_publish_date":"2013-01-07"}
```

---

## 2. `stock_news.game.jsonl` — 개별종목 공시 뉴스

특정 상장사의 **공시(실적·계약·배당·투자 등)**를 게임 기사로 변환. 종목 화면/종목별 뉴스 피드에 사용.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `news_id` | string | ✓ | 고유 ID. 예: `stock_news_sample_001` |
| `category` | const | ✓ | 항상 `stock_disclosure` |
| `bundle_id` | string | ✓ | 원본 공시 번들 ID. 예: `STOCK_BUNDLE_001144` |
| `stock_code` | string | ✓ | 6자리 종목코드(문자열, 앞자리 0 보존). 예: `006400` |
| `stock_name` | string | ✓ | 종목명. 예: `삼성SDI` |
| `event_family` | enum | ✓ | `earnings`, `contract`, `dividend`, `investment`, `asset_transaction` |
| `news_type` | const | ✓ | 항상 `corporate_action_disclosure` |
| `claim_level` | enum | ✓ | `no_market_claim` (시장반응 언급 없음) \| `market_reaction_adjacency` (공시+익일 주가반응 포함) |
| `publish_date` | date | ✓ | 공시일 |
| `game_publish_date` | date | ✓ | 게임 노출일 |
| `news_lines` | string[] | ✓ | 출력용 문장. `market_reaction_adjacency`면 2문장(공시→주가반응)인 경우가 많음 |

**event_family 분포**: earnings 762 · contract 616 · dividend 173 · investment 72 · asset_transaction 6.
**claim_level 분포**: no_market_claim 1,221 · market_reaction_adjacency 408.

```jsonc
{"news_id":"stock_news_sample_001","category":"stock_disclosure","bundle_id":"STOCK_BUNDLE_001144",
 "stock_code":"006400","stock_name":"삼성SDI","event_family":"dividend",
 "news_type":"corporate_action_disclosure","claim_level":"no_market_claim","publish_date":"2013-01-23",
 "news_lines":["삼성SDI의 매출액은 약 4조9,078억원으로 공시됐다."],"game_publish_date":"2013-01-23"}
```

> **종목 매칭**: `stock_code`로 게임 종목 마스터와 조인. 한 종목에 여러 뉴스가 다른 날짜로 붙는다.

---

## 3. `annual_earnings_news.game.jsonl` — 연간 실적 뉴스

종목별 **사업연도 결산 실적**(매출/영업이익/순이익) 한 줄 요약. 실적 시즌 노출용.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `news_id` | string | ✓ | 예: `annual__006400__2012` (종목코드+사업연도) |
| `category` | const | ✓ | 항상 `annual_earnings` |
| `stock_code` | string | ✓ | 6자리 종목코드 |
| `stock_name` | string | ✓ | 종목명 |
| `business_year` | int | ✓ | 실적 대상 사업연도. 예: `2012` |
| `publish_date` | date | ✓ | 발표/공시 기준일 |
| `game_publish_date` | date | ✓ | 게임 노출일 |
| `date_basis` | enum | ✓ | 발행일 산출 근거: `filing`(공시접수) \| `disclosure` \| `estimated`(추정) |
| `fs_div` | enum | ✓ | 재무제표 구분: `연결` \| `별도` \| `""`(미상) |
| `news_lines` | string[] | ✓ | 출력용 실적 문장 |

**date_basis 분포**: filing 845 · disclosure 186 · estimated 1. · **fs_div 분포**: 연결 802 · "" 186 · 별도 44.

```jsonc
{"news_id":"annual__006400__2012","category":"annual_earnings","stock_code":"006400","stock_name":"삼성SDI",
 "business_year":2012,"publish_date":"2013-01-23","date_basis":"disclosure","fs_div":"",
 "news_lines":["삼성SDI는 2012년 매출액 약 4조9,078억원, 영업이익 약 582억원, 당기순이익 약 2조5,472억원을 기록했다."],
 "game_publish_date":"2013-01-23"}
```

> `business_year`는 **실적 대상 연도**, `publish_date`는 **발표일**(보통 이듬해 1~3월). 둘은 다르다.

---

## 4. `split_articles.game.jsonl` — 분할 기사 (공시 → 후속 반응)

하나의 공시 사건을 **공시 기사 + 며칠 뒤 시장반응 후속 기사**의 2부작으로 쪼갠 것. "기사체+캘린더" 재설계 산출물.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `article_id` | string | ✓ | 기사 고유 ID. 예: `stock_news__STOCK_BUNDLE_006570__disclosure` |
| `source_custom_id` | string | ✓ | 원본 묶음 ID. 예: `stock_news__STOCK_BUNDLE_006570` |
| `category` | const | ✓ | 항상 `split_article` |
| `article_type` | enum | ✓ | `disclosure`(공시 기사) \| `market_reaction_followup`(후속 반응 기사) |
| `stock_code` | string | ✓ | 6자리 종목코드 |
| `stock_name` | string | ✓ | 종목명 |
| `event_family` | enum | ✓ | `earnings`, `contract`, `dividend`, `investment` |
| `source_rcept_no` | string | ✓ | DART 공시 접수번호(추적용). 예: `20130125800135` |
| `publish_date` | date | ✓ | 기사 발행일 |
| `game_publish_date` | date | ✓ | 게임 노출일 |
| `news_lines` | string[] | ✓ | 출력용 문장 |
| `material_reason` | string | `market_reaction_followup`일 때 | 후속 기사 발행 사유(주가반응 임계). 예: `ret1d>=5.0` |

**article_type 분포**: disclosure 397 · market_reaction_followup 397 (1:1 쌍). · **event_family**: earnings 478 · contract 202 · dividend 80 · investment 34.

```jsonc
// 1부: 공시 기사
{"source_custom_id":"stock_news__STOCK_BUNDLE_006570","stock_code":"086280","stock_name":"현대글로비스",
 "event_family":"earnings","source_rcept_no":"20130125800135",
 "article_id":"stock_news__STOCK_BUNDLE_006570__disclosure","article_type":"disclosure","publish_date":"2013-01-25",
 "news_lines":["현대글로비스는 2012년 매출액 약 9조2,729억원, 영업이익 약 4,229억원, 당기순이익 약 4,061억원을 기록했다고 1월 25일 공시했다."],
 "category":"split_article","game_publish_date":"2013-01-25"}
// 2부: 며칠 뒤 시장반응 후속 기사 (같은 source_custom_id, article_id만 __reaction)
{"source_custom_id":"stock_news__STOCK_BUNDLE_006570","stock_code":"086280","stock_name":"현대글로비스",
 "event_family":"earnings","source_rcept_no":"20130125800135",
 "article_id":"stock_news__STOCK_BUNDLE_006570__reaction","article_type":"market_reaction_followup","publish_date":"2013-01-28",
 "news_lines":["현대글로비스는 2012년 매출액 약 9조2,729억원, 영업이익 약 4,229억원의 실적을 지난 1월 25일 발표했다. 다음 거래일 주가는 5.82% 올랐고, 같은 날 유통 업종지수는 0.94% 상승했다."],
 "material_reason":"ret1d>=5.0","category":"split_article","game_publish_date":"2013-01-28"}
```

> 같은 `source_custom_id`로 1·2부를 묶을 수 있고, `article_id` 접미사(`__disclosure`/`__reaction`)로 구분한다.

---

## 5. 게임 발행일·턴 정렬 규칙 (중요)

뉴스를 턴에 배치할 때는 **`publish_date`가 아니라 `game_publish_date`를 기준**으로 한다. 파이프라인이 거래일 달력에 맞춰 이미 보정해 둔 값이다.

- 거래일 달력: KOSPI ∪ KOSDAQ 기준 약 2,702 거래일.
- 보정 규칙: **토요일 → +2일(월), 일요일 → +1일(월), 평일(휴장일 포함) → 유지.** (게임은 평일 휴장일에도 열림)
- 따라서 `game_publish_date`에는 **주말이 없다**. 평일 휴장일에 걸린 소수 건은 그대로 둔다(리포트상 "휴장일 유지").

**게임 측 권장 소비 패턴**
1. 4개 파일을 모두 읽어 한 리스트로 합친다(각 객체에 `category`가 있어 구분 가능).
2. `game_publish_date`로 인덱싱(`Map<date, News[]>`).
3. 턴의 거래일이 정해지면 해당 날짜 버킷의 뉴스를 노출. 거시(`market_*`)는 전체 시장 화면에, 개별/실적/분할 기사는 해당 `stock_code` 종목 화면에 라우팅.
4. 호재/악재 연출은 `direction`, 강조도는 `strength`(거시) 활용.

```js
// 예: 줄 단위 JSONL 로더 (참고용)
const byDate = new Map();
for (const line of text.split("\n")) {
  if (!line.trim()) continue;
  const n = JSON.parse(line);
  if (!byDate.has(n.game_publish_date)) byDate.set(n.game_publish_date, []);
  byDate.get(n.game_publish_date).push(n);
}
```

---

## 6. 데이터 위치 · 상태

### 실제 데이터 파일 (Google Drive)
실제 `*.game.jsonl` 4종은 용량 때문에 git에 올리지 않고 **Google Drive**에 둔다 (팀 공용 Drive `interim/news_generator` 하위):

📁 **`game_news_data/`** — https://drive.google.com/drive/folders/1DWObrtb_eFfSj6h_AqP3QZvDLplUsYp1

폴더 내용:
- `market_news.game.jsonl` (거시) · `stock_news.game.jsonl` (개별) · `annual_earnings_news.game.jsonl` (연간실적) · `split_articles.game.jsonl` (분할기사)
- `calendar_alignment_report.md` · `calendar_alignment_summary.json` — 거래일 정렬 검증 리포트

> 링크가 안 열리면 팀 공용 Drive 접근 권한을 요청할 것(데이터 담당). 파일은 전부 UTF-8 JSONL.

### 상태/주의
- **거시뉴스(`market_news`) 전기간 재생성이 진행 중**: 정책·법·정치 발표 레이어를 반영한 재생성이 끝나면 **본문 내용과 건수가 갱신**된다. 다만 위 **스키마(필드 구조)는 바뀌지 않는다** — 게임 로더는 안심하고 이 계약에 맞춰 구현하면 된다. 갱신 시 Drive 폴더의 파일만 교체된다.
- 현재 게임 프로토타입의 `src/data/gameData.json`은 `news:{title,summary}` 형태의 **임시 플레이스홀더**다. 위 실데이터는 `news_lines:string[]` 구조이므로, 어댑터(예: `title = news_lines[0]`, `body = news_lines.join("\n")`)를 두거나 뉴스 컴포넌트를 `news_lines` 기준으로 일반화하는 것을 권장.

## 7. 변경 이력
- 2026-06-24: 최초 작성 (market/stock/annual/split 4종 스키마 + 거래일 정렬 규칙). 실데이터는 Drive `game_news_data/`에 업로드.
