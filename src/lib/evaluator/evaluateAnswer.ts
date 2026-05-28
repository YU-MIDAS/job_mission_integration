import OpenAI from "openai";
import { zodTextFormat } from "openai/helpers/zod";
import { AXES, Evaluation, LlmEvaluationSchema, MissionScore } from "./schema.js";
import { buildEvaluationPrompt, PROMPT_VERSION, RubricCriterion, SYSTEM_PROMPT } from "./prompts.js";

type Mission = {
  mission_id: string;
  job_name?: string;
  title?: string;
  scenario?: string;
  task?: string;
  axis_signals?: Partial<Record<(typeof AXES)[number], number>>;
  /** @deprecated 구 스키마 키워드 힌트. 새 스키마는 rubric_criteria 사용. */
  rubric?: Record<string, string[]>;
  /** 새 스키마 (mission_output.v1): 도메인별 채점 기준 */
  rubric_criteria?: RubricCriterion[];
  /** 새 스키마 (mission_output.v1): 고득점 답변이 다뤄야 할 핵심 인사이트 */
  expected_insights?: string[];
};

type EvaluateAnswerInput = {
  mission: Mission;
  answer: string;
};

type EvaluateAnswerResult = {
  evaluation: Evaluation;
  missionScore: MissionScore;
};

let openaiClient: OpenAI | null = null;
const DEFAULT_EVAL_MODEL = "gpt-5-nano";

function getOpenAIClient() {
  if (!process.env.OPENAI_API_KEY) return null;
  if (!openaiClient) {
    openaiClient = new OpenAI({
      apiKey: process.env.OPENAI_API_KEY
    });
  }
  return openaiClient;
}

