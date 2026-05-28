# JOBSIM Model Handoff Log

기준 목적:
- 다른 모델/개발자가 바로 이어서 작업할 수 있도록 현재 상태, 변경 이력, 검증 결과를 한 곳에 유지한다.
- 코드 수정이 발생하면 이 파일도 같이 갱신한다.

갱신 규칙(필수):
1. 코드/설정/데이터 파일 수정 시 이 문서에 항목 추가
2. 각 항목에 최소 포함:
   - 날짜(YYYY-MM-DD)
   - 변경 이유
   - 수정 파일 목록
   - 검증 명령/결과
3. 완료되지 않은 작업은 `Open Items`에 남긴다.

---

## Current Snapshot (2026-05-26)

### Architecture
- Frontend: `app/**` (UI/상태/렌더)
- Backend: `server.ts`, `src/lib/**` (API/평가/추천/계약)
- Mission data: `missions/index.json`, `missions/scenarios/*.json`
- Tests/CI: `tests/**`, `.github/workflows/**`

### Main APIs
- `POST /api/evaluate`
- `POST /api/recommendations`
- `POST /api/logs`
- `GET /api/bootstrap`
- `GET /health`

### Current Scoring Notes
- 사분면 수행점수: `missionScore` 합(0~1)을 평균하여 사용
- 직무 적합도: `cosine similarity -> clamp(0..1) -> gamma(1.5) -> 0~100`

---

## Change History

### 2026-05-26 — 경계/테스트/운영 기본 정리 (기존 반영 상태)
왜:
- 프론트/백엔드/LLM 책임 분리, 회귀 감시 기본선 확보

주요 반영:
- API 계약 스키마 고정 (`src/lib/api/contracts.ts`)
- E2E/평가 안정성/파이프라인 CI 정리
- 운영 템플릿(PM2/Docker/.env 분리) 정리

관련 파일:
- `server.ts`
- `src/lib/api/contracts.ts`
- `.github/workflows/e2e.yml`
- `.github/workflows/eval-stability.yml`
- `.github/workflows/pipeline-sanity.yml`
- `docs/ARCHITECTURE_BOUNDARIES.md`
- `docs/OPERATIONS.md`

---

### 2026-05-26 — 미션 파일명 규칙 통일 + 충돌 방지
왜:
- `pilot_v1_...` 형식 제거, 시나리오 파일명 단순화
- 동일 직무코드의 복수 미션 충돌 방지 필요

반영:
- `missions/scenarios` 파일명 통일:
  - `K000000997_01.json`, `K000000997_02.json`
  - `K000001080_01.json`, `K000001080_02.json`
  - `K000001179_01.json`, `K000007519_01.json`
- `missions/index.json` 경로 동기화

중요:
- `K000000997.json` 단일 형식은 복수 미션에서 파일명 충돌 위험이 있으므로 미사용

검증:
- `npm run typecheck` 통과
- `npm run test:e2e` 통과

---

### 2026-05-26 — 사분면 수행점수 고정 버그 수정
왜:
- 수행점수가 구조적으로 낮게 고정되어 q1/q2 판별이 왜곡됨

원인:
- 미션 점수(이미 0~1)를 다시 축 개수(5)로 나눔

반영:
- `computePerformanceByJob`에서 불필요 `/5` 제거
- 범위 클램프(0~1) 적용

관련 파일:
- `src/lib/recommendation/engine.ts`

검증:
- `npm run typecheck` 통과

---

### 2026-05-26 — 직무 적합도 점수 과낙관 스케일 보정
왜:
- 기존 `((sim+1)/2)*100`은 하한이 높아 체감상 과대평가 경향

반영:
- 점수 변환을 `sim*100` 계열로 변경
- 감마 보정 적용: `score = (clamp(sim,0,1)^1.5)*100`
- 추천 카드 유사도%도 동일 스케일로 통일

관련 파일:
- `src/lib/recommendation/engine.ts`
- `app/components/resultDashboard.js`

