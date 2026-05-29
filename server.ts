import "dotenv/config";
import { randomUUID } from "node:crypto";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import express from "express";
import fs from "node:fs";
import { promises as fsp } from "node:fs";
import os from "node:os";
import path from "node:path";
import { TextDecoder } from "node:util";
import { fileURLToPath } from "node:url";
import { z } from "zod";
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
const OUTPUTS_DIR = path.join(__dirname, "outputs");
const API_RAW_DIR = path.join(__dirname, "data/api_raw");
const ADDITIONAL_SEARCH_DIR = path.join(__dirname, "data/additional_search");
const EVALUATION_LOG_PREFIX = "evaluation-logs-";
const OPENAI_EVAL_MODEL = process.env.OPENAI_EVAL_MODEL ?? "gpt-5-nano";
const API_SHARED_TOKEN = (process.env.API_SHARED_TOKEN ?? "").trim();
const COMPATIBILITY_SCORE_GAMMA = Number(process.env.COMPATIBILITY_SCORE_GAMMA ?? 1.5);
const ADMIN_LOG_LIMIT = 500;
const ADMIN_DIFFICULTIES = ["easy", "normal", "hard"] as const;

type RateLimitBucket = {
  count: number;
  resetAt: number;
};

type GenerationStatus = "running" | "succeeded" | "failed" | "exported";

type MissionPreview = {
  mission_id: string;
  title: string;
  job_name: string;
  difficulty: string;
  estimated_time_minutes: number | null;
  scenario_summary: string;
  tasks: string[];
  materials: Array<{
    material_id: string;
    type: string;
    subtype: string;
    title: string;
    description: string;
    used_for: string;
    data: unknown;
    confidence: unknown;
  }>;
  materials_count: number;
  mission_output_path: string;
  reliability_score: number | null;
  warning_count: number;
  fail_count: number;
};

type ExportResult = {
  status: "succeeded" | "failed";
  finished_at: string;
  output: string;
  validation?: ReturnType<typeof validateAndRefreshMissionBootstrap>;
};

type GenerationRun = {
  runId: string;
  status: GenerationStatus;
  jobCode: string;
  difficulty: typeof ADMIN_DIFFICULTIES[number];
  createdAt: string;
  updatedAt: string;
  logs: Array<{ at: string; stream: "system" | "stdout" | "stderr"; message: string }>;
  process?: ChildProcessWithoutNullStreams;
  runDir?: string;
  summary?: unknown;
  missionPreview?: MissionPreview | null;
  exitCode?: number | null;
  error?: string;
  exportResult?: ExportResult;
};

type OpenAiAdminStatus = {
  configured: boolean;
  eval_model: string;
  generation_api_key_env: "OPENAI_API_KEY";
};

const evaluateRateBuckets = new Map<string, RateLimitBucket>();
const generationRuns = new Map<string, GenerationRun>();
let lastLogCleanupAt = 0;
let activeGenerationRunId: string | null = null;

const GenerationRunRequestSchema = z.object({
  jobCode: z.string().regex(/^K\d+$/),
  difficulty: z.enum(ADMIN_DIFFICULTIES)
});

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

function getPythonBin() {
  if (process.env.PYTHON_BIN?.trim()) return process.env.PYTHON_BIN.trim();
  return os.platform() === "win32" ? "python" : "python3";
}

function decodeKnownXml(buffer: Buffer) {
  const utf8 = buffer.toString("utf8");
  if (!utf8.includes("\uFFFD")) return utf8;
  try {
    return new TextDecoder("euc-kr").decode(buffer);
  } catch {
    return utf8;
  }
}

