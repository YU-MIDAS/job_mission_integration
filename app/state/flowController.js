const AXES = ["AX1", "AX2", "AX3", "AX4", "AX5"];

function buildZeroMissionScore() {
  return Object.fromEntries(AXES.map((axis) => [axis, 0]));
}

function buildUnavailableEvaluation(mission, reason) {
  return {
    mission_id: mission.mission_id,
    prompt_version: "client-unavailable-v1",
    source: "unavailable",
    flags: ["low_confidence", "ambiguous"],
    axes: Object.fromEntries(AXES.map((axis) => [axis, {
      score: 0,
      confidence: 0,
      evidence: [],
      reason: `채점 중 오류가 발생했습니다. ${reason}`
    }]))
  };
}

export function bindAppEvents(ctx) {
  const {
    state,
    getCurrentMission,
    scoreAnswer,
    saveEvaluationLog,
    render,
    refreshMissionNextButton,
    advanceMissionFlow,
    resetState
  } = ctx;

  document.querySelectorAll(".jc").forEach((element) => element.addEventListener("click", () => {
    const jobCode = element.dataset.jc;
    // 단일 선택 모드: 같은 카드를 다시 누르면 해제, 다른 카드를 누르면 교체
    if (state.selectedJobs.length === 1 && state.selectedJobs[0] === jobCode) {
      state.selectedJobs = [];
    } else {
      state.selectedJobs = [jobCode];
    }
    render();
  }));

  const byId = (id) => document.getElementById(id);

  byId("btn-start")?.addEventListener("click", () => {
    state.screen = "preq";
    state.preqIndex = 0;
    render();
  });

  byId("btn-pnext")?.addEventListener("click", () => {
    const jobCode = state.selectedJobs[state.preqIndex];
    state.preqAnswers[jobCode] = {
      q1: byId("pq1")?.value || "",
      q2: byId("pq2")?.value || ""
    };
    if (state.preqIndex < state.selectedJobs.length - 1) {
      state.preqIndex += 1;
      render();
      return;
    }
    state.screen = "mission";
    state.missionJobIndex = 0;
    state.missionStepIndex = 0;
    state.missionAnswer = "";
    state.selectedOption = null;
    render();
  });

  byId("btn-pback")?.addEventListener("click", () => {
    state.preqIndex -= 1;
    render();
  });

  byId("m-ans")?.addEventListener("input", (event) => {
    state.missionAnswer = event.target.value;
    refreshMissionNextButton();
  });

  byId("btn-mnext")?.addEventListener("click", () => {
    const mission = getCurrentMission();
    if (!mission) return;
    const answer = state.missionAnswer;
    if (!state.missionScores[mission.job_code]) state.missionScores[mission.job_code] = [];

    const pending = scoreAnswer(answer, mission)
      .then((scored) => {
        state.missionScores[mission.job_code].push(scored.missionScore);
        saveEvaluationLog({
          schema_version: 1,
          session_id: state.sessionId || (state.sessionId = crypto.randomUUID?.() || String(Date.now())),
          created_at: new Date().toISOString(),
          user_key: "local-anonymous",
          job_code: mission.job_code,
          mission_key: mission.key || null,
          mission_id: mission.mission_id,
          mission_title: mission.title,
          answer,
          prompt_version: scored.evaluation.prompt_version,
          evaluation: scored.evaluation,
          mission_score: scored.missionScore
        });
      })
      .catch((error) => {
        const reason = (error && error.message) ? error.message : String(error);
        const missionScore = buildZeroMissionScore();
        state.missionScores[mission.job_code].push(missionScore);
        const evaluation = buildUnavailableEvaluation(mission, reason);
        saveEvaluationLog({
          schema_version: 1,
          session_id: state.sessionId || (state.sessionId = crypto.randomUUID?.() || String(Date.now())),
          created_at: new Date().toISOString(),
          user_key: "local-anonymous",
          job_code: mission.job_code,
          mission_key: mission.key || null,
          mission_id: mission.mission_id,
          mission_title: mission.title,
          answer,
          prompt_version: evaluation.prompt_version,
          evaluation,
          mission_score: missionScore
        });
        console.warn("Background score failed:", error);
      });

    state.pendingScores.push(pending);
    advanceMissionFlow();
  });

  byId("btn-restart")?.addEventListener("click", () => {
    resetState();
    delete state.sessionId;
    try {
      localStorage.removeItem("jobsim:evaluation_logs:v1");
    } catch (_) {
      // ignore localStorage failures
    }
    render();
  });
}

export function refreshMissionNextButton(ctx) {
  const { state, getCurrentMission } = ctx;
  const button = document.getElementById("btn-mnext");
  if (!button) return;
  const mission = getCurrentMission();
  if (!mission) return;
  const enabled = state.missionAnswer.trim().length > 10;
  button.disabled = !enabled;
}

export function advanceMissionFlow(ctx) {
  const { appNode, state, getMissionsByJobCode, fetchResultSummary, render } = ctx;
  const jobCode = state.selectedJobs[state.missionJobIndex];
  const missions = getMissionsByJobCode(jobCode);

  if (state.missionStepIndex < missions.length - 1) {
    state.missionStepIndex += 1;
    state.missionAnswer = "";
    render();
    return;
  }

  if (state.missionJobIndex < state.selectedJobs.length - 1) {
    state.missionJobIndex += 1;
    state.missionStepIndex = 0;
    state.missionAnswer = "";
    render();
    return;
  }

  const showResult = async () => {
    await fetchResultSummary();
    state.screen = "result";
    render();
  };

  if (state.pendingScores.length > 0) {
    appNode.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:60vh;gap:20px">
      <div class="loading-dots"><span></span><span></span><span></span></div>
      <div style="font-size:16px;font-weight:600">결과를 분석하고 있어요</div>
      <div style="font-size:13px;color:var(--sub);text-align:center">AI가 답변을 꼼꼼히 검토하고 있어요.<br>잠시만 기다려주세요.</div>
    </div>`;
    Promise.all(state.pendingScores).then(async () => {
      state.pendingScores = [];
      await showResult();
    });
    return;
  }

  void showResult();
}
