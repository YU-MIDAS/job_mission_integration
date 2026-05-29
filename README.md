# JOBSIM Mission Integration

직무 기반 미션을 생성하고, 사용자가 미션을 수행한 뒤 AI 평가 결과를 확인하는 웹 애플리케이션입니다.

프로젝트는 크게 두 흐름으로 나뉩니다.

1. 관리자 화면에서 직무와 난이도를 선택해 미션을 생성합니다.
2. 일반 사용자 화면에서 생성된 미션을 수행하고 결과를 분석합니다.

## 준비물

### 필수 프로그램

- Node.js 20 이상
- npm
- Python 3.11 이상
- OpenAI API key

미션 생성 파이프라인은 Python 표준 라이브러리 중심으로 동작합니다. 일반적인 관리자 미션 생성 기능만 사용할 때는 별도 Python 패키지 설치가 필요하지 않습니다.

### 필수 파일과 폴더

```text
data/api_raw/
data/additional_search/
missions/index.json
missions/scenarios/
.env
```

각 폴더의 역할은 다음과 같습니다.

| 경로 | 역할 |
| --- | --- |
| `data/api_raw/{jobCode}/` | 직무 원천 XML 데이터입니다. 관리자 화면의 직무 목록은 이 폴더를 기준으로 표시됩니다. |
| `data/additional_search/{jobCode}.md` | 미션 생성에 사용할 직무별 추가 조사 자료입니다. 이 파일이 있어야 해당 직무가 `생성가능` 상태가 됩니다. |
| `missions/index.json` | 일반 사용자 화면에 노출할 미션 목록입니다. |
| `missions/scenarios/*.json` | 실제 사용자 화면에서 읽는 미션 시나리오 파일입니다. |
| `outputs/` | 관리자 미션 생성 중 생기는 임시 산출물입니다. |
| `reports/` | 사용자 답변 평가 로그가 저장되는 폴더입니다. |

`data/api_raw`는 용량이 커질 수 있는 원천 데이터이므로, 필요에 따라 로컬에만 두고 커밋하지 않아도 됩니다.

## 환경 설정

처음 실행할 때 `.env.example`을 복사해 `.env`를 만듭니다.

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS 또는 Linux:

```bash
cp .env.example .env
```

`.env`에는 아래 값을 설정합니다.

```env
OPENAI_API_KEY=sk-...
OPENAI_GENERATION_MODEL=gpt-5.4-nano
OPENAI_EVAL_MODEL=gpt-5-nano
API_SHARED_TOKEN=admin-password
COMPATIBILITY_SCORE_GAMMA=1.5
PORT=8080
```

선택 값:

```env
PYTHON_BIN=python
EVALUATION_LOG_RETENTION_DAYS=30
EVALUATE_RATE_LIMIT_WINDOW_MS=60000
EVALUATE_RATE_LIMIT_MAX=30
```

| 환경 변수 | 설명 |
| --- | --- |
| `OPENAI_API_KEY` | 미션 생성과 답변 평가에 사용하는 OpenAI API key입니다. |
| `OPENAI_GENERATION_MODEL` | 관리자 미션 생성과 생성 전 OpenAI preflight에 사용하는 모델입니다. 기본값은 `gpt-5.4-nano`입니다. |
| `OPENAI_EVAL_MODEL` | 사용자 답변 평가 모델입니다. 기본값은 `gpt-5-nano`입니다. |
| `API_SHARED_TOKEN` | 관리자 페이지 접속 암호이자 보호된 API 호출 토큰입니다. |
| `COMPATIBILITY_SCORE_GAMMA` | 직무 추천/적합도 점수 보정값입니다. 기본값은 `1.5`입니다. |
| `PORT` | 로컬 서버 포트입니다. 기본값은 `8080`입니다. |
| `PYTHON_BIN` | 사용할 Python 실행 파일입니다. 생략하면 Windows는 `python`, 그 외 환경은 `python3`를 사용합니다. |

## 설치와 실행

의존성을 설치합니다.

```bash
npm install
```

개발 서버를 실행합니다.

```bash
npm run dev
```

브라우저에서 아래 주소로 접속합니다.

```text
일반 사용자 화면: http://localhost:8080/
관리자 화면: http://localhost:8080/admin.html
```

관리자 화면에 들어갈 때는 `.env`의 `API_SHARED_TOKEN` 값을 입력합니다.

## 관리자 미션 생성 방법

1. `http://localhost:8080/admin.html`에 접속합니다.
2. 관리자 암호를 입력합니다.
3. 왼쪽 직무 목록에서 직무를 선택합니다.
4. 난이도를 선택합니다.
   - 쉬움
   - 보통
   - 어려움
5. `미션 생성` 버튼을 누릅니다. 서버가 먼저 OpenAI API preflight를 실행하며, 실패하면 Python 생성 프로세스를 시작하지 않습니다.
6. 생성 상태 타임라인과 원본 로그를 확인합니다.
7. 하단 미리보기에서 미션 제목, 시나리오, 수행 과제, 참고자료, 신뢰도 점수를 검토합니다.
8. 마음에 들면 `내보내기 승인`을 누릅니다.
9. 마음에 들지 않으면 `생성 결과 삭제`를 누릅니다.

생성 중에는 동시에 하나의 run만 실행할 수 있습니다. 이미 생성 중인 run이 있으면 추가 생성 요청은 거부됩니다.

## 직무를 생성 가능 상태로 만드는 방법