검증:
- `npm run typecheck` 통과
- `npm run test:e2e` 통과

---

### 2026-05-26 — 동일 mission_id 충돌 방지 (self-report/log)
왜:
- 동일 `mission_id`가 2개 이상 존재해 self-report/로그가 섞일 위험

반영:
- self-report 키를 `mission_id` 단독에서 `mission.key` 우선으로 변경
- 로그에 `mission_key` 필드 추가 저장
- API 계약에 `mission_key` 허용 추가

관련 파일:
- `app/state/flowController.js`
- `app/components/missionScreens.js`
- `src/lib/api/contracts.ts`

검증:
- `npm run typecheck` 통과
- `npm run test:e2e` 통과

---

### 2026-05-26 — P1 안정화(임시결과 표시/escaping/저정보 방어)
왜:
- fallback 결과 신뢰도 혼동, XSS/레이아웃 리스크, 반복 장문 방어 부족

반영 1) fallback/임시결과 신뢰도 표기
- `evaluation.source` 도입: `llm | heuristic | unavailable | precheck`
- non-LLM 평가 시 점수 상한 1, confidence 상한 0.45
- 결과 화면에 "임시 추정 결과" 배지 노출
- non-LLM만 있을 때 근거 카드 생략

반영 2) escaping 보강
- 사전문항 textarea 값 escape 적용
- gap 섹션(`사전 예상/기대 대비`) escape 적용

반영 3) 저정보 답변 방어
- 반복 trigram/unique ratio/문자반복 기반 `isLowInformationAnswer` 추가
- 해당 케이스는 precheck 경로로 보수 처리
- golden 케이스 2개 추가(반복 장문, gibberish 장문)

관련 파일:
- `src/lib/evaluator/schema.ts`
- `src/lib/evaluator/evaluateAnswer.ts`
- `app/domain/evaluationWorkflow.js`
- `app/main.js`
- `app/components/resultDashboard.js`
- `app/components/missionScreens.js`
- `tests/golden-evaluations.json`

검증:
- `npm run typecheck` 통과
- `npm run pipeline:check` 통과
- `npm run test:e2e` 통과 (4 passed)
- `npm run eval:stability -- --runs=1 --out=reports/eval-stability-local.json --no-fail` 통과 (8 passed)

---

### 2026-05-26 — 적합도 분포 점검 스크립트 추가
왜:
- 감마값 조정을 로그 분포 기반으로 빠르게 판단하기 위해

반영:
- `scripts/analyze-compatibility.mjs` 추가
- npm 스크립트 `analyze:compat` 추가

사용:
- `npm run analyze:compat`
- `npm run analyze:compat -- --gamma=1.3`
- `npm run analyze:compat -- --gamma=1.8`

관련 파일:
- `scripts/analyze-compatibility.mjs`
- `package.json`

---

### 2026-05-26 — 문자 깨짐(혼합 스크립트) 정정
왜:
- 일부 한국어 텍스트에서 `분석`의 `석`이 한자로 치환되어 표시되는 문제 수정

반영:
- 한자 혼입 형태를 모두 `분석`으로 정규화
- 관련 파이프라인 설명/라벨 텍스트 일괄 정정

관련 파일:
- `pipeline/03_factor_analysis.py`
- `pipeline/tune_temperature.py`

검증:
- 소스 범위(`app/src/pipeline/tests/docs/missions/scripts`)에서
  한자 혼입/비의도 스크립트 패턴 재검색 시 미검출
- `npm run pipeline:check` 통과

---

### 2026-05-26 — 감사 P1 우선항목 1차 반영
왜:
- 운영 즉시 위험(P1) 항목부터 선반영해 비용/가용성/집계 정확도 리스크를 줄이기 위해

반영 1) `/api/evaluate` 보호 강화
- `answer` 길이 상한 적용 (`max 3000`)
- `/api/evaluate` IP 기준 레이트리밋 추가 (기본 분당 10회)
- 선택형 API 토큰 인증 추가 (`API_SHARED_TOKEN` 설정 시 `x-api-token`/Bearer 필수)