function extractXmlTag(text: string, tag: string) {
  const match = text.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`));
  return match?.[1]?.replace(/\s+/g, " ").trim() ?? "";
}

function loadAdminJobs() {
  let entries: fs.Dirent[] = [];
  try {
    entries = fs.readdirSync(API_RAW_DIR, { withFileTypes: true });
  } catch {
    return [];
  }

  return entries
    .filter((entry) => entry.isDirectory() && /^K\d+$/.test(entry.name))
    .map((entry) => {
      const jobCode = entry.name;
      const detailPath = path.join(API_RAW_DIR, jobCode, "dtlGb_2.xml");
      const practicePath = path.join(ADDITIONAL_SEARCH_DIR, `${jobCode}.md`);
      let jobName = jobCode;
      let jobField = "";
      let summary = "";
      if (fs.existsSync(detailPath)) {
        const text = decodeKnownXml(fs.readFileSync(detailPath));
        jobName = extractXmlTag(text, "jobSmclNm") || jobCode;
        jobField = extractXmlTag(text, "jobMdclNm");
        summary = extractXmlTag(text, "jobSum");
      }
      return {
        jobCode,
        jobName,
        jobField,
        summary,
        enabled: fs.existsSync(practicePath),
        hasPracticeSheet: fs.existsSync(practicePath),
        rawPath: path.relative(__dirname, path.join(API_RAW_DIR, jobCode)).replace(/\\/g, "/"),
        practiceSheetPath: fs.existsSync(practicePath)
          ? path.relative(__dirname, practicePath).replace(/\\/g, "/")
          : null
      };
    })
    .sort((a, b) => {
      if (a.enabled !== b.enabled) return a.enabled ? -1 : 1;
      return a.jobCode.localeCompare(b.jobCode);
    });
}

function addRunLog(run: GenerationRun, stream: "system" | "stdout" | "stderr", message: string) {
  const lines = message.replace(/\r/g, "").split("\n").map((line) => line.trimEnd()).filter(Boolean);
  for (const line of lines) {
    run.logs.push({ at: new Date().toISOString(), stream, message: line });
  }
  if (run.logs.length > ADMIN_LOG_LIMIT) {
    run.logs.splice(0, run.logs.length - ADMIN_LOG_LIMIT);
  }
  run.updatedAt = new Date().toISOString();
}

function serializeRun(run: GenerationRun) {
  const { process: _process, ...serializable } = run;
  return serializable;
}

function resolveInside(base: string, candidate: string) {
  const resolved = path.resolve(base, candidate);
  const normalizedBase = path.resolve(base);
  if (resolved !== normalizedBase && !resolved.startsWith(normalizedBase + path.sep)) {
    throw new Error(`Path escapes base directory: ${candidate}`);
  }
  return resolved;
}

function findLatestRunDir(startedAtMs: number) {
  const runsRoot = path.join(OUTPUTS_DIR, "pilot/v1/runs");
  if (!fs.existsSync(runsRoot)) return null;
  const candidates = fs.readdirSync(runsRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => {
      const dir = path.join(runsRoot, entry.name);
      const stat = fs.statSync(dir);
      return { dir, mtimeMs: stat.mtimeMs };
    })
    .filter((item) => item.mtimeMs >= startedAtMs - 5_000)
    .sort((a, b) => b.mtimeMs - a.mtimeMs);
  return candidates[0]?.dir ?? null;
}

function loadJsonFile<T = unknown>(filePath: string): T {
  return JSON.parse(fs.readFileSync(filePath, "utf8")) as T;
}

function openAiAdminStatus(): OpenAiAdminStatus {
  return {
    configured: Boolean(process.env.OPENAI_API_KEY),
    eval_model: OPENAI_EVAL_MODEL,
    generation_api_key_env: "OPENAI_API_KEY"
  };
}

function buildMissionPreview(runDir: string): MissionPreview | null {
  const summaryPath = path.join(runDir, "pilot_summary.json");
  const artifactIndexPath = path.join(runDir, "artifact_index.json");
  if (!fs.existsSync(summaryPath) || !fs.existsSync(artifactIndexPath)) return null;

  const artifactIndex = loadJsonFile<{ items?: Array<{ status?: string; mission_output_path?: string | null }> }>(artifactIndexPath);
  const missionRelPath = artifactIndex.items?.find((item) => item.status === "saved" && item.mission_output_path)?.mission_output_path;
  if (!missionRelPath) return null;
  const missionPath = resolveInside(runDir, missionRelPath);
  const mission = loadJsonFile<Record<string, any>>(missionPath);
  const missionBlock = mission.mission ?? {};
  const difficulty = missionBlock.difficulty ?? {};
  const scenario = missionBlock.scenario ?? {};
  const tasks = Array.isArray(missionBlock.tasks) ? missionBlock.tasks : [];
  const materials = Array.isArray(missionBlock.materials) ? missionBlock.materials : [];
  const reliability = mission.reliability ?? {};

  return {
    mission_id: String(mission.mission_id ?? ""),
    title: String(missionBlock.title ?? ""),
    job_name: String(mission.job_identity?.job_smcl_nm ?? ""),
    difficulty: String(difficulty.level ?? ""),
    estimated_time_minutes: Number.isFinite(Number(difficulty.estimated_time_minutes))
      ? Number(difficulty.estimated_time_minutes)
      : null,
    scenario_summary: [
      scenario.context,
      scenario.goal ? `목표: ${scenario.goal}` : "",
      mission.mission_facts?.main_issue ? `핵심 이슈: ${mission.mission_facts.main_issue}` : ""
    ].filter(Boolean).join("\n"),
    tasks: tasks.map((task: { instruction?: unknown }) => String(task?.instruction ?? "")).filter(Boolean),
    materials: materials.map((material: Record<string, unknown>) => ({
      material_id: String(material.material_id ?? ""),
      type: String(material.type ?? ""),
      subtype: String(material.subtype ?? ""),
      title: String(material.title ?? ""),
      description: String(material.description ?? ""),
      used_for: String(material.used_for ?? ""),
      data: material.data ?? {},
      confidence: material.confidence ?? null
    })),
    materials_count: materials.length,
    mission_output_path: path.relative(__dirname, missionPath).replace(/\\/g, "/"),
    reliability_score: Number.isFinite(Number(reliability.score)) ? Number(reliability.score) : null,
    warning_count: Number(reliability.warning_count ?? 0),
    fail_count: Number(reliability.fail_count ?? 0)
  };
}

function validateAndRefreshMissionBootstrap() {
  const indexPath = path.join(MISSIONS_DIR, "index.json");
  const index = loadJsonFile<{ missions?: Array<Record<string, any>> }>(indexPath);
  const missions = Array.isArray(index.missions) ? index.missions : [];
  const issues: Array<{ code: string; message: string; path?: string }> = [];
  const keys = new Set<string>();
  const ids = new Set<string>();
  const axes = ["AX1", "AX2", "AX3", "AX4", "AX5"];

  for (const entry of missions) {
    const key = String(entry.key ?? "");
    const missionId = String(entry.mission_id ?? "");
    if (!key) issues.push({ code: "MISSION_KEY_MISSING", message: "Mission index entry is missing key." });
    if (keys.has(key)) issues.push({ code: "MISSION_KEY_DUPLICATE", message: `Duplicate mission key: ${key}` });
    keys.add(key);
    if (!missionId) issues.push({ code: "MISSION_ID_MISSING", message: `Mission ${key || "(unknown)"} is missing mission_id.` });
    if (ids.has(missionId)) issues.push({ code: "MISSION_ID_DUPLICATE", message: `Duplicate mission_id: ${missionId}` });
    ids.add(missionId);

    const relativePath = String(entry.path ?? "");
    let missionPath = "";
    try {
      missionPath = resolveInside(MISSIONS_DIR, relativePath);
    } catch (error) {
      issues.push({ code: "MISSION_PATH_INVALID", message: error instanceof Error ? error.message : String(error), path: relativePath });
      continue;
    }
    if (!fs.existsSync(missionPath)) {
      issues.push({ code: "MISSION_FILE_MISSING", message: `Referenced mission file is missing: ${relativePath}`, path: relativePath });
      continue;
    }

    const signals = entry.axis_signals ?? {};
    for (const axis of axes) {
      const value = Number(signals[axis]);
      if (!Number.isFinite(value) || value < 0) {
        issues.push({ code: "AXIS_SIGNAL_INVALID", message: `${key} has invalid ${axis}.`, path: relativePath });
      }
    }
  }

  const payload = buildMissionBootstrapPayload(MISSIONS_DIR);
  const validated = BootstrapResponseSchema.safeParse(payload);
  if (!validated.success) {
    issues.push({
      code: "BOOTSTRAP_RESPONSE_SCHEMA_MISMATCH",
      message: "Mission bootstrap payload does not match API schema."
    });
  }
  if (issues.length) {
    return { ok: false, issues, jobs: 0, missions: 0 };
  }

  missionBootstrapPayload = payload;
  return {
    ok: true,
    issues,
    jobs: payload.jobDefs.length,
    missions: payload.allMissions.length
  };
}

function completeGenerationRun(run: GenerationRun, exitCode: number | null, startedAtMs: number) {
  run.exitCode = exitCode;
  run.process = undefined;
  activeGenerationRunId = null;

  if (exitCode !== 0) {
    run.status = "failed";
    run.error = `Generation process exited with code ${exitCode ?? "unknown"}.`;
    addRunLog(run, "system", run.error);
    return;
  }

  const runDir = findLatestRunDir(startedAtMs);
  if (!runDir) {
    run.status = "failed";
    run.error = "Generation finished, but no output run directory was found.";
    addRunLog(run, "system", run.error);
    return;
  }

  run.runDir = path.relative(__dirname, runDir).replace(/\\/g, "/");
  const summaryPath = path.join(runDir, "pilot_summary.json");
  try {
    run.summary = fs.existsSync(summaryPath) ? loadJsonFile(summaryPath) : null;
    run.missionPreview = buildMissionPreview(runDir);
    const savedCount = Number((run.summary as { saved_count?: unknown } | null)?.saved_count ?? 0);
    run.status = savedCount > 0 && run.missionPreview ? "succeeded" : "failed";
    if (run.status === "failed") {
      run.error = "Generation completed, but no saved mission output was found.";
    }
    addRunLog(run, "system", `Generation ${run.status}. runDir=${run.runDir}`);
  } catch (error) {
    run.status = "failed";
    run.error = error instanceof Error ? error.message : String(error);
    addRunLog(run, "system", run.error);
  }
}

function runCommandCapture(command: string, args: string[]) {
  return new Promise<{ code: number | null; output: string }>((resolve) => {
    let settled = false;
    const finish = (code: number | null, output: string) => {
      if (settled) return;
      settled = true;
      resolve({ code, output });
    };
    const child = spawn(command, args, {
      cwd: __dirname,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8"
      }
    });
    let output = "";
    child.stdout.on("data", (data) => {
      output += String(data);
    });
    child.stderr.on("data", (data) => {
      output += String(data);
    });
    child.on("error", (error) => {
      output += `\n${error.message}`;
      finish(1, output);
    });
    child.on("close", (code) => {
      finish(code, output);
    });
  });
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

app.get("/api/admin/mission-generation/jobs", requireApiToken, (_req, res) => {
  const jobs = loadAdminJobs();
  return res.json({
    jobs,
    total: jobs.length,
    enabled_count: jobs.filter((job) => job.enabled).length,
    api_token_required: Boolean(API_SHARED_TOKEN),
    openai: openAiAdminStatus()
  });
});

app.post("/api/admin/mission-generation/runs", requireApiToken, (req, res) => {
  const parsed = GenerationRunRequestSchema.safeParse(req.body);
  if (!parsed.success) {
    return sendApiError(
      res,
      400,
      "GENERATION_BAD_REQUEST",
      "Request body does not match generation run contract.",
      parsed.error.issues
    );
  }

  const activeRun = activeGenerationRunId ? generationRuns.get(activeGenerationRunId) : null;
  if (activeRun?.status === "running") {
    return sendApiError(
      res,
      409,
      "GENERATION_ALREADY_RUNNING",
      "Another mission generation run is already in progress.",
      { run_id: activeRun.runId }
    );
  }

  const jobs = loadAdminJobs();
  const selectedJob = jobs.find((job) => job.jobCode === parsed.data.jobCode);
  if (!selectedJob) {
    return sendApiError(res, 404, "GENERATION_JOB_NOT_FOUND", "Job code was not found under data/api_raw.");
  }
  if (!selectedJob.enabled) {
    return sendApiError(
      res,
      400,
      "GENERATION_JOB_DISABLED",
      "This job cannot be generated until data/additional_search contains a matching Markdown file.",
      { job_code: selectedJob.jobCode }
    );
  }

  const runId = randomUUID();
  const now = new Date().toISOString();
  const run: GenerationRun = {
    runId,
    status: "running",
    jobCode: selectedJob.jobCode,
    difficulty: parsed.data.difficulty,
    createdAt: now,
    updatedAt: now,
    logs: [],
    missionPreview: null
  };
  generationRuns.set(runId, run);
  activeGenerationRunId = runId;

  const pythonBin = getPythonBin();
  const args = [
    "-u",
    "-m",
    "src.mission_generation.pilot_runner",
    "--jobs",
    selectedJob.jobCode,
    "--difficulties",
    parsed.data.difficulty,
    "--concurrency",
    "1"
  ];
  const startedAtMs = Date.now();
  addRunLog(run, "system", `Starting ${pythonBin} ${args.join(" ")}`);

  try {
    const child = spawn(pythonBin, args, {
      cwd: __dirname,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8"
      }
    });
    run.process = child;
    child.stdout.on("data", (data) => addRunLog(run, "stdout", String(data)));
    child.stderr.on("data", (data) => addRunLog(run, "stderr", String(data)));
    child.on("error", (error) => {
      run.process = undefined;
      run.status = "failed";
      run.error = error.message;
      activeGenerationRunId = null;
      addRunLog(run, "system", error.message);
    });
    child.on("close", (code) => {
      completeGenerationRun(run, code, startedAtMs);
    });
  } catch (error) {
    run.process = undefined;
    run.status = "failed";
    run.error = error instanceof Error ? error.message : String(error);
    activeGenerationRunId = null;
    addRunLog(run, "system", run.error);
  }

  return res.status(202).json({ run: serializeRun(run) });
});

app.get("/api/admin/mission-generation/runs/:runId", requireApiToken, (req, res) => {
  const run = generationRuns.get(req.params.runId);
  if (!run) {
    return sendApiError(res, 404, "GENERATION_RUN_NOT_FOUND", "Mission generation run was not found.");
  }
  return res.json({ run: serializeRun(run) });
});

app.delete("/api/admin/mission-generation/runs/:runId", requireApiToken, async (req, res) => {
  const run = generationRuns.get(req.params.runId);
  if (!run) {
    return sendApiError(res, 404, "GENERATION_RUN_NOT_FOUND", "Mission generation run was not found.");
  }
  if (run.status === "running") {
    return sendApiError(res, 409, "GENERATION_STILL_RUNNING", "Cannot delete while generation is still running.");
  }
  if (run.status === "exported") {
    return sendApiError(
      res,
      409,
      "GENERATION_ALREADY_EXPORTED",
      "This run has already been exported. Delete from the mission catalog separately."
    );
  }

  try {
    if (run.runDir) {
      const runDir = resolveInside(__dirname, run.runDir);
      await fsp.rm(runDir, { recursive: true, force: true });
    }
    if (activeGenerationRunId === run.runId) activeGenerationRunId = null;
    generationRuns.delete(run.runId);
    return res.json({ ok: true, run_id: run.runId });
  } catch (error) {
    return sendApiError(
      res,
      500,
      "GENERATION_DELETE_FAILED",
      error instanceof Error ? error.message : String(error)
    );
  }
});

app.post("/api/admin/mission-generation/runs/:runId/export", requireApiToken, async (req, res) => {
  const run = generationRuns.get(req.params.runId);
  if (!run) {
    return sendApiError(res, 404, "GENERATION_RUN_NOT_FOUND", "Mission generation run was not found.");
  }
  if (run.status === "running") {
    return sendApiError(res, 409, "GENERATION_STILL_RUNNING", "Cannot export while generation is still running.");
  }
  if (!run.runDir) {
    return sendApiError(res, 400, "GENERATION_RUN_HAS_NO_OUTPUT", "Run does not have an output directory to export.");
  }
  if (run.status === "exported" && run.exportResult) {
    return res.json({ run: serializeRun(run) });
  }

  try {
    const runDir = resolveInside(__dirname, run.runDir);
    addRunLog(run, "system", `Export approval started for ${run.runDir}`);
    const command = getPythonBin();
    const result = await runCommandCapture(command, ["scripts/export_missions.py", "--outputs", runDir]);
    addRunLog(run, result.code === 0 ? "stdout" : "stderr", result.output);

    if (result.code !== 0) {
      run.exportResult = {
        status: "failed",
        finished_at: new Date().toISOString(),
        output: result.output
      };
      return sendApiError(
        res,
        500,
        "MISSION_EXPORT_FAILED",
        "export_missions.py failed.",
        run.exportResult
      );
    }

    const validation = validateAndRefreshMissionBootstrap();
    run.exportResult = {
      status: validation.ok ? "succeeded" : "failed",
      finished_at: new Date().toISOString(),
      output: result.output,
      validation
    };

    if (!validation.ok) {
      addRunLog(run, "system", `Export validation failed. issues=${validation.issues.length}`);
      return sendApiError(
        res,
        500,
        "MISSION_EXPORT_VALIDATION_FAILED",
        "Export finished, but mission catalog validation failed.",
        validation
      );
    }

    run.status = "exported";
    addRunLog(run, "system", `Export approved. missions=${validation.missions} jobs=${validation.jobs}`);
    return res.json({ run: serializeRun(run) });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    run.exportResult = {
      status: "failed",
      finished_at: new Date().toISOString(),
      output: message
    };
    addRunLog(run, "system", `Export failed. ${message}`);
    return sendApiError(res, 500, "MISSION_EXPORT_FAILED", message, run.exportResult);
  }
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
