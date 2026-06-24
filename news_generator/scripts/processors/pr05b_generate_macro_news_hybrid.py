#!/usr/bin/env python3
"""
pr05b — 거시뉴스 하이브리드 생성기 (Batch 1차 + 동기 재시도 폴백)

목적
----
거시뉴스의 품질 게이트(무조작·5건 고정·길이·초보용어 커버리지)를 그대로 유지하면서
비용을 절반 가까이 줄이는 하이브리드 파이프라인.

- Phase 1 (Batch, -50%): 대상 일자 전체를 OpenAI Batch API로 1차 생성한다.
  완료되면 각 일자 출력을 pr05의 검증(_validate_batch_event_coverage)·후처리
  (_postprocess_news_items)에 "그대로" 통과시킨다. 통과분만 채택한다.
- Phase 2 (Sync 재시도): 배치에서 검증 실패하거나 배치 자체 에러가 난 일자만
  pr05의 동기 retry-with-feedback 루프(_generate_one_day)로 재생성한다.
  이 루프는 실패 사유를 다음 프롬프트에 되먹여 자기교정하는, 섬세함을 지키는 게이트다.
- 최종: 1차 통과분 + 재시도 복구분을 병합해 CSV로 저장. 끝내 실패한 일자만 fail_log.

품질 동등성
-----------
배치/동기는 같은 모델·temperature·프롬프트·response_format을 쓴다(아래 모두 pr05 재사용).
따라서 기사 한 건의 품질·뉘앙스는 동일하며, 배치가 떨어뜨리는 것은 "글의 질"이 아니라
"인라인 자기교정 루프"뿐이다. 그 루프는 Phase 2에서 실패 일자에 한해 동일하게 적용된다.

중단·재개
---------
--work-dir 아래에 배치 요청 파일/배치 ID/원본 출력을 저장한다. 각 단계는 멱등적이라
같은 명령을 다시 실행하면 끊긴 지점부터 이어간다(완료된 배치를 다시 제출하지 않는다).
폴링이 --max-wait-sec를 넘으면 상태만 저장하고 종료하며, 재실행하면 폴링을 재개한다.

사용 예 (연도별 — 기존 pr05 순차 흐름과 동일한 패턴)
----------------------------------------------------
  set -a; . ./.env; set +a
  OUT=data/interim/macro_news_policy_legal_regen
  for Y in 2013 2014 ... 2023; do
    CSV=$OUT/gen_${Y}.csv
    [ -f "$CSV" ] && { echo "skip $Y"; continue; }
    python3 scripts/processors/pr05b_generate_macro_news_hybrid.py \
      --input-jsonl $OUT/with_outlook.jsonl \
      --output-csv  $CSV \
      --fail-log-path $OUT/fail_${Y}.csv \
      --work-dir    $OUT/batch_${Y} \
      --model gpt-4o \
      --start-date ${Y}-01-01 --end-date ${Y}-12-31
  done

연도별로 끊으면 배치 1건당 ~250요청으로 파일 크기/큐 한도에 안전하고, 각 연도가
독립적으로 재개된다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 같은 디렉터리의 pr05를 모듈로 재사용 (프롬프트·검증·후처리·저장 100% 동일)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pr05_generate_macro_news_from_llm as pr05  # noqa: E402


BATCH_ENDPOINT = "/v1/chat/completions"
TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled", "cancelling"}


# ============================================================
# 상태 파일 (work-dir)
# ============================================================

class HybridState:
    """work-dir에 배치 진행 상태를 저장/복원해 중단·재개를 보장한다."""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.path = work_dir / "state.json"
        self.requests_path = work_dir / "batch_requests.jsonl"
        self.output_path = work_dir / "batch_output.jsonl"
        self.error_path = work_dir / "batch_error.jsonl"
        self.data: Dict[str, Any] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, **kwargs: Any) -> None:
        self.data.update(kwargs)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ============================================================
# pr05 재사용 헬퍼
# ============================================================

def parsed_news_or_raise(
    gen: "pr05.MacroNewsGenerator",
    content: Optional[str],
    record: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """배치 응답 content를 pr05 동기 경로와 동일하게 파싱·검증한다.

    (_generate_one_day 의 응답 처리부와 동일한 순서: json -> news 키 -> list -> 게이트)
    """
    if content is None:
        raise ValueError("LLM 응답 content가 None입니다.")
    parsed = json.loads(content)
    if "news" not in parsed:
        raise ValueError("응답에 news 키가 없습니다.")
    if not isinstance(parsed["news"], list):
        raise ValueError("news가 list가 아닙니다.")
    gen._validate_batch_event_coverage(parsed["news"], record)  # 게이트(검증·정제)
    return parsed["news"]


def build_batch_request(
    gen: "pr05.MacroNewsGenerator",
    custom_id: str,
    system_prompt: str,
    record: Dict[str, Any],
) -> Dict[str, Any]:
    """pr05 동기 첫 시도와 동일한 본문으로 Batch 요청 한 줄을 만든다."""
    user_prompt = gen.prompt_builder.build_user_prompt(record)
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": BATCH_ENDPOINT,
        "body": {
            "model": gen.config.model,
            "temperature": gen.config.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        },
    }


def extract_content(result: Dict[str, Any]) -> Optional[str]:
    """배치 출력 한 줄에서 completion content를 꺼낸다. 실패 시 None."""
    resp = result.get("response") or {}
    if resp.get("status_code") != 200:
        return None
    body = resp.get("body") or {}
    choices = body.get("choices") or []
    if not choices:
        return None
    return (choices[0].get("message") or {}).get("content")


# ============================================================
# Phase 1 — Batch
# ============================================================

def run_batch_phase(
    gen: "pr05.MacroNewsGenerator",
    records: List[Dict[str, Any]],
    state: HybridState,
    poll_interval: float,
    max_wait_sec: float,
    completion_window: str,
) -> Optional[Dict[str, str]]:
    """배치를 제출·폴링·다운로드한다.

    반환: custom_id(=인덱스 문자열) -> content 매핑. 폴링이 시간 내 끝나지 않으면 None
    (상태 저장 후 호출측에서 종료).
    """
    client = gen.client

    # 1) 요청 파일 빌드 (멱등)
    if not state.requests_path.exists():
        system_prompt = gen.prompt_builder.build_system_prompt()
        state.work_dir.mkdir(parents=True, exist_ok=True)
        with open(state.requests_path, "w", encoding="utf-8") as f:
            for idx, record in enumerate(records):
                req = build_batch_request(gen, str(idx), system_prompt, record)
                f.write(json.dumps(req, ensure_ascii=False) + "\n")
        print(f"  [batch] 요청 파일 작성: {state.requests_path} ({len(records)}건)")
    else:
        print(f"  [batch] 기존 요청 파일 재사용: {state.requests_path}")

    # 2) 파일 업로드 (멱등)
    input_file_id = state.get("input_file_id")
    if not input_file_id:
        with open(state.requests_path, "rb") as f:
            up = client.files.create(file=f, purpose="batch")
        input_file_id = up.id
        state.set(input_file_id=input_file_id)
        print(f"  [batch] 입력 파일 업로드: {input_file_id}")

    # 3) 배치 생성 (멱등)
    batch_id = state.get("batch_id")
    if not batch_id:
        batch = client.batches.create(
            input_file_id=input_file_id,
            endpoint=BATCH_ENDPOINT,
            completion_window=completion_window,
        )
        batch_id = batch.id
        state.set(batch_id=batch_id, batch_status=batch.status)
        print(f"  [batch] 배치 생성: {batch_id} (status={batch.status})")
    else:
        print(f"  [batch] 기존 배치 재개: {batch_id}")

    # 4) 폴링
    waited = 0.0
    status = state.get("batch_status", "")
    output_file_id = None
    error_file_id = None
    while True:
        try:
            batch = client.batches.retrieve(batch_id)
        except Exception as e:  # 일시적 네트워크 오류는 계속 폴링
            print(f"  [batch] 조회 일시 오류, 재시도: {e}")
            time.sleep(poll_interval)
            waited += poll_interval
            if waited >= max_wait_sec:
                return None
            continue

        status = batch.status
        counts = getattr(batch, "request_counts", None)
        done = getattr(counts, "completed", "?") if counts else "?"
        total = getattr(counts, "total", "?") if counts else "?"
        failed = getattr(counts, "failed", "?") if counts else "?"
        state.set(batch_status=status)
        print(
            f"  [batch] status={status} 진행 {done}/{total} (failed={failed}) "
            f"elapsed={int(waited)}s"
        )

        if status in TERMINAL_STATUSES:
            output_file_id = getattr(batch, "output_file_id", None)
            error_file_id = getattr(batch, "error_file_id", None)
            break

        if waited >= max_wait_sec:
            print(
                f"  [batch] --max-wait-sec({max_wait_sec}s) 초과. 상태만 저장하고 종료. "
                f"같은 명령을 다시 실행하면 폴링을 재개합니다."
            )
            return None

        time.sleep(poll_interval)
        waited += poll_interval

    if status != "completed":
        print(f"  [batch] 배치 종료 상태={status} (정상 완료 아님).")
        # 부분 출력이라도 있으면 활용
    state.set(batch_status=status, output_file_id=output_file_id,
              error_file_id=error_file_id)

    # 5) 출력 다운로드 (멱등)
    if output_file_id and not state.output_path.exists():
        text = client.files.content(output_file_id).text
        state.output_path.write_text(text, encoding="utf-8")
        print(f"  [batch] 출력 다운로드: {state.output_path}")
    if error_file_id and not state.error_path.exists():
        try:
            etext = client.files.content(error_file_id).text
            state.error_path.write_text(etext, encoding="utf-8")
            print(f"  [batch] 에러 파일 다운로드: {state.error_path}")
        except Exception as e:
            print(f"  [batch] 에러 파일 다운로드 실패(무시): {e}")

    # 6) custom_id -> content 매핑
    mapping: Dict[str, str] = {}
    if state.output_path.exists():
        for line in state.output_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            result = json.loads(line)
            cid = str(result.get("custom_id"))
            content = extract_content(result)
            if content is not None:
                mapping[cid] = content
    return mapping


# ============================================================
# 오케스트레이션
# ============================================================

def run_hybrid(config: "pr05.MacroNewsGenerateConfig", args: argparse.Namespace) -> None:
    gen = pr05.MacroNewsGenerator(config)
    records = gen._filter_records(gen._load_jsonl())

    config.output_csv.parent.mkdir(parents=True, exist_ok=True)
    config.fail_log_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(args.work_dir)
    state = HybridState(work_dir)

    print("=" * 100)
    print("[pr05b 하이브리드 시작] Batch 1차 + 동기 재시도")
    print(f"input_jsonl : {config.input_jsonl}")
    print(f"output_csv  : {config.output_csv}")
    print(f"work_dir    : {work_dir}")
    print(f"model       : {config.model} | temperature : {config.temperature}")
    print(f"days        : {len(records)}")
    print("=" * 100)

    if not records:
        print("처리할 일자가 없습니다(필터 결과 0건). 종료.")
        gen._save_news_csv([])
        gen._save_fail_log([])
        return

    # ---- Phase 1: Batch ----
    print("[Phase 1] Batch 생성/폴링/다운로드")
    mapping = run_batch_phase(
        gen, records, state,
        poll_interval=args.poll_interval,
        max_wait_sec=args.max_wait_sec,
        completion_window=args.completion_window,
    )
    if mapping is None:
        print("배치가 아직 완료되지 않았습니다. 나중에 같은 명령으로 재실행하세요.")
        sys.exit(0)

    # ---- Phase 1 검증/후처리 ----
    all_rows: List[Dict[str, Any]] = []
    failed_indices: List[int] = []
    batch_pass = 0
    for idx, record in enumerate(records):
        date = record.get("date", "UNKNOWN")
        content = mapping.get(str(idx))
        if content is None:
            failed_indices.append(idx)
            continue
        try:
            news_items = parsed_news_or_raise(gen, content, record)
            rows = gen._postprocess_news_items(news_items, record)
            if not rows:
                raise ValueError("후처리 결과 0건")
            all_rows.extend(rows)
            batch_pass += 1
        except Exception as e:
            print(f"  [batch-fail] {date}: {e}")
            failed_indices.append(idx)

    print(f"[Phase 1 결과] 배치 통과 {batch_pass}일 / 재시도 대상 {len(failed_indices)}일")

    # ---- Phase 2: 동기 재시도 (실패 일자만) ----
    fail_rows: List[Dict[str, Any]] = []
    sync_recovered = 0
    if failed_indices and not args.no_sync_fallback:
        print(f"[Phase 2] 동기 retry-with-feedback 재생성: {len(failed_indices)}일")
        for n, idx in enumerate(failed_indices, start=1):
            record = records[idx]
            date = record.get("date", "UNKNOWN")
            print(f"  [{n}/{len(failed_indices)}] {date} 동기 재시도...")
            try:
                news_items = gen._generate_one_day(record)
                rows = gen._postprocess_news_items(news_items, record)
                if not rows:
                    raise ValueError("후처리 결과 0건")
                all_rows.extend(rows)
                sync_recovered += 1
            except Exception as e:
                import traceback as _tb
                print(f"    -> 최종 실패: {date} / {e}")
                fail_rows.append({
                    "date": date, "error": str(e), "traceback": _tb.format_exc(),
                })
            time.sleep(config.sleep_sec)
    elif failed_indices:
        # 폴백 비활성: 실패 일자를 그대로 fail_log로
        for idx in failed_indices:
            date = records[idx].get("date", "UNKNOWN")
            fail_rows.append({"date": date, "error": "batch 실패(동기 폴백 비활성)", "traceback": ""})

    # ---- 저장 (날짜·news_id 정렬) ----
    all_rows.sort(key=lambda r: (r.get("date", ""), r.get("news_id", "")))
    gen._save_news_csv(all_rows)
    gen._save_fail_log(fail_rows)
    state.set(finished=True, batch_pass=batch_pass,
              sync_recovered=sync_recovered, failed=len(fail_rows))

    print("=" * 100)
    print("[pr05b 완료]")
    print(f"총 생성 뉴스 : {len(all_rows)}")
    print(f"배치 1차 통과 : {batch_pass}일")
    print(f"동기 복구    : {sync_recovered}일")
    print(f"최종 실패    : {len(fail_rows)}일")
    print(f"output_csv   : {config.output_csv}")
    print(f"fail_log     : {config.fail_log_path}")
    print("=" * 100)


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="거시뉴스 하이브리드 생성기 (Batch 1차 + 동기 재시도)"
    )
    # pr05와 동일한 입출력/모델 인자
    p.add_argument("--input-jsonl", required=True)
    p.add_argument("--output-csv", required=True)
    p.add_argument(
        "--fail-log-path",
        default="data/processed/llm_generated_macro_news_fail_log.csv",
    )
    p.add_argument("--model", default="gpt-4o")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--sleep-sec", type=float, default=1.0)
    p.add_argument("--limit-days", type=int, default=None)
    p.add_argument("--start-date", default=None)
    p.add_argument("--end-date", default=None)
    p.add_argument("--env-path", default=None)
    # 하이브리드 전용
    p.add_argument(
        "--work-dir", required=True,
        help="배치 요청/ID/원본출력을 저장할 디렉터리(중단·재개 단위).",
    )
    p.add_argument("--poll-interval", type=float, default=30.0,
                   help="배치 상태 폴링 간격(초).")
    p.add_argument("--max-wait-sec", type=float, default=86400.0,
                   help="이 시간 내 배치가 안 끝나면 상태 저장 후 종료(재실행 시 재개).")
    p.add_argument("--completion-window", default="24h")
    p.add_argument("--no-sync-fallback", action="store_true",
                   help="설정 시 배치 실패 일자를 동기 재생성하지 않고 fail_log로만 남김.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    config = pr05.MacroNewsGenerateConfig(
        input_jsonl=Path(args.input_jsonl),
        output_csv=Path(args.output_csv),
        fail_log_path=Path(args.fail_log_path),
        model=args.model,
        temperature=args.temperature,
        max_retries=args.max_retries,
        sleep_sec=args.sleep_sec,
        limit_days=args.limit_days,
        start_date=args.start_date,
        end_date=args.end_date,
        env_path=Path(args.env_path) if args.env_path else None,
    )
    run_hybrid(config, args)


if __name__ == "__main__":
    main()