관리자 화면에는 `data/api_raw`에 있는 모든 직무가 표시됩니다. 다만 실제로 미션을 생성하려면 같은 직무 코드의 추가 조사 자료가 필요합니다.

예를 들어 `K000000997` 직무를 생성 가능하게 하려면 아래 두 경로가 준비되어야 합니다.

```text
data/api_raw/K000000997/
data/additional_search/K000000997.md
```

`data/additional_search/{jobCode}.md`에는 해당 직무의 실제 업무 맥락, 주요 과업, 필요한 판단 요소, 참고할 수 있는 자료 형태 등을 적습니다. 이 파일은 미션 생성기의 배경 자료로 사용됩니다.

새 직무를 추가한 뒤에는 관리자 페이지를 새로고침하면 직무 상태가 갱신됩니다.

## 생성 결과가 저장되는 위치

관리자에서 `미션 생성`을 누르면 임시 산출물이 아래 위치에 저장됩니다.

```text
outputs/pilot/v1/runs/{runId}/
```

주요 파일은 다음과 같습니다.

| 파일 | 설명 |
| --- | --- |
| `pilot_summary.json` | 생성 run 요약입니다. |
| `artifact_index.json` | 생성된 미션 산출물 목록입니다. |
| `jobs/{jobCode}/{difficulty}/mission_output.json` | 생성된 미션 원본입니다. |
| `_failed/failure_index.json` | 실패한 항목이 있을 때 실패 정보가 저장됩니다. |

`내보내기 승인`을 누르면 임시 산출물이 사용자 화면용 파일로 변환됩니다.

```text
missions/scenarios/*.json
missions/index.json
```

내보내기 성공 후 서버의 bootstrap 캐시가 갱신되므로 서버를 재시작하지 않아도 일반 사용자 화면에서 새 미션을 볼 수 있습니다.

## 일반 사용자 미션 수행 방법

1. `http://localhost:8080/`에 접속합니다.
2. 표시된 미션 중 하나를 선택합니다.
3. 시나리오와 참고자료를 읽습니다.
4. 답변을 작성하고 제출합니다.
5. AI 평가 결과와 피드백을 확인합니다.

사용자 답변 평가는 `OPENAI_API_KEY`와 `OPENAI_EVAL_MODEL`을 사용합니다.

## CLI로 미션 생성하기

관리자 화면 대신 명령어로도 미션을 생성할 수 있습니다.

```bash
python -u -m src.mission_generation.pilot_runner --jobs K000000997 --difficulties normal --concurrency 1
```

By default, CLI mission generation also requires a working OpenAI API call. If `OPENAI_API_KEY` is missing, the run records a failure instead of saving a mock mission. Use `--allow-mock-fallback` only when you intentionally want local mock output for missing-key dry runs, or `--mock` when you want to force mock output even if a key is configured.

```bash
python -u -m src.mission_generation.pilot_runner --jobs K000000997 --difficulties normal --concurrency 1 --allow-mock-fallback
```

생성 결과를 사용자 화면용 파일로 내보내려면 run 폴더를 지정합니다.

```bash
python scripts/export_missions.py --outputs outputs/pilot/v1/runs/{runId}
```

index만 다시 만들고 싶을 때는 다음 명령을 사용합니다.

```bash
python scripts/export_missions.py --rebuild-index
```

## 검증 명령

프론트엔드와 TypeScript 서버 타입을 확인합니다.

```bash
npm run typecheck
```

관리자 페이지 JavaScript 문법을 확인합니다.

```bash
node --check app/admin.js
```

E2E 테스트가 필요한 경우 Playwright 테스트를 실행합니다.

```bash
npm run test:e2e
```

서버 상태는 다음 주소로 확인할 수 있습니다.

```text
http://localhost:8080/health
http://localhost:8080/api/bootstrap
```

## 문제 해결

### 관리자 페이지에서 401이 나올 때

입력한 암호가 `.env`의 `API_SHARED_TOKEN`과 같은지 확인합니다. `.env`를 수정했다면 서버를 재시작합니다.

### 직무가 `추가자료 필요`로 표시될 때

해당 직무 코드의 Markdown 파일이 필요합니다.

```text
data/additional_search/{jobCode}.md
```

### 미션 생성이 실패할 때

아래 항목을 확인합니다.

- `.env`에 `OPENAI_API_KEY`가 있는지
- `OPENAI_GENERATION_MODEL` 호출 권한과 OpenAI API 상태가 정상인지
- `data/api_raw/{jobCode}/`가 있는지
- `data/additional_search/{jobCode}.md`가 있는지
- Python 명령이 실행 가능한지
- `PYTHON_BIN` 값이 현재 환경에 맞는지

### 내보내기 승인이 실패할 때

내보내기 단계에서는 다음 항목을 검증합니다.

- `missions/index.json`에서 참조하는 파일이 실제로 존재하는지
- 미션 key 또는 id가 중복되지 않는지
- `axis_signals` 값이 유효한지
- `/api/bootstrap` 응답 스키마를 통과하는지

검증 실패 메시지를 확인한 뒤 생성 결과를 삭제하고 다시 생성하거나, 필요한 경우 `missions/scenarios`의 충돌 항목을 정리합니다.

### 포트가 이미 사용 중일 때

`.env`의 `PORT` 값을 바꾼 뒤 서버를 다시 실행합니다.

```env
PORT=8081
```

```bash
npm run dev
```