반영 2) 로그 운영성 개선
- 평가 로그를 일자별 파일로 분리 저장:
  `reports/evaluation-logs-YYYY-MM-DD.jsonl`
- 로그 보관일 기반 정리(기본 14일)
- `docker-compose.yml`에 `./reports:/app/reports` 볼륨 추가
- `analyze:compat`가 일자별 로그 파일들을 자동 집계하도록 수정

반영 3) 결과 정확도/식별자 개선
- 추천엔진 이중 정규화 제거 (`similarityProfile` 재정규화 제거)
- `mission_id`를 배치 suffix 포함 값으로 전부 유니크화
  (중복 집계/매칭 충돌 방지)
- non-LLM cap 정책 조정:
  - `heuristic`: score cap 2 / confidence cap 0.55
  - `precheck`, `unavailable`: score cap 1 / confidence cap 0.45

반영 4) 실패 로그 누락 방지
- 미션 채점 Promise reject 시에도 unavailable 평가 로그와 0점 mission_score를 저장
- 재시작 시 로컬 평가 로그 캐시(`jobsim:evaluation_logs:v1`) 삭제

반영 5) 모델 검증 보조
- `ops:model-check` 스크립트 추가 (`OPENAI_EVAL_MODEL` 실제 사용 가능 여부 확인)
- `openai` 의존성 버전을 `latest`에서 `^6.39.0`으로 고정

관련 파일:
- `server.ts`
- `src/lib/api/contracts.ts`
- `src/lib/evaluator/evaluateAnswer.ts`
- `src/lib/recommendation/engine.ts`
- `app/state/flowController.js`
- `app/api-client/jobsimApi.js`
- `missions/index.json`
- `missions/scenarios/*.json`
- `scripts/analyze-compatibility.mjs`
- `scripts/ops-model-check.mjs`
- `scripts/eval-stability.ts`
- `docker-compose.yml`
- `package.json`, `package-lock.json`
- `.env.example`, `.env.server.example`, `ecosystem.config.cjs`
- `docs/OPERATIONS.md`, `README.md`, `ANALYSIS.md`

검증:
- `npm run typecheck` 통과
- `npm run test:e2e` 통과 (4 passed)
- `npm run eval:stability -- --runs=1 --out=reports/eval-stability-local.json --no-fail` 통과 (8 passed)
- `npm run pipeline:check` 통과

---

### 2026-05-26 — prompt injection 탐지 + bootstrap cluster_id 정합화
왜:
- 감사 항목 후속으로 보안/데이터 일관성 잔여 이슈를 즉시 처리하기 위해

반영 1) prompt injection 실탐지 적용
- 답변 텍스트에 인젝션 시그널(예: `ignore previous instructions`, role 태그, 점수 강제 지시) 탐지 로직 추가
- 탐지 시 `precheck`로 전환해 `possible_prompt_injection` 플래그와 보수적 점수(축별 0~1) 적용
- LLM이 `possible_prompt_injection` 플래그를 반환한 경우에도 후처리에서 점수/신뢰도 강제 캡
- 프롬프트에 보안 지시 보강: 사용자 답변을 `UNTRUSTED` JSON 문자열로 전달

반영 2) bootstrap `cluster_id: "UNKNOWN"` 제거
- 서버 bootstrap에서 `data/processed/job_weights.json`을 로드해 `job_code -> cluster_id` 매핑 적용
- `allMissions`, `jobDefs` 모두 실제 cluster_id 채움
- 정적 fallback 로더(`app/domain/missionsCatalog.js`)도 동일하게 cluster_id를 로드해 일관성 유지

반영 3) 회귀 테스트 강화
- golden 테스트에 `prompt_injection_attempt` 케이스 추가
- `allowFlags` 검증 로직을 eval-stability 스크립트에 반영해 필수 플래그 누락도 실패 처리

