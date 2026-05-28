import "dotenv/config";
import express from "express";
import fs from "node:fs";
import { promises as fsp } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  BootstrapResponseSchema,
  EvaluateRequestSchema,
  EvaluateResponseSchema,
  LogEntrySchema,
  RecommendationsRequestSchema,
  RecommendationsResponseSchema
} from "./src/lib/api/contracts.js";
import { buildMissionBootstrapPayload } from "./src/lib/bootstrap/missionBootstrap.js";
import { evaluateAnswer } from "./src/lib/evaluator/evaluateAnswer.js";
import {
  buildJobWeightCatalog,
  buildResultSummary,
  type MissionScoresByJob
} from "./src/lib/recommendation/engine.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const port = Number(process.env.PORT ?? 8080);
const REPORTS_DIR = path.join(__dirname, "reports");
const MISSIONS_DIR = path.join(__dirname, "missions");
const EVALUATION_LOG_PREFIX = "evaluation-logs-";
const OPENAI_EVAL_MODEL = process.env.OPENAI_EVAL_MODEL ?? "gpt-5-nano";
const API_SHARED_TOKEN = (process.env.API_SHARED_TOKEN ?? "").trim();
const COMPATIBILITY_SCORE_GAMMA = Number(process.env.COMPATIBILITY_SCORE_GAMMA ?? 1.5);

type RateLimitBucket = {
  count: number;
  resetAt: number;
};

const evaluateRateBuckets = new Map<string, RateLimitBucket>();
let lastLogCleanupAt = 0;

function positiveIntFromEnv(raw: string | undefined, fallback: number) {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(1, Math.floor(parsed));
}

const EVALUATION_LOG_RETENTION_DAYS = positiveIntFromEnv(
  process.env.EVALUATION_LOG_RETENTION_DAYS,
  14
);
const EVALUATE_RATE_LIMIT_WINDOW_MS = Math.max(
  1_000,
  positiveIntFromEnv(process.env.EVALUATE_RATE_LIMIT_WINDOW_MS, 60_000)
);
const EVALUATE_RATE_LIMIT_MAX = positiveIntFromEnv(
  process.env.EVALUATE_RATE_LIMIT_MAX,
  10
);

function loadJobWeightCatalog() {
  const rawPath = path.join(__dirname, "data/processed/job_weights.json");
  const raw = JSON.parse(fs.readFileSync(rawPath, "utf8")) as unknown;
  return buildJobWeightCatalog(raw);
}

const jobWeightCatalog = loadJobWeightCatalog();
let missionBootstrapPayload: ReturnType<typeof buildMissionBootstrapPayload> | null = null;
try {
  missionBootstrapPayload = buildMissionBootstrapPayload(MISSIONS_DIR);
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error("[bootstrap] failed to preload mission catalog:", message);
}

function sendApiError(
  res: express.Response,
  status: number,
  code: string,
  message: string,
  details?: unknown
) {
  return res.status(status).json({
    error: {
      code,
      message,
      ...(details === undefined ? {} : { details })
    }
  });
}

