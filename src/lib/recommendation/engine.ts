import { AXES, type Axis, type Evaluation } from "../evaluator/schema.js";

type AxisProfile = Record<Axis, number>;
const DEFAULT_SIMILARITY_SCORE_GAMMA = 1.5;
const SIMILARITY_SCORE_GAMMA = (() => {
  const parsed = Number(process.env.COMPATIBILITY_SCORE_GAMMA ?? DEFAULT_SIMILARITY_SCORE_GAMMA);
  if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_SIMILARITY_SCORE_GAMMA;
  return parsed;
})();

type RawJobWeight = {
  job_name?: unknown;
  cluster_id?: unknown;
  silhouette?: unknown;
  sil_reliable?: unknown;
  is_simulation_target?: unknown;
  weights?: unknown;
};

export type JobWeightEntry = {
  code: string;
  jobName: string;
  clusterId: string;
  silhouette: number;
  reliable: boolean;
  weights: AxisProfile;
};

export type MissionScoresByJob = Record<string, Array<Partial<Record<Axis, number>>>>;

export type RecommendationItem = {
  code: string;
  name: string;
  clusterId: string;
  similarity: number;
  similarityPct: number;
  weights: AxisProfile;
};

type AxisEvidence = Evaluation["axes"]["AX1"]["evidence"][number];

export type RecommendationLog = {
  job_code: string;
  mission_key?: string;
  mission_id?: string;
  mission_title: string;
  answer?: string;
  self_report?: Record<string, unknown>;
  evaluation: Evaluation;
};

export type AxisEvidenceSummaryItem = {
  score: number;
  confidence: number;
  evidence: Array<AxisEvidence & { mission_title: string }>;
  flags: string[];
  sources: number;
};

export type AxisCoverageItem = {
  totalSignal: number;
  missionCount: number;
  measured: boolean;
};

type MissionAxisSignals = Partial<Record<Axis, number>>;

export type Quadrant = "q1" | "q2" | "q3" | "q4";

export type JobInsight = {
  performance: number;
  emotion: number;
  compatibility: number;
  quadrant: Quadrant;
  topAxis: Axis;
  lowAxis: Axis;
  missionCount: number;
  label: string;
  detail: string;
};

export type ResultSummary = {
  axisProfile: AxisProfile;
  similarityProfile: AxisProfile;
  compatibility: Record<string, number>;
  selectedJobMeta: Record<string, {
    referenceJobName: string | null;
    clusterId: string | null;
    reliable: boolean;
    silhouette: number | null;
    referenceWeights: AxisProfile | null;
  }>;
  recommendations: Record<string, RecommendationItem[]>;
  bestMatch: {
    jobCode: string;
    score: number;
    referenceJobName: string;
    referenceWeights: AxisProfile;
  } | null;
  axisEvidenceSummary: Record<Axis, AxisEvidenceSummaryItem>;
  axisCoverage: Record<Axis, AxisCoverageItem>;
  measuredAxes: Axis[];
  jobInsights: Record<string, JobInsight>;
};

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return value;
}

function asOptionalNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

function clamp01(value: number): number {
  return Math.min(Math.max(value, 0), 1);
}

function emptyAxisProfile(): AxisProfile {
  return Object.fromEntries(AXES.map((axis) => [axis, 0])) as AxisProfile;
}

function emptyAxisCounts(): Record<Axis, number> {
  return Object.fromEntries(AXES.map((axis) => [axis, 0])) as Record<Axis, number>;
}

function emptyAxisEvidenceSummary(): Record<Axis, AxisEvidenceSummaryItem> {
  return Object.fromEntries(
    AXES.map((axis) => [axis, {
      score: 0,
      confidence: 0,
      evidence: [],
      flags: [],
      sources: 0
    }])
  ) as unknown as Record<Axis, AxisEvidenceSummaryItem>;
}