function emptyAxis(reason?: string) {
  return {
    score: 0,
    confidence: 0,
    evidence: [],
    reason: reason ?? "답변에서 해당 축과 관련된 구체적인 행동이나 근거를 찾을 수 없습니다."
  };
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function normalizeText(value: string) {
  return value.replace(/\s+/g, " ").trim().toLowerCase();
}

function countNumbers(value: string) {
  return value.match(/\d+(?:[.,]\d+)?%?|\d+(?:[.,]\d+)?/g)?.length ?? 0;
}

function uniqueTokenRatio(value: string) {
  const tokens = normalizeText(value).split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return 0;
  return new Set(tokens).size / tokens.length;
}

function tokenCount(value: string) {
  return normalizeText(value).split(/\s+/).filter(Boolean).length;
}

function repeatedTrigramRatio(value: string) {
  const tokens = normalizeText(value).split(/\s+/).filter(Boolean);
  if (tokens.length < 9) return 0;
  const trigrams: string[] = [];
  for (let index = 0; index <= tokens.length - 3; index += 1) {
    trigrams.push(`${tokens[index]} ${tokens[index + 1]} ${tokens[index + 2]}`);
  }
  if (!trigrams.length) return 0;
  const unique = new Set(trigrams).size;
  return 1 - (unique / trigrams.length);
}

function hasSufficientAnswerContent(answer: string) {
  const normalized = normalizeText(answer);
  const tokens = tokenCount(answer);
  const wordLike = answer.match(/[A-Za-z가-힣]{2,}/g)?.length ?? 0;
  const hasAnyNumber = /\d/.test(answer);
  const hasSentencePunctuation = /[.!?,;:]/.test(answer);
  const minLength = normalized.length >= 24;

  if (!minLength) return false;
  if (tokens >= 4) return true;
  if (wordLike >= 4) return true;
  if (tokens >= 3 && (hasAnyNumber || hasSentencePunctuation)) return true;
  return false;
}

function isLowInformationAnswer(answer: string) {
  const normalized = normalizeText(answer);
  if (!normalized) return true;
  const tokens = normalized.split(/\s+/).filter(Boolean);
  const uniqueness = uniqueTokenRatio(answer);
  const trigramRepeat = repeatedTrigramRatio(answer);
  const contentTokenCount = answer.match(/[A-Za-z가-힣0-9]{2,}/g)?.length ?? 0;
  const longRepeatedCharRun = /(.)\1{5,}/.test(normalized.replace(/\s+/g, ""));

  if (longRepeatedCharRun) return true;
  if (tokens.length >= 12 && uniqueness < 0.42) return true;
  if (tokens.length >= 10 && trigramRepeat > 0.55) return true;
  if (tokens.length >= 15 && contentTokenCount < 6) return true;
  return false;
}

function hasPromptInjectionSignal(answer: string) {
  const text = answer ?? "";
  if (!text.trim()) return false;

  const hardPatterns = [
    /(?:ignore|disregard|override|bypass|forget)\s+(?:all\s+)?(?:previous|above|prior|earlier)\s+(?:instructions?|prompts?|rules?)/i,
    /(?:ignore|disregard|override|bypass|forget)\s+(?:the\s+)?(?:system|developer)\s+(?:instructions?|prompt|rules?)/i,
    /(?:set|assign|give)\s+(?:all\s+)?(?:scores?|axes?|ax1|ax2|ax3|ax4|ax5).{0,40}(?:4|four|max(?:imum)?|100)/i,
    /<\s*\/?\s*(system|assistant|developer|tool)\b[^>]*>/i,
    /\b(?:system|assistant|developer)\s*:\s*.+/i
  ];

  const softPatterns = [
    /\breturn\b.{0,30}\bjson\b/i,
    /\bdo\s+not\s+follow\b.{0,30}\b(?:system|developer)\b/i,
    /```(?:system|assistant|developer|prompt)?/i,
    /\b(?:ignore|override|bypass)\b/i
  ];

  if (hardPatterns.some((pattern) => pattern.test(text))) return true;
  const softHits = softPatterns.reduce((count, pattern) => count + (pattern.test(text) ? 1 : 0), 0);
  return softHits >= 2;
}

function countMarkers(answer: string, markers: string[]) {
  const normalizedAnswer = normalizeText(answer);
  return markers.filter((marker) => normalizedAnswer.includes(normalizeText(marker))).length;
}

function keywordHits(answer: string, keywords: string[]) {
  const normalizedAnswer = normalizeText(answer);
  return keywords
    .map((keyword) => keyword.trim())
    .filter((keyword) => keyword && normalizedAnswer.includes(normalizeText(keyword)));
}

function quoteAppearsInAnswerStrict(answer: string, quote: string) {
  const normalizedAnswer = normalizeText(answer);
  const normalizedQuote = normalizeText(quote);
  if (!normalizedQuote) return false;
  return normalizedAnswer.includes(normalizedQuote);
}

function addFlag(evaluation: Evaluation, flag: Evaluation["flags"][number]) {
  if (!evaluation.flags.includes(flag)) evaluation.flags.push(flag);
}

function capScoresForNonLlm(evaluation: Evaluation): Evaluation {
  if (evaluation.source === "llm") return evaluation;
  const scoreCap = evaluation.source === "heuristic" ? 2 : 1;
  const confidenceCap = evaluation.source === "heuristic" ? 0.55 : 0.45;
  for (const axis of AXES) {
    const result = evaluation.axes[axis];
    if (result.score > scoreCap) {
      result.score = scoreCap;
      result.reason = `${result.reason} Capped at ${scoreCap} in ${evaluation.source ?? "non-llm"} mode.`;
    }
    result.confidence = Math.min(result.confidence, confidenceCap);
  }
  addFlag(evaluation, "low_confidence");
  return evaluation;
}

function validateAndRecalculate(answer: string, evaluation: Evaluation): Evaluation {
  const usedQuotes = new Set<string>();

  for (const axis of AXES) {
    const axisResult = evaluation.axes[axis];
    axisResult.evidence = axisResult.evidence.filter((item) => {
      const quoteKey = normalizeText(item.quote);
      const supportsAxis = item.primary_axis === axis;
      const isStrictAnswerQuote = quoteAppearsInAnswerStrict(answer, item.quote);
      const isDuplicate = usedQuotes.has(quoteKey);

      if (supportsAxis && isStrictAnswerQuote && !isDuplicate) {
        usedQuotes.add(quoteKey);
        return true;
      }

      return false;
    });

    if (axisResult.evidence.length === 0) {
      const originalReason = axisResult.reason;
      const hasLLMReason = originalReason && originalReason !== "No validated evidence." && originalReason.length > 5;
      evaluation.axes[axis] = emptyAxis(
        hasLLMReason ? `[검증 후 근거 불인정] ${originalReason}` : undefined
      );
      continue;
    }

    const primaryEvidence = axisResult.evidence.filter((item) => item.primary_axis === axis);
    const scoreEvidence = primaryEvidence.length > 0 ? primaryEvidence : axisResult.evidence;
    axisResult.score = Math.max(...scoreEvidence.map((item) => item.level));

    if (primaryEvidence.length === 0) {
      axisResult.score = Math.min(axisResult.score, 2);
      axisResult.reason = `${axisResult.reason} Secondary-only evidence; score capped.`;
    }

    if (axisResult.evidence.length < 2 && axisResult.score >= 4) {
      axisResult.score = 3;
      axisResult.reason = `${axisResult.reason} High score capped because evidence is limited.`;
    }

    axisResult.confidence = Math.min(Math.max(axisResult.confidence, 0), 1);

    if (axisResult.confidence < 0.55) {
      axisResult.score = Math.min(axisResult.score, 2);
    }
  }

  const highAxes = AXES
    .filter((axis) => evaluation.axes[axis].score >= 3)
    .sort((a, b) => {
      const ea = evaluation.axes[a];
      const eb = evaluation.axes[b];
      return (eb.confidence * 10 + eb.evidence.length) - (ea.confidence * 10 + ea.evidence.length);
    });

  if (highAxes.length >= 4) {
    highAxes.slice(2).forEach((axis) => {
      evaluation.axes[axis].score = Math.min(evaluation.axes[axis].score, 2);
      evaluation.axes[axis].confidence = Math.min(evaluation.axes[axis].confidence, 0.54);
      evaluation.axes[axis].reason = `${evaluation.axes[axis].reason} Capped to avoid all-axis high scoring.`;
    });
    addFlag(evaluation, "low_confidence");
  }

  const lowConfidence = AXES.some((axis) => {
    const result = evaluation.axes[axis];
    return result.score > 0 && (result.confidence < 0.55 || result.evidence.length === 0);
  });

  if (lowConfidence) addFlag(evaluation, "low_confidence");

  // === Enforce off_topic: if flag is set, cap all scores at 1 ===
  // This defends against the GPT marking off_topic but forgetting to lower scores.
  if (evaluation.flags.includes("off_topic")) {
    for (const axis of AXES) {
      const result = evaluation.axes[axis];
      if (result.score > 1) {
        result.score = 1;
        result.reason = `${result.reason} Capped at 1 because answer was off-topic.`;
      }
      result.confidence = Math.min(result.confidence, 0.4);
    }
  }

  if (evaluation.flags.includes("possible_prompt_injection")) {
    addFlag(evaluation, "off_topic");
    for (const axis of AXES) {
      const result = evaluation.axes[axis];
      if (result.score > 1) {
        result.score = 1;
        result.reason = `${result.reason} Capped at 1 because prompt-injection signal was detected.`;
      }
      result.confidence = Math.min(result.confidence, 0.35);
    }
  }

  return evaluation;
}

function toMissionScore(evaluation: Evaluation, mission: Mission): MissionScore {
  return Object.fromEntries(AXES.map((axis) => {
    const signal = mission.axis_signals?.[axis] ?? 0;
    return [axis, (evaluation.axes[axis].score / 4) * signal];
  })) as MissionScore;
}

function lowContentEvaluation(
  mission: Mission,
  reason: string,
  flags: Evaluation["flags"] = ["too_short", "off_topic", "ambiguous", "low_confidence"],
  source: Evaluation["source"] = "precheck"
): Evaluation {
  const axes = Object.fromEntries(AXES.map((axis) => [axis, {
    score: 0,
    confidence: 0.05,
    evidence: [],
    reason
  }])) as unknown as Evaluation["axes"];

  return {
    mission_id: mission.mission_id,
    axes,
    flags,
    source,
    prompt_version: `${PROMPT_VERSION}-precheck`
  };
}

function heuristicFallbackEvaluation(mission: Mission, answer: string, cause: string): Evaluation {
  const length = normalizeText(answer).length;
  const numberCount = countNumbers(answer);
  const uniqueness = uniqueTokenRatio(answer);
  const repetitionPenalty = uniqueness < 0.45 ? 0.9 : uniqueness < 0.6 ? 0.35 : 0;
  const lengthLevel = length >= 260 ? 3 : length >= 140 ? 2 : length >= 45 ? 1 : 0;

  // 한·영 통합 마커 — 새 스키마(한국어 답변)에도 동작하도록 한국어 키워드 추가
  const markersByAxis: Record<(typeof AXES)[number], string[]> = {
    AX1: [
      // 영어(구 스키마 호환)
      "data", "log", "metric", "rate", "ratio", "compare", "p=", "%",
      // 한국어
      "데이터", "수치", "비율", "비교", "분석", "통계", "지표", "로그", "차트",
      "추이", "증가", "감소", "평균", "전환율", "검증", "근거", "측정", "집계"
    ],
    AX2: [
      "check", "observe", "pattern", "trace", "inspect", "explore",
      "패턴", "관찰", "이상", "탐색", "확인", "추적", "발견", "파악", "점검",
      "흐름", "변화", "탐지", "모니터", "탐구"
    ],
    AX3: [
      "priority", "strategy", "hypothesis", "verify", "decide", "option", "recommend",
      "우선순위", "전략", "가설", "판단", "결정", "선택", "제안", "권고",
      "단기", "장기", "먼저", "1순위", "방향", "목표", "계획"
    ],
    AX4: [
      "team", "share", "report", "role", "approve", "department",
      "팀", "공유", "보고", "역할", "협업", "부서", "승인", "조율",
      "협의", "이해관계자", "조직"
    ],
    AX5: [
      "customer", "user", "empathy", "apologize", "communicate", "trust",
      "고객", "사용자", "공감", "소통", "신뢰", "배려", "응대",
      "경험", "불만", "만족", "안내"
    ]
  };

  // 새 스키마의 rubric_criteria 설명에서 키워드를 추가 힌트로 추출
  // (각 기준 설명의 명사 키워드 ~ 최대 3개/기준)
  const criteriaKeywordsByAxis: Partial<Record<(typeof AXES)[number], string[]>> = {};
  if (mission.rubric_criteria?.length) {
    // criteria description을 axis_signals 비례로 axes에 분배
    // (정확한 linked_evidence 매핑은 없으므로 axis_signals 비율로 근사)
    const signalTotal = AXES.reduce((s, ax) => s + (mission.axis_signals?.[ax] ?? 0), 0);
    for (const criterion of mission.rubric_criteria) {
      // description에서 4자 이상 단어만 추출 (의미있는 한국어 명사 추정)
      const words = criterion.description.match(/[가-힣]{4,}/g) ?? [];
      if (!words.length) continue;
      // 가장 높은 axis_signals를 가진 축에 할당
      const topAxis = AXES.reduce((best, ax) =>
        (mission.axis_signals?.[ax] ?? 0) > (mission.axis_signals?.[best] ?? 0) ? ax : best
      , AXES[0]);
      criteriaKeywordsByAxis[topAxis] ??= [];
      criteriaKeywordsByAxis[topAxis]!.push(...words.slice(0, 3));
    }
  }

  const axes = Object.fromEntries(AXES.map((axis) => {
    const legacyKws = mission.rubric?.[axis] ?? [];
    const criteriaKws = criteriaKeywordsByAxis[axis] ?? [];
    const allKws = [...legacyKws, ...criteriaKws];

    const hits = keywordHits(answer, allKws);
    const markerHits = countMarkers(answer, markersByAxis[axis]);
    const signal = mission.axis_signals?.[axis] ?? 0;
    const keywordLevel = Math.min(hits.length, 5);
    const markerLevel = Math.min(markerHits, 4);
    const numericLevel = axis === "AX1" ? Math.min(numberCount, 4) : 0;
    const signalLevel = signal * 4;
    const evidenceShapeBonus = keywordLevel >= 2 && (markerLevel >= 1 || numericLevel >= 1) ? 0.55 : 0;
    const raw =
      keywordLevel * 0.55 +
      markerLevel * 0.35 +
      numericLevel * 0.28 +
      signalLevel * 0.38 +
      lengthLevel * 0.22 +
      evidenceShapeBonus -
      repetitionPenalty;

    let score = 0;
    if (raw >= 4.2 && length >= 120 && uniqueness >= 0.55) score = 4;
    else if (raw >= 3.0 && length >= 80 && uniqueness >= 0.5) score = 3;
    else if (raw >= 1.65 && length >= 35 && uniqueness >= 0.45) score = 2;
    else if (raw >= 0.45) score = 1;

    // 새 스키마: rubric 키워드가 없을 때 marker만으로도 최소 score 허용
    // (구 스키마와 달리 keyword_hits=0이어도 marker가 있으면 점수를 막지 않음)
    const hasNewSchema = !legacyKws.length;
    if (!hasNewSchema && hits.length === 0 && markerHits === 0 && numericLevel === 0) {
      score = 0;
    }

    const confidence = score === 0
      ? clamp(0.08 + signal * 0.1, 0.05, 0.2)
      : clamp(
          0.16 +
          score * 0.08 +
          Math.min(hits.length, 4) * 0.035 +
          Math.min(markerHits, 3) * 0.025 +
          signal * 0.14 +
          lengthLevel * 0.025 -
          repetitionPenalty * 0.08,
          0.18,
          0.68
        );

    return [axis, {
      score,
      confidence: Number(confidence.toFixed(2)),
      evidence: [],
      reason: `Heuristic fallback (${cause}). raw=${raw.toFixed(2)}, keyword_hits=${hits.length}, marker_hits=${markerHits}, signal=${signal.toFixed(2)}, length_level=${lengthLevel}, uniqueness=${uniqueness.toFixed(2)}.`
    }];
  })) as unknown as Evaluation["axes"];

  const flags: Evaluation["flags"] = ["low_confidence"];
  if (length < 15) flags.push("too_short");
  if (uniqueTokenRatio(answer) < 0.45) flags.push("ambiguous");

  return {
    mission_id: mission.mission_id,
    axes,
    flags,
    source: "heuristic",
    prompt_version: `${PROMPT_VERSION}-fallback`
  };
}

export async function evaluateAnswer({ mission, answer }: EvaluateAnswerInput): Promise<EvaluateAnswerResult> {
  if (!hasSufficientAnswerContent(answer)) {
    const evaluation = lowContentEvaluation(
      mission,
      "답변 길이나 문장 정보가 부족해 채점을 진행하지 않았습니다. 핵심 근거 2개 이상을 포함해 다시 작성해 주세요."
    );
    const calibrated = capScoresForNonLlm(evaluation);
    return { evaluation: calibrated, missionScore: toMissionScore(calibrated, mission) };
  }

  if (isLowInformationAnswer(answer)) {
    const evaluation = lowContentEvaluation(
      mission,
      "답변의 반복 표현이 많거나 정보 밀도가 낮아 신뢰 가능한 채점을 진행하지 않았습니다. 핵심 근거와 구체 행동을 포함해 다시 작성해 주세요.",
      ["off_topic", "ambiguous", "low_confidence"],
      "precheck"
    );
    const calibrated = capScoresForNonLlm(evaluation);
    return { evaluation: calibrated, missionScore: toMissionScore(calibrated, mission) };
  }

  if (hasPromptInjectionSignal(answer)) {
    const evaluation = lowContentEvaluation(
      mission,
      "답변에서 채점 규칙을 바꾸려는 지시문 형태가 감지되어 채점을 중단했습니다. 미션 해결 근거만 포함해 다시 작성해 주세요.",
      ["possible_prompt_injection", "off_topic", "ambiguous", "low_confidence"],
      "precheck"
    );
    const calibrated = capScoresForNonLlm(evaluation);
    return { evaluation: calibrated, missionScore: toMissionScore(calibrated, mission) };
  }

  const openai = getOpenAIClient();
  if (!openai) {
    const evaluation = heuristicFallbackEvaluation(mission, answer, "missing_api_key");
    const calibrated = capScoresForNonLlm(evaluation);
    return { evaluation: calibrated, missionScore: toMissionScore(calibrated, mission) };
  }

  let evaluation: Evaluation;
  try {
    const response = await openai.responses.parse({
      model: process.env.OPENAI_EVAL_MODEL ?? DEFAULT_EVAL_MODEL,
      input: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: buildEvaluationPrompt({ mission, answer }) }
      ],
      text: {
        format: zodTextFormat(LlmEvaluationSchema, "jobsim_evaluation")
      }
    });

    const parsed = response.output_parsed;
    if (!parsed) {
      throw new Error("OpenAI returned an empty evaluation.");
    }

    parsed.prompt_version = parsed.prompt_version || PROMPT_VERSION;
    evaluation = validateAndRecalculate(answer, {
      ...parsed,
      source: "llm"
    });
  } catch (error) {
    const cause = error instanceof Error ? error.message : "unknown_llm_error";
    evaluation = heuristicFallbackEvaluation(mission, answer, cause);
  }

  const calibrated = capScoresForNonLlm(evaluation);
  const missionScore = toMissionScore(calibrated, mission);

  return { evaluation: calibrated, missionScore };
}