function getLocalDateStamp(date = new Date()) {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function getDailyEvaluationLogPath(date = new Date()) {
  return path.join(REPORTS_DIR, `${EVALUATION_LOG_PREFIX}${getLocalDateStamp(date)}.jsonl`);
}

function getClientIp(req: express.Request) {
  const forwarded = req.headers["x-forwarded-for"];
  if (typeof forwarded === "string" && forwarded.trim()) {
    return forwarded.split(",")[0].trim();
  }
  if (Array.isArray(forwarded) && forwarded.length > 0) {
    return forwarded[0]?.trim() ?? req.ip;
  }
  return req.ip || req.socket.remoteAddress || "unknown";
}

function extractApiToken(req: express.Request) {
  const headerToken = req.header("x-api-token");
  if (headerToken?.trim()) return headerToken.trim();
  const authorization = req.header("authorization") ?? "";
  if (authorization.toLowerCase().startsWith("bearer ")) {
    return authorization.slice(7).trim();
  }
  return "";
}

const requireApiToken: express.RequestHandler = (req, res, next) => {
  if (!API_SHARED_TOKEN) return next();
  const provided = extractApiToken(req);
  if (provided && provided === API_SHARED_TOKEN) return next();
  return sendApiError(
    res,
    401,
    "AUTH_REQUIRED",
    "Missing or invalid API token."
  );
};

function pruneExpiredRateBuckets(now: number) {
  for (const [key, bucket] of evaluateRateBuckets) {
    if (bucket.resetAt <= now) evaluateRateBuckets.delete(key);
  }
}

const evaluateRateLimit: express.RequestHandler = (req, res, next) => {
  const now = Date.now();
  pruneExpiredRateBuckets(now);

  const key = getClientIp(req);
  const existing = evaluateRateBuckets.get(key);
  const bucket = (!existing || existing.resetAt <= now)
    ? { count: 0, resetAt: now + EVALUATE_RATE_LIMIT_WINDOW_MS }
    : existing;

  if (bucket.count >= EVALUATE_RATE_LIMIT_MAX) {
    const retryAfterSec = Math.max(1, Math.ceil((bucket.resetAt - now) / 1000));
    res.setHeader("Retry-After", String(retryAfterSec));
    return sendApiError(
      res,
      429,
      "RATE_LIMITED",
      "Too many evaluate requests. Please retry later.",
      { retry_after_sec: retryAfterSec }
    );
  }

  bucket.count += 1;
  evaluateRateBuckets.set(key, bucket);
  return next();
};

async function cleanupOldEvaluationLogs(now = Date.now()) {
  if (now - lastLogCleanupAt < 6 * 60 * 60 * 1000) return;
  lastLogCleanupAt = now;
  const cutoff = now - (EVALUATION_LOG_RETENTION_DAYS * 24 * 60 * 60 * 1000);

  let names: string[] = [];
  try {
    names = await fsp.readdir(REPORTS_DIR);
  } catch {
    return;
  }

  const stale = names.filter((name) => {
    if (!name.startsWith(EVALUATION_LOG_PREFIX) || !name.endsWith(".jsonl")) return false;
    const stamp = name.slice(EVALUATION_LOG_PREFIX.length, -".jsonl".length);
    const parsed = Date.parse(`${stamp}T00:00:00`);
    if (!Number.isFinite(parsed)) return false;
    return parsed < cutoff;
  });

  await Promise.all(stale.map(async (name) => {
    const target = path.join(REPORTS_DIR, name);
    await fsp.rm(target, { force: true });
  }));
}

app.use(express.json({ limit: "1mb" }));
app.use(express.static(path.join(__dirname, "app")));
// 미션 JSON 정적 제공 — app/index.html 이 ../missions/* 경로로 fetch 함
app.use("/missions", express.static(path.join(__dirname, "missions")));

app.get("/health", (_req, res) => {
  return res.json({
    ok: true,
    service: "jobsim-llm-evaluator",
    now: new Date().toISOString(),
    uptime_sec: Math.round(process.uptime()),
    has_openai_key: Boolean(process.env.OPENAI_API_KEY),
    eval_model: OPENAI_EVAL_MODEL,
    compatibility_score_gamma: Number.isFinite(COMPATIBILITY_SCORE_GAMMA) ? COMPATIBILITY_SCORE_GAMMA : 1.5,
    evaluate_rate_limit: {
      window_ms: EVALUATE_RATE_LIMIT_WINDOW_MS,
      max_requests: EVALUATE_RATE_LIMIT_MAX
    },
    api_token_required: Boolean(API_SHARED_TOKEN)
  });
});

app.get("/api/bootstrap", (_req, res) => {
  if (!missionBootstrapPayload) {
    return sendApiError(
      res,
      500,
      "BOOTSTRAP_UNAVAILABLE",
      "Mission bootstrap payload is not available on this server instance."
    );
  }

  const validated = BootstrapResponseSchema.safeParse(missionBootstrapPayload);
  if (!validated.success) {
    return sendApiError(
      res,
      500,
      "BOOTSTRAP_RESPONSE_SCHEMA_MISMATCH",
      "Server produced an invalid bootstrap response payload.",
      validated.error.issues
    );
  }

  return res.json(validated.data);
});

app.post("/api/evaluate", requireApiToken, evaluateRateLimit, async (req, res) => {
  try {
    const parsed = EvaluateRequestSchema.safeParse(req.body);
    if (!parsed.success) {
      return sendApiError(
        res,
        400,
        "EVALUATE_BAD_REQUEST",
        "Request body does not match evaluate contract.",
        parsed.error.issues
      );
    }

    const result = await evaluateAnswer(parsed.data);
    const validated = EvaluateResponseSchema.safeParse(result);
    if (!validated.success) {
      return sendApiError(
        res,
        500,
        "EVALUATE_RESPONSE_SCHEMA_MISMATCH",
        "Server produced an invalid evaluate response payload.",
        validated.error.issues
      );
    }

    return res.json(validated.data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown evaluation error.";
    console.error("[evaluate]", message);
    return sendApiError(res, 500, "EVALUATE_INTERNAL_ERROR", message);
  }
});

app.post("/api/recommendations", requireApiToken, async (req, res) => {
  try {
    const parsed = RecommendationsRequestSchema.safeParse(req.body);
    if (!parsed.success) {
      return sendApiError(
        res,
        400,
        "RECOMMENDATIONS_BAD_REQUEST",
        "Request body does not match recommendations contract.",
        parsed.error.issues
      );
    }

    const missionSignalsByKey: Record<string, Record<string, number>> = {};
    const missionSignalsById: Record<string, Record<string, number>> = {};
    const missionSignalsByJob: Record<string, Array<Record<string, number>>> = {};
    if (missionBootstrapPayload?.allMissions?.length) {
      for (const mission of missionBootstrapPayload.allMissions) {
        const rawSignals = (mission as { axis_signals?: Record<string, unknown> }).axis_signals ?? {};
        const sanitizedSignals = Object.fromEntries(
          Object.entries(rawSignals).map(([axis, value]) => [axis, Number(value) || 0])
        ) as Record<string, number>;
        if (mission.key) missionSignalsByKey[mission.key] = sanitizedSignals;
        if (mission.mission_id) missionSignalsById[mission.mission_id] = sanitizedSignals;
        if (!missionSignalsByJob[mission.job_code]) missionSignalsByJob[mission.job_code] = [];
        missionSignalsByJob[mission.job_code].push(sanitizedSignals);
      }
    }

    const summary = buildResultSummary({
      selectedJobs: parsed.data.selectedJobs,
      missionScoresByJob: parsed.data.missionScoresByJob as MissionScoresByJob,
      evaluationLogs: parsed.data.evaluationLogs,
      topN: parsed.data.topN,
      catalog: jobWeightCatalog,
      missionSignalsByKey,
      missionSignalsById,
      missionSignalsByJob
    });

    const validated = RecommendationsResponseSchema.safeParse(summary);
    if (!validated.success) {
      return sendApiError(
        res,
        500,
        "RECOMMENDATIONS_RESPONSE_SCHEMA_MISMATCH",
        "Server produced an invalid recommendations response payload.",
        validated.error.issues
      );
    }

    return res.json(validated.data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown recommendations error.";
    console.error("[recommendations]", message);
    return sendApiError(res, 500, "RECOMMENDATIONS_INTERNAL_ERROR", message);
  }
});

app.post("/api/logs", requireApiToken, async (req, res) => {
  try {
    const parsed = LogEntrySchema.safeParse(req.body);
    if (!parsed.success) {
      return sendApiError(
        res,
        400,
        "LOGS_BAD_REQUEST",
        "Request body does not match log entry contract.",
        parsed.error.issues
      );
    }

    const now = Date.now();
    await fsp.mkdir(REPORTS_DIR, { recursive: true });
    await cleanupOldEvaluationLogs(now);
    const logPath = getDailyEvaluationLogPath(new Date(now));
    await fsp.appendFile(logPath, `${JSON.stringify(parsed.data)}\n`, "utf8");
    return res.status(201).json({ ok: true });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown log write error.";
    console.error("[logs]", message);
    return sendApiError(res, 500, "LOGS_INTERNAL_ERROR", message);
  }
});

app.listen(port, () => {
  console.log(`JOBSIM server running at http://localhost:${port}`);
});