function emptyAxisCoverage(): Record<Axis, AxisCoverageItem> {
  return Object.fromEntries(
    AXES.map((axis) => [axis, {
      totalSignal: 0,
      missionCount: 0,
      measured: true
    }])
  ) as Record<Axis, AxisCoverageItem>;
}

function axisRefFromLog(log: RecommendationLog) {
  if (typeof log.mission_key === "string" && log.mission_key.trim()) {
    return `key:${log.mission_key.trim()}`;
  }
  if (typeof log.mission_id === "string" && log.mission_id.trim()) {
    return `id:${log.mission_id.trim()}`;
  }
  return `fallback:${log.job_code}:${log.mission_title}`;
}

function normalizedWeightProfile(profile: AxisProfile): AxisProfile {
  const safe = Object.fromEntries(
    AXES.map((axis) => [axis, Math.max(profile[axis] ?? 0, 0)])
  ) as AxisProfile;
  const total = AXES.reduce((sum, axis) => sum + safe[axis], 0);
  if (total <= 0) return safe;
  return Object.fromEntries(
    AXES.map((axis) => [axis, safe[axis] / total])
  ) as AxisProfile;
}

function applyAxisMask(profile: AxisProfile, measuredAxes: readonly Axis[]): AxisProfile {
  const measured = new Set(measuredAxes);
  return Object.fromEntries(
    AXES.map((axis) => [axis, measured.has(axis) ? (profile[axis] ?? 0) : 0])
  ) as AxisProfile;
}

function round4(value: number) {
  return Number(value.toFixed(4));
}

function buildAxisCoverage(input: {
  selectedJobs: string[];
  evaluationLogs: RecommendationLog[];
  missionSignalsByKey?: Record<string, MissionAxisSignals>;
  missionSignalsById?: Record<string, MissionAxisSignals>;
  missionSignalsByJob?: Record<string, MissionAxisSignals[]>;
  threshold?: number;
}) {
  const threshold = Math.max(0, input.threshold ?? 0.05);
  const coverage = emptyAxisCoverage();
  const selected = new Set(input.selectedJobs);
  const seenRefs = new Set<string>();
  let signalMissionCount = 0;

  function applySignals(signals: MissionAxisSignals | undefined) {
    if (!signals) return false;
    let hasSignal = false;
    for (const axis of AXES) {
      const signal = clamp01(asNumber(signals[axis], 0));
      if (signal > 0) {
        coverage[axis].missionCount += 1;
        hasSignal = true;
      }
      coverage[axis].totalSignal += signal;
    }
    return hasSignal;
  }

  for (const log of input.evaluationLogs) {
    if (!selected.has(log.job_code)) continue;
    const ref = axisRefFromLog(log);
    if (seenRefs.has(ref)) continue;
    seenRefs.add(ref);

    const signalFromKey = log.mission_key ? input.missionSignalsByKey?.[log.mission_key] : undefined;
    const signalFromId = log.mission_id ? input.missionSignalsById?.[log.mission_id] : undefined;
    const hasSignal = applySignals(signalFromKey || signalFromId);
    if (hasSignal) signalMissionCount += 1;
  }

  // 로그에서 신호를 못 찾은 경우(legacy 로그 등) 선택 직무의 전체 미션 신호로 fallback
  if (signalMissionCount === 0 && input.missionSignalsByJob) {
    for (const jobCode of input.selectedJobs) {
      const signalsList = input.missionSignalsByJob[jobCode] ?? [];
      for (const signals of signalsList) {
        const hasSignal = applySignals(signals);
        if (hasSignal) signalMissionCount += 1;
      }
    }
  }

  const hasSignalContext = signalMissionCount > 0;
  for (const axis of AXES) {
    coverage[axis].totalSignal = round4(coverage[axis].totalSignal);
    coverage[axis].measured = hasSignalContext
      ? (coverage[axis].missionCount > 0 && coverage[axis].totalSignal >= threshold)
      : true;
  }

  const measuredAxes = AXES.filter((axis) => coverage[axis].measured);

  return {
    axisCoverage: coverage,
    measuredAxes
  };
}