관련 파일:
- `src/lib/evaluator/evaluateAnswer.ts`
- `src/lib/evaluator/prompts.ts`
- `src/lib/bootstrap/missionBootstrap.ts`
- `app/domain/missionsCatalog.js`
- `tests/golden-evaluations.json`
- `scripts/eval-stability.ts`

검증:
- `npm run typecheck` 통과
- `npm run eval:stability -- --runs=1 --out=reports/eval-stability-local.json --no-fail` 통과 (9 passed)
- `npm run test:e2e` 통과 (4 passed)
- `npm run pipeline:check` 통과

---

### 2026-05-27 — 분석용 시뮬레이션 보고서 추가 (코드 변경 없음)
왜:
- 사용자 요청에 따라 코드 수정 없이 통계 시뮬레이션을 확장 실행하고 보고서 형태로 정리하기 위해

반영:
- `k=2~10` 확장 KMeans 안정성 실험
- GMM(`k=2~10`, covariance 4종) 비교
- PCA+HDBSCAN 그리드 실험(UMAP 대안)
- 3요인 내재화 시 기존 AX1~AX5 매핑(절대 로딩 기여율 기반) 산출

산출물:
- `reports/simulation_report_2026-05-27.md`
- `reports/simulation_report_2026-05-27.json`

참고:
- 본 단계는 분석 산출물 생성만 수행했으며 앱/서버 로직 코드는 수정하지 않음

---

### 2026-05-27 — LLM 채점 실패(Structured Output) 복구
왜:
- `/api/evaluate`에서 OpenAI parse가 스키마 제약에 걸려 LLM 경로가 실패하고 heuristic fallback으로 강등되는 이슈를 복구하기 위해

원인:
- `EvaluationSchema`의 `source`가 optional(`.optional()`)이라 Structured Output parse 제약과 충돌

반영:
- LLM parse 전용 스키마 `LlmEvaluationSchema` 추가 (`source` 제외)
- OpenAI parse는 `LlmEvaluationSchema`로 수행
- parse 성공 후 서버에서 `source: "llm"`을 주입해 기존 평가 타입/계약 유지

관련 파일:
- `src/lib/evaluator/schema.ts`
- `src/lib/evaluator/evaluateAnswer.ts`

검증:
- `npm run typecheck` 통과

---

### 2026-05-27 — 5축 결과 화면 표기 개선(절대 수준 vs 상대 비중 분리)
왜:
- 결과 카드의 퍼센트 표기가 절대점수와 상대비중을 혼용해 해석 혼동이 발생하기 때문

반영:
- 축 카드의 메인 수치 표기를 `점수/4.0` 형태로 변경
- 보조 라인 분리:
  - `절대 수준 x.x/4.0 (yy%)`
  - `상대 비중(5축 내) zz%`
- 레이더 섹션 상단에 해석 안내 문구 추가:
  - 절대 수준(축별 점수)과 상대 비중(합 100 환산) 구분

관련 파일:
- `app/components/resultDashboard.js`
- `app/index.html`

검증:
- `npm run typecheck` 통과

---

### 2026-05-27 — 직무 단일 선택 + 하루 일과 예시 문구 개선
왜:
- 첫 화면에서 다중 선택보다 단일 선택 UX가 요구됨
- 결과 화면의 `하루 일과 예시`가 직무별 차이가 없는 고정형 문구로 보여 정보성이 낮음

반영 1) 직무 선택 단일화
- 직무 선택을 토글형 다중 선택에서 단일 선택으로 변경
- 같은 카드를 다시 누르면 선택 해제, 다른 카드를 누르면 기존 선택 교체
- 첫 화면 안내 문구를 `직무를 1개 선택하세요.`로 변경

반영 2) 하루 일과 예시 생성 로직 개선
- `jobInfo.daily`를 고정 문장에서 직무/미션 기반 동적 문장으로 변경
- 반영 요소:
  - 직무 도메인(`mission_facts.domain`)
  - 대표 미션 제목(최대 2개)
  - 과제 expected_action / instruction 기반 업무 흐름(예: 현황 점검 → 원인 진단 → 개선안 제안)
  - 미션 평균 소요시간
