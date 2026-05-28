const EVAL_LOG_KEY = "jobsim:evaluation_logs:v1";

function readStoredEvaluationLogs() {
  try {
    return JSON.parse(localStorage.getItem(EVAL_LOG_KEY) || "[]");
  } catch (_) {
    return [];
  }
}

function buildUnavailableEvaluation({ axes, answer, mission, reason }) {
  const flags = ["low_confidence"];
  if ((answer || "").trim().length < 15) flags.push("too_short");

  const axisScores = Object.fromEntries(axes.map((axis) => [axis, {
    score: 0,
    confidence: 0,
    evidence: [],
    reason: `서버 채점을 완료하지 못했습니다. ${reason}`
  }]));

  return {
    mission_id: mission.mission_id,
    prompt_version: "server-unavailable-v1",
    source: "unavailable",
    flags,
    axes: axisScores
  };
}

function buildFallbackResultSummary({ state, emptyAxisProfile, emptyAxisEvidenceSummary }) {
  const axisCoverage = Object.fromEntries(
    ["AX1", "AX2", "AX3", "AX4", "AX5"].map((axis) => [axis, {
      totalSignal: 0,
      missionCount: 0,
      measured: true
    }])
  );
  return {
    axisProfile: emptyAxisProfile(),
    similarityProfile: emptyAxisProfile(),
    compatibility: Object.fromEntries(state.selectedJobs.map((jobCode) => [jobCode, 0])),
    selectedJobMeta: Object.fromEntries(state.selectedJobs.map((jobCode) => [jobCode, {
      referenceJobName: null,
      clusterId: null,
      reliable: false,
      silhouette: null,
      referenceWeights: null
    }])),
    recommendations: Object.fromEntries(state.selectedJobs.map((jobCode) => [jobCode, []])),
    bestMatch: null,
    axisEvidenceSummary: emptyAxisEvidenceSummary(),
    axisCoverage,
    measuredAxes: ["AX1", "AX2", "AX3", "AX4", "AX5"],
    jobInsights: Object.fromEntries(state.selectedJobs.map((jobCode) => [jobCode, {
      performance: 0.5,
      emotion: 0.5,
      compatibility: 0,
      quadrant: "q4",
      topAxis: "AX1",
      lowAxis: "AX1",
      missionCount: 0,
      label: "수행 낮음 · 몰입 추정 낮음",
      detail: "평가 데이터가 없어 기본값으로 표시됩니다."
    }]))
  };
}

export function createEvaluationWorkflow({
  state,
  axes,
  emptyMissionScore,
  emptyAxisProfile,
  emptyAxisEvidenceSummary,
  sendEvaluationLogRequest,
  evaluateAnswerRequest,
  recommendationsRequest
}) {
  async function sendEvaluationLog(entry) {
    try {
      await sendEvaluationLogRequest(entry);
    } catch (error) {
      console.warn("Server log save failed. Local cache only.", error);
    }
  }

  function saveEvaluationLog(entry) {
    state.evaluationLogs.push(entry);
    const logs = [...readStoredEvaluationLogs(), entry].slice(-200);
    try {
      localStorage.setItem(EVAL_LOG_KEY, JSON.stringify(logs));
    } catch (_) {
      // ignore localStorage failures
    }
    void sendEvaluationLog(entry);
  }

  async function scoreAnswer(answer, mission) {
    const rawEval = mission._raw?.evaluation || {};
    const payload = {
      mission_id: mission.mission_id,
      job_name: mission.job_name,
      title: mission.title,
      scenario: mission.scenario,
      task: mission.task,
      axis_signals: mission.axis_signals,
      rubric: mission.rubric,
      expected_insights: rawEval.expected_insights || [],
      rubric_criteria: (rawEval.rubric || []).map((item) => ({
        criterion: item.criterion,
        description: item.description || "",
        points: item.points || 0
      }))
    };

    try {
      const data = await evaluateAnswerRequest({ answer, mission: payload });
      return { missionScore: data.missionScore, evaluation: data.evaluation };
    } catch (error) {
      const reason = (error && error.message) ? error.message : String(error);
      console.warn("Server evaluation failed. Saving unavailable placeholder evaluation.", error);
      return {
        missionScore: emptyMissionScore(),
        evaluation: buildUnavailableEvaluation({ axes, answer, mission, reason })
      };
    }
  }

  async function fetchResultSummary() {
    try {
      const data = await recommendationsRequest({
        selectedJobs: state.selectedJobs,
        missionScoresByJob: state.missionScores,
        evaluationLogs: state.evaluationLogs,
        topN: 3
      });
      state.resultSummary = data;
      state.axisProfile = data.axisProfile;
      state.compatibility = data.compatibility;
      state.resultError = null;
    } catch (error) {
      const reason = (error && error.message) ? error.message : String(error);
      console.warn("Server recommendations failed. Fallback to empty summary.", error);
      state.resultSummary = buildFallbackResultSummary({
        state,
        emptyAxisProfile,
        emptyAxisEvidenceSummary
      });
      state.axisProfile = state.resultSummary.axisProfile;
      state.compatibility = state.resultSummary.compatibility;
      state.resultError = reason;
    }
  }

  return {
    saveEvaluationLog,
    scoreAnswer,
    fetchResultSummary
  };
}