function normalizeProfile(profile: AxisProfile): AxisProfile {
  const transformed = Object.fromEntries(
    AXES.map((axis) => [axis, Math.sqrt(Math.max(profile[axis] ?? 0, 0))])
  ) as AxisProfile;
  const total = AXES.reduce((sum, axis) => sum + transformed[axis], 0);
  if (total === 0) return profile;
  return Object.fromEntries(AXES.map((axis) => [axis, transformed[axis] / total])) as AxisProfile;
}

function cosineSim(a: AxisProfile, b: AxisProfile): number {
  const va = AXES.map((axis) => a[axis] ?? 0);
  const vb = AXES.map((axis) => b[axis] ?? 0);
  const dot = va.reduce((sum, value, index) => sum + value * vb[index], 0);
  const magA = Math.sqrt(va.reduce((sum, value) => sum + value * value, 0));
  const magB = Math.sqrt(vb.reduce((sum, value) => sum + value * value, 0));
  return magA * magB === 0 ? 0 : dot / (magA * magB);
}

function similarityToPct(similarity: number): number {
  const normalized = clamp01(similarity);
  return Math.round(Math.pow(normalized, SIMILARITY_SCORE_GAMMA) * 100);
}

function computeProfile(missionScoresByJob: MissionScoresByJob): AxisProfile {
  const totals = emptyAxisProfile();
  const counts = emptyAxisProfile();
  const allRows = Object.values(missionScoresByJob).flatMap((rows) => rows ?? []);

  for (const row of allRows) {
    for (const axis of AXES) {
      totals[axis] += asNumber(row[axis], 0);
      counts[axis] += 1;
    }
  }

  return Object.fromEntries(
    AXES.map((axis) => [axis, counts[axis] > 0 ? totals[axis] / counts[axis] : 0])
  ) as AxisProfile;
}

function toAxisProfile(input: unknown): AxisProfile | null {
  if (!input || typeof input !== "object") return null;
  const record = input as Record<string, unknown>;
  const profile = emptyAxisProfile();
  for (const axis of AXES) {
    profile[axis] = asNumber(record[axis], 0);
  }
  return profile;
}

function buildAxisEvidenceSummary(
  evaluationLogs: RecommendationLog[],
  axisCoverage?: Record<Axis, AxisCoverageItem>
): Record<Axis, AxisEvidenceSummaryItem> {
  const summary = emptyAxisEvidenceSummary();
  const counts = emptyAxisCounts();

  for (const log of evaluationLogs) {
    for (const axis of AXES) {
      const axisResult = log.evaluation?.axes?.[axis];
      if (!axisResult) continue;

      if (axisResult.score > 0 || axisResult.evidence.length > 0 || axisResult.confidence > 0) {
        counts[axis] += 1;
        summary[axis].score += axisResult.score;
        summary[axis].confidence += axisResult.confidence;
        summary[axis].sources += 1;
      }

      for (const evidence of axisResult.evidence) {
        summary[axis].evidence.push({
          ...evidence,
          mission_title: log.mission_title
        });
      }

      for (const flag of log.evaluation.flags) {
        if (!summary[axis].flags.includes(flag)) summary[axis].flags.push(flag);
      }
    }
  }

  for (const axis of AXES) {
    const isMeasured = axisCoverage?.[axis]?.measured ?? true;
    if (!isMeasured) {
      summary[axis].score = 0;
      summary[axis].confidence = 0;
      summary[axis].evidence = [];
      summary[axis].sources = 0;
      if (!summary[axis].flags.includes("unmeasured_axis")) {
        summary[axis].flags.push("unmeasured_axis");
      }
      continue;
    }
    if (counts[axis] > 0) {
      summary[axis].score = Math.round(summary[axis].score / counts[axis]);
      summary[axis].confidence = Number((summary[axis].confidence / counts[axis]).toFixed(2));
    }
    summary[axis].evidence = summary[axis].evidence.slice(0, 3);
  }

  return summary;
}