- 서버 bootstrap 경로와 프론트 정적 fallback 경로 모두 동일 로직으로 반영

관련 파일:
- `app/state/flowController.js`
- `app/components/missionScreens.js`
- `app/domain/missionsCatalog.js`
- `src/lib/bootstrap/missionBootstrap.ts`

검증:
- `npm run typecheck` 통과

---

### 2026-05-27 — 결과 화면 `하루 일과 예시` 섹션 제거
왜:
- 결과 카드에서 `하루 일과 예시` 블록이 핵심 해석 대비 효용이 낮아 UI 단순화 요구가 있었기 때문

반영:
- 직무 소개 카드에서 `📅 하루 일과 예시` 블록 제거
- 직무 요약/업무 목록/필요 능력 목록은 유지

관련 파일:
- `app/components/resultDashboard.js`

검증:
- `npm run typecheck` 통과

---

### 2026-05-27 — 레이더 5축을 절대값(축별 0~100) 기준으로 전환
왜:
- 상대비중(합 100) 방식은 일반 사용자에게 해석 난도가 높아, 축별 절대 퍼센트 표시 요구가 있었기 때문

반영:
- 레이더 차트의 내 폴리곤 값을 `evaluationLogs`의 축 점수 평균(0~4)을 0~100으로 환산한 절대값으로 변경
- 축 라벨 퍼센트도 절대값 기준으로 표시
- 카드 문구를 절대값 기준으로 정렬하고, 상대비중 문구 제거
- 기준 직업 점선 폴리곤/증감 화살표는 스케일 혼동 방지를 위해 제거
- 안내 문구를 `각 축 독립 절대값` 설명으로 교체

관련 파일:
- `app/components/resultDashboard.js`

검증:
- `npm run typecheck` 통과

---

### 2026-05-27 — AX4/AX5 미측정 축 처리 + 추천 계산 보정
왜:
- 일부 직무(예: 데이터 분석 계열)에서 AX4/AX5가 미션 설계상 거의 측정되지 않아, 0점 누적처럼 보이는 왜곡을 줄이기 위해

반영:
- 추천 응답에 축 커버리지 추가:
  - `axisCoverage`: 축별 `totalSignal / missionCount / measured`
  - `measuredAxes`: 실제 측정 축 목록
- `mission_key`/`mission_id` 기반으로 미션 `axis_signals`를 서버에서 매칭해 축별 측정 여부 계산
- 측정 축 마스크를 추천 계산에 반영:
  - 사용자 프로파일 정규화 시 미측정 축 제외
  - 코사인 유사도 계산 시 기준 직무 가중치도 동일 축으로 마스킹 후 재정규화
- 인사이트 계산(top/low axis)도 측정 축 기준으로 정렬
- 축 상세 요약에서 미측정 축은 `unmeasured_axis` 플래그로 표기
- 결과 카드에서 미측정 축을 `미측정` 상태로 노출하고 안내 문구 추가

관련 파일:
- `src/lib/recommendation/engine.ts`
- `src/lib/api/contracts.ts`
- `server.ts`
- `app/components/resultDashboard.js`
- `app/domain/evaluationWorkflow.js`
- `app/index.html`

검증:
- `npm run typecheck` 통과
- `npm run test:e2e` 통과 (4 passed)

---

### 2026-05-27 — 미션 마무리 객관식 3문항 제거
왜:
- 미션 종료 흐름 단순화 및 입력 부담 감소 요구 반영

반영:
- 미션 화면의 self-report(재미/난이도/상상) 3문항 UI 제거
- 다음 버튼 활성 조건을 `답변 길이` 기준으로 단순화
- E2E 시나리오를 self-report 없는 플로우로 갱신

관련 파일:
- `app/components/missionScreens.js`
- `app/state/flowController.js`
- `tests/e2e/jobsim-flow.spec.ts`

