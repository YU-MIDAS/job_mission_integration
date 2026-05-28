# JOBSIM 운영 런북

## 1) 환경 변수 준비

```bash
cp .env.server.example .env.server
```

필수:
- `OPENAI_API_KEY`

선택:
- `OPENAI_EVAL_MODEL` (기본 `gpt-5-nano`)
- `PORT` (기본 `8080`)
- `EVALUATE_RATE_LIMIT_MAX` (기본 `10`)
- `EVALUATE_RATE_LIMIT_WINDOW_MS` (기본 `60000`)
- `EVALUATION_LOG_RETENTION_DAYS` (기본 `14`)
- `API_SHARED_TOKEN` (설정 시 `x-api-token` 또는 Bearer 토큰 필수)

## 2) 서버 실행 (PM2)

```bash
npm install
export $(grep -v '^#' .env.server | xargs)
pm2 start ecosystem.config.cjs --update-env
pm2 save
```

상태 확인:
```bash
pm2 status
npm run ops:healthcheck
npm run ops:model-check
```

## 3) 서버 실행 (Docker)

```bash
docker compose up -d --build
```

상태 확인:
```bash
docker compose ps
npm run ops:healthcheck -- http://localhost:8080/health
npm run ops:model-check
```

## 4) 배포 전 체크리스트

1. `npm run pipeline:check`
2. `npm run eval:stability -- --runs=3 --out=reports/eval-stability-predeploy.json`
3. `npm run ops:healthcheck -- http://localhost:8080/health`
4. `npm run ops:model-check`
5. `OPENAI_API_KEY` 노출 여부 재검사 (`.env.example`, `.env.server.example`에 실키 금지)

## 5) 장애 대응 기본

1. `/health` 응답 확인
2. `pm2 logs jobsim-server` 또는 `docker compose logs -f`
3. `OPENAI_API_KEY` 누락/만료 여부 점검
4. `missions/index.json` 경로 및 파일 무결성 점검