const AXIS_LABELS: Record<Axis, string> = {
  AX1: "정보분석·논리",
  AX2: "관찰·탐색",
  AX3: "전략·판단",
  AX4: "리더십·조직",
  AX5: "대인서비스"
};

function computePerformanceByJob(missionScoresByJob: MissionScoresByJob, jobCode: string): number {
  const scores = missionScoresByJob[jobCode] ?? [];
  if (!scores.length) return 0.5;

  const missionAverages = scores.map((row) => {
    const axisTotal = AXES.reduce((sum, axis) => sum + asNumber(row[axis], 0), 0);
    // missionScore is already normalized by axis signals (sum ~= 1),
    // so divide-by-axis-count would collapse the range to <= 0.2.
    return Math.min(Math.max(axisTotal, 0), 1);
  });

  return Math.min(
    Math.max(missionAverages.reduce((sum, value) => sum + value, 0) / missionAverages.length, 0),
    1
  );
}

function computeAxisAverageByJob(missionScoresByJob: MissionScoresByJob, jobCode: string): AxisProfile {
  const scores = missionScoresByJob[jobCode] ?? [];
  if (!scores.length) return emptyAxisProfile();

  return Object.fromEntries(
    AXES.map((axis) => {
      const total = scores.reduce((sum, row) => sum + asNumber(row[axis], 0), 0);
      return [axis, total / scores.length];
    })
  ) as AxisProfile;
}

function computeEmotionByJob(evaluationLogs: RecommendationLog[], jobCode: string): number {
  function estimateEngagement(log: RecommendationLog): number {
    const answerTokens = typeof log.answer === "string"
      ? log.answer.trim().split(/\s+/).filter(Boolean).length
      : 0;
    const answerRatio = clamp01((answerTokens - 18) / 120);
    const axisResults = AXES.map((axis) => log.evaluation?.axes?.[axis]).filter(Boolean);
    const evidenceCount = axisResults.reduce((sum, result) => sum + (result?.evidence?.length ?? 0), 0);
    const evidenceRatio = clamp01(evidenceCount / 6);
    const avgConfidence = axisResults.length
      ? axisResults.reduce((sum, result) => sum + asNumber(result?.confidence, 0), 0) / axisResults.length
      : 0;

    const flags = new Set(log.evaluation?.flags ?? []);
    let penalty = 0;
    if (flags.has("too_short")) penalty += 0.20;
    if (flags.has("off_topic")) penalty += 0.20;
    if (flags.has("ambiguous")) penalty += 0.12;
    if (flags.has("possible_prompt_injection")) penalty += 0.25;
    if (log.evaluation?.source === "precheck" || log.evaluation?.source === "unavailable") penalty += 0.20;
    if (log.evaluation?.source === "heuristic") penalty += 0.08;

    const estimate =
      0.24 +
      answerRatio * 0.33 +
      evidenceRatio * 0.23 +
      clamp01(avgConfidence) * 0.20 -
      penalty;
    return clamp01(estimate);
  }

  let total = 0;
  let count = 0;

  for (const log of evaluationLogs) {
    if (log.job_code !== jobCode) continue;
    const fun = asOptionalNumber(log.self_report?.fun);
    if (fun !== null && fun >= 1 && fun <= 3) {
      total += (4 - fun) / 3;
      count += 1;
      continue;
    }
    total += estimateEngagement(log);
    count += 1;
  }

  return count > 0 ? total / count : 0.5;
}

function toQuadrant(performance: number, emotion: number): Quadrant {
  if (performance >= 0.45 && emotion >= 0.6) return "q1";
  if (performance >= 0.45 && emotion < 0.6) return "q2";
  if (performance < 0.45 && emotion >= 0.6) return "q3";
  return "q4";
}