검증:
- `npm run typecheck` 통과
- `npm run test:e2e` 통과 (4 passed)

---

### 2026-05-27 — self-report 잔여 의존성 제거 + 몰입 추정 보정
왜:
- 미션 마무리 객관식 3문항을 제거한 뒤에도 상태/이벤트/로그에 self-report 잔여 코드가 남아 있었고
- 사분면의 `흥미` 값이 self-report 입력 유무에 따라 왜곡되거나 고정값처럼 보일 수 있어 해석 일관성이 필요했기 때문

반영:
- 프론트 상태/이벤트에서 self-report 의존 제거
  - `state.selfReport` 삭제
  - `.sr-btn` 이벤트 핸들러 삭제
  - 로그 저장 시 `self_report` 송신 제거
- 미션 화면/스타일에서 self-report 관련 잔여 코드 정리
  - self-report UI placeholder 변수 제거
  - 미사용 `.sr-*` CSS 제거
- 사분면 문구를 `흥미` → `몰입 추정`으로 변경
  - 섹션 타이틀: `잘 했나요? 얼마나 몰입했나요?`
  - 인사이트 라벨/디테일 문구도 동일 기준으로 변경
- 서버 추천 엔진의 몰입 계산 보정
  - `self_report.fun`이 있으면 기존 방식 유지
  - 없으면 답변 토큰 수/근거 수/평균 confidence/flags(source 포함) 기반 추정치 사용
- 적합도 스케일 감마를 환경변수로 노출
  - `COMPATIBILITY_SCORE_GAMMA` (기본 1.5)
  - `/health`에 현재 감마값 포함

관련 파일:
- `app/state/store.js`
- `app/state/flowController.js`
- `app/components/missionScreens.js`
- `app/components/resultSections.js`
- `app/domain/evaluationWorkflow.js`
- `app/index.html`
- `tests/e2e/jobsim-flow.spec.ts`
- `src/lib/recommendation/engine.ts`
- `src/lib/api/contracts.ts`
- `server.ts`
- `.env.example`
- `.env.server.example`
- `README.md`

검증:
- `npm run typecheck` 통과
- `npm run test:e2e` 통과 (4 passed)

---

### 2026-05-27 — 직무 소개 문구에서 시나리오 도메인 오염 제거
왜:
- 결과 화면 직무 소개가 `mission_facts.domain`(시뮬레이션 상황 도메인)을 그대로 사용해
  - 예: 투자분석가 설명에 `도심 물류용 배터리 팩 부품` 같은 비직무 문맥이 노출되는 문제가 있었기 때문

반영:
- 직무 소개 `summary` 생성 기준을 변경:
  - 시나리오 `domain` 기반 문구 제거
  - `job_code`별 직무 설명 템플릿 우선 사용
  - 미등록 직무는 `job_mdcl_nm` 기반 일반 fallback 사용
- 서버 bootstrap 경로와 프론트 fallback 경로에 동일 로직 적용

관련 파일:
- `src/lib/bootstrap/missionBootstrap.ts`
- `app/domain/missionsCatalog.js`

검증:
- `npm run typecheck` 통과
- `npm run test:e2e` 통과 (4 passed)

---

## Open Items (Next)

1. `isLowInformationAnswer` 임계치 미세조정
- 현재 값은 보수적 기본값
- 권장: 로그 80줄+ 수집 후 재조정

2. 적합도 감마(1.5) 최종 확정
- 현재는 과낙관 완화 목적 기본값
- `analyze:compat` 결과 기반으로 확정 필요

3. 결과 분석 UX 문구 정교화
- non-LLM 결과일 때 안내 문구를 더 명확하게 다듬을 여지 있음

---

## Quick Verify Commands

```bash
npm run typecheck
npm run pipeline:check
npm run test:e2e
npm run eval:stability -- --runs=1 --out=reports/eval-stability-local.json --no-fail
npm run analyze:compat
```