function buildJobInsights(input: {
  selectedJobs: string[];
  missionScoresByJob: MissionScoresByJob;
  compatibility: Record<string, number>;
  evaluationLogs: RecommendationLog[];
  catalog: Record<string, JobWeightEntry>;
  measuredAxes: Axis[];
}): Record<string, JobInsight> {
  const insights: Record<string, JobInsight> = {};
  const axesForRanking: Axis[] = input.measuredAxes.length ? [...input.measuredAxes] : [...AXES];

  for (const jobCode of input.selectedJobs) {
    const performance = computePerformanceByJob(input.missionScoresByJob, jobCode);
    const emotion = computeEmotionByJob(input.evaluationLogs, jobCode);
    const compatibility = input.compatibility[jobCode] ?? 0;
    const missionCount = input.missionScoresByJob[jobCode]?.length ?? 0;
    const avg = applyAxisMask(computeAxisAverageByJob(input.missionScoresByJob, jobCode), axesForRanking);
    const sortedAxes = axesForRanking.slice().sort((a, b) => (avg[b] ?? 0) - (avg[a] ?? 0));
    const topAxis = sortedAxes[0] ?? "AX1";
    const lowAxis = sortedAxes[sortedAxes.length - 1] ?? "AX1";
    const jobName = input.catalog[jobCode]?.jobName ?? jobCode;
    const perfPct = Math.round(performance * 100);
    const emotionPct = Math.round(emotion * 100);
    const label = `${performance >= 0.45 ? "수행 높음" : "수행 낮음"} · ${emotion >= 0.6 ? "몰입 추정 높음" : "몰입 추정 낮음"}`;
    const detail = missionCount > 0
      ? `${jobName} 미션 ${missionCount}개 기준, 수행 ${perfPct}%, 몰입 추정 ${emotionPct}%, 직무 적합도 ${compatibility}점입니다. 가장 강한 축은 ${AXIS_LABELS[topAxis]}이고 가장 낮은 축은 ${AXIS_LABELS[lowAxis]}입니다.`
      : `${jobName} 평가 데이터 0개, 수행 ${perfPct}%, 몰입 추정 ${emotionPct}%, 직무 적합도 ${compatibility}점입니다.`;

    insights[jobCode] = {
      performance,
      emotion,
      compatibility,
      quadrant: toQuadrant(performance, emotion),
      topAxis,
      lowAxis,
      missionCount,
      label,
      detail
    };
  }

  return insights;
}

export function buildJobWeightCatalog(raw: unknown): Record<string, JobWeightEntry> {
  if (!raw || typeof raw !== "object") return {};
  const entries = raw as Record<string, RawJobWeight>;

  const catalog = Object.entries(entries)
    .filter(([, value]) => value?.is_simulation_target === true)
    .map(([code, value]) => {
      const weights = toAxisProfile(value.weights);
      if (!weights) return null;
      return [code, {
        code,
        jobName: typeof value.job_name === "string" ? value.job_name : code,
        clusterId: typeof value.cluster_id === "string" ? value.cluster_id : "C8",
        silhouette: asNumber(value.silhouette, 0),
        reliable: Boolean(value.sil_reliable),
        weights
      }] as const;
    })
    .filter((entry): entry is readonly [string, JobWeightEntry] => entry !== null);

  return Object.fromEntries(catalog);
}

export function buildResultSummary(input: {
  selectedJobs: string[];
  missionScoresByJob: MissionScoresByJob;
  evaluationLogs?: RecommendationLog[];
  topN?: number;
  catalog: Record<string, JobWeightEntry>;
  missionSignalsByKey?: Record<string, MissionAxisSignals>;
  missionSignalsById?: Record<string, MissionAxisSignals>;
  missionSignalsByJob?: Record<string, MissionAxisSignals[]>;
}): ResultSummary {
  const evaluationLogs = input.evaluationLogs ?? [];
  const topN = Math.max(1, Math.min(input.topN ?? 3, 10));
  const { axisCoverage, measuredAxes } = buildAxisCoverage({
    selectedJobs: input.selectedJobs,
    evaluationLogs,
    missionSignalsByKey: input.missionSignalsByKey,
    missionSignalsById: input.missionSignalsById,
    missionSignalsByJob: input.missionSignalsByJob
  });
  const axesForSimilarity: Axis[] = measuredAxes.length ? [...measuredAxes] : [...AXES];
  const axisProfile = normalizeProfile(
    applyAxisMask(computeProfile(input.missionScoresByJob), axesForSimilarity)
  );
  const similarityProfile = axisProfile;
  const profileTotal = axesForSimilarity.reduce((sum: number, axis) => sum + (axisProfile[axis] ?? 0), 0);

  const compatibility: Record<string, number> = {};
  const selectedJobMeta: ResultSummary["selectedJobMeta"] = {};
  const recommendations: Record<string, RecommendationItem[]> = {};
  let bestMatch: ResultSummary["bestMatch"] = null;

  for (const jobCode of input.selectedJobs) {
    const ref = input.catalog[jobCode];

    if (!ref || profileTotal === 0) {
      compatibility[jobCode] = 0;
      selectedJobMeta[jobCode] = {
        referenceJobName: ref?.jobName ?? null,
        clusterId: ref?.clusterId ?? null,
        reliable: Boolean(ref?.reliable),
        silhouette: ref?.silhouette ?? null,
        referenceWeights: ref?.weights ?? null
      };
      recommendations[jobCode] = [];
      continue;
    }

    const refWeightsForSimilarity = normalizedWeightProfile(
      applyAxisMask(ref.weights, axesForSimilarity)
    );
    const refWeightTotal = axesForSimilarity.reduce((sum: number, axis) => sum + (refWeightsForSimilarity[axis] ?? 0), 0);
    if (refWeightTotal <= 0) {
      compatibility[jobCode] = 0;
      selectedJobMeta[jobCode] = {
        referenceJobName: ref.jobName,
        clusterId: ref.clusterId,
        reliable: ref.reliable,
        silhouette: ref.silhouette,
        referenceWeights: ref.weights
      };
      recommendations[jobCode] = [];
      continue;
    }

    const sim = cosineSim(similarityProfile, refWeightsForSimilarity);
    const score = similarityToPct(sim);
    compatibility[jobCode] = score;

    selectedJobMeta[jobCode] = {
      referenceJobName: ref.jobName,
      clusterId: ref.clusterId,
      reliable: ref.reliable,
      silhouette: ref.silhouette,
      referenceWeights: ref.weights
    };

    if (!bestMatch || score > bestMatch.score) {
      bestMatch = {
        jobCode,
        score,
        referenceJobName: ref.jobName,
        referenceWeights: ref.weights
      };
    }

    recommendations[jobCode] = Object.values(input.catalog)
      .filter((candidate) => {
        return (
          candidate.clusterId === ref.clusterId &&
          candidate.reliable === true &&
          candidate.code !== ref.code
        );
      })
      .map((candidate) => {
        const candidateWeightsForSimilarity = normalizedWeightProfile(
          applyAxisMask(candidate.weights, axesForSimilarity)
        );
        const similarity = cosineSim(similarityProfile, candidateWeightsForSimilarity);
        return {
          code: candidate.code,
          name: candidate.jobName,
          clusterId: candidate.clusterId,
          similarity,
          similarityPct: similarityToPct(similarity),
          weights: candidate.weights
        };
      })
      .sort((a, b) => b.similarity - a.similarity)
      .slice(0, topN);
  }

  const axisEvidenceSummary = buildAxisEvidenceSummary(evaluationLogs, axisCoverage);
  const jobInsights = buildJobInsights({
    selectedJobs: input.selectedJobs,
    missionScoresByJob: input.missionScoresByJob,
    compatibility,
    evaluationLogs,
    catalog: input.catalog,
    measuredAxes: axesForSimilarity
  });

  return {
    axisProfile,
    similarityProfile,
    compatibility,
    selectedJobMeta,
    recommendations,
    bestMatch,
    axisEvidenceSummary,
    axisCoverage,
    measuredAxes,
    jobInsights
  };
}
