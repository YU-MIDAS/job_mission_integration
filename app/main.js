/* ══════════════════════════════════════════════════════
   JOBSIM v3
   채점: 서버 /api/evaluate
   결과집계/추천/증거요약/사분면: 서버 /api/recommendations
   프론트는 입력/렌더 중심
══════════════════════════════════════════════════════ */

import {
  AXES,
  state,
  resetState,
  emptyMissionScore,
  emptyAxisProfile,
  emptyAxisEvidenceSummary
} from "./state/store.js";
import {
  bootstrapCatalogRequest,
  sendEvaluationLogRequest,
  evaluateAnswerRequest,
  recommendationsRequest
} from "./api-client/jobsimApi.js";
import { loadMissionsCatalog } from "./domain/missionsCatalog.js";
import { createEvaluationWorkflow } from "./domain/evaluationWorkflow.js";
import {
  renderEvidenceSection,
  renderQuadSection
} from "./components/resultSections.js";
import {
  renderCompatibilitySection,
  renderGapSection,
  renderJobIntroSection,
  renderRadarSection,
  renderRecommendationsSection
} from "./components/resultDashboard.js";
import {
  renderMissionScreen,
  renderPreQuestionScreen,
  renderSelectScreen
} from "./components/missionScreens.js";
import {
  bindAppEvents,
  refreshMissionNextButton as refreshMissionNextButtonAction,
  advanceMissionFlow as advanceMissionFlowAction
} from "./state/flowController.js";
import {
  getCurrentMission,
  getDoneMissionCount,
  getMissionsByJobCode,
  getTotalMissionCount,
  isLastMission
} from "./state/missionProgress.js";

const AX_LABELS = { AX1: "정보분석·논리", AX2: "관찰·탐색", AX3: "전략·판단", AX4: "리더십·조직", AX5: "대인서비스" };
const AX_META = {
  AX1: { icon: "📊", name: "데이터로 생각해요", desc: "수치·자료를 분석하고 논리적으로 판단하는 능력", examples: ["데이터 비교", "원인·결과 추론", "수치 기반 판단"] },
  AX2: { icon: "🔍", name: "꼼꼼하게 관찰해요", desc: "이상 현상을 발견하고 변수를 탐색하는 능력", examples: ["패턴 발견", "현장 관찰", "추가 조사 제안"] },
  AX3: { icon: "♟️", name: "전략적으로 판단해요", desc: "우선순위를 정하고 선택지를 비교해 결정하는 능력", examples: ["우선순위 설정", "단기·장기 구분", "선택지 비교"] },
  AX4: { icon: "👥", name: "팀을 이끌어요", desc: "역할을 나누고 팀·조직을 조율하는 능력", examples: ["역할 분담", "보고 구조화", "팀 단위 조율"] },
  AX5: { icon: "💬", name: "사람을 배려해요", desc: "상대방 입장을 고려하고 공감하며 소통하는 능력", examples: ["고객 공감", "불만 원인 파악", "UX 관점 제안"] }
};
const LEVEL_INFO = [
  { label: "근거 없음", emoji: "—", color: "var(--muted)", msg: "이 능력이 답변에서 확인되지 않았어요." },
  { label: "기초", emoji: "🟡", color: "#c4a84a", msg: "기본적인 내용은 언급했어요. 조금 더 구체적으로 써봐요." },
  { label: "보통", emoji: "🔵", color: "var(--accent)", msg: "괜찮은 답변이에요. 더 깊이 있으면 더 좋아요." },
  { label: "좋음", emoji: "🟢", color: "var(--success)", msg: "잘했어요! 이 능력이 잘 드러났어요." },
  { label: "탁월", emoji: "⭐", color: "#f5c842", msg: "아주 뛰어나요! 높은 수준의 분석력을 보여줬어요." }
];
const RADAR_SCALE = 2.8;

let JOB_DEFS = [];
let JOB_INFO = {};
let ALL_MISSIONS = [];
let MISSIONS_INDEX = null;
let LOAD_ERROR = null;

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch]));
}

function inferEvaluationSource(evaluation) {
  const source = evaluation?.source;
  if (source) return source;
  const promptVersion = String(evaluation?.prompt_version || "");
  if (promptVersion.includes("unavailable")) return "unavailable";
  if (promptVersion.includes("fallback")) return "heuristic";
  if (promptVersion.includes("precheck")) return "precheck";
  return "llm";
}

const evaluationWorkflow = createEvaluationWorkflow({
  state,
  axes: AXES,
  emptyMissionScore,
  emptyAxisProfile,
  emptyAxisEvidenceSummary,
  sendEvaluationLogRequest,
  evaluateAnswerRequest,
  recommendationsRequest
});
const {
  saveEvaluationLog,
  scoreAnswer,
  fetchResultSummary
} = evaluationWorkflow;

function getMissions(jc) { return getMissionsByJobCode(ALL_MISSIONS, jc); }
function curMission() { return getCurrentMission({ allMissions: ALL_MISSIONS, state }); }
function totalM() { return getTotalMissionCount({ allMissions: ALL_MISSIONS, selectedJobs: state.selectedJobs }); }
function doneM() { return getDoneMissionCount({ allMissions: ALL_MISSIONS, state }); }
function isLast() { return isLastMission({ allMissions: ALL_MISSIONS, state }); }

const APP = document.getElementById("app");
function render() {
  const fn = { select: rSelect, preq: rPreq, mission: rMission, result: rResult }[state.screen];
  if (fn) {
    APP.innerHTML = fn();
    bindEvents();
  }
}
function brand() { return "<div class=\"brand\"><div class=\"brand-logo\">JS</div><span class=\"brand-name\">JOBSIM</span></div>"; }

function rSelect() {
  return renderSelectScreen({
    state,
    jobDefs: JOB_DEFS,
    renderBrand: brand
  });
}

function rPreq() {
  return renderPreQuestionScreen({
    state,
    jobDefs: JOB_DEFS,
    renderBrand: brand
  });
}

function rMission() {
  const mission = curMission();
  if (!mission) return "<p>미션을 찾을 수 없습니다.</p>";
  const jobCode = state.selectedJobs[state.missionJobIndex];
  const jobDef = JOB_DEFS.find((j) => j.job_code === jobCode);
  if (!jobDef) return "<p>직무 정보를 찾을 수 없습니다.</p>";
  return renderMissionScreen({
    state,
    mission,
    jobDef,
    missionProgressPct: Math.round(doneM() / totalM() * 100),
    missionCount: getMissions(jobCode).length,
    renderBrand: brand,
    isLastMission: isLast(),
    esc
  });
}

function rResult() {
  const nonLlmCount = (state.evaluationLogs || [])
    .filter((log) => inferEvaluationSource(log?.evaluation) !== "llm")
    .length;
  const warn = state.resultError
    ? `<div class="log-note" style="margin-bottom:16px">서버 결과 분석에 실패해 기본값으로 표시 중입니다. (${esc(state.resultError)})</div>`
    : "";
  const sourceWarn = nonLlmCount > 0
    ? `<div class="log-note" style="margin-bottom:16px">임시 추정 결과가 포함되어 있습니다. (비LLM 평가 ${nonLlmCount}건)</div>`
    : "";
  return `${brand()}
  <h1 class="stitle">미션 결과가 나왔어요!</h1>
  <p class="sdesc">답변 패턴을 분석해서 당신만의 강점과 어울리는 직업을 찾아봤어요.</p>
  ${warn}${sourceWarn}
  ${rRadar()}${rEvidence()}${rJobIntro()}${rCompat()}${rQuad()}${rRec()}${rGap()}${rEvaluationLogSummary()}
  <hr/><button class="btn btn-g" id="btn-restart">↺ 처음부터 다시하기</button>`;
}

function rRadar() {
  return renderRadarSection({
    state,
    axes: AXES,
    axisLabels: AX_LABELS,
    axisMeta: AX_META,
    levelInfo: LEVEL_INFO,
    radarScale: RADAR_SCALE,
    emptyAxisProfile,
    jobDefs: JOB_DEFS
  });
}

function rEvidence() {
  const hasLlmEvidence = (state.evaluationLogs || [])
    .some((log) => inferEvaluationSource(log?.evaluation) === "llm");
  if (!hasLlmEvidence) {
    return `<div class="rs"><div class="rs-title">✨ 능력별 상세 피드백</div><div class="log-note">임시 추정 모드에서는 인용 근거 카드가 생략됩니다.</div></div>`;
  }
  return renderEvidenceSection({
    axes: AXES,
    summary: state.resultSummary?.axisEvidenceSummary || emptyAxisEvidenceSummary(),
    axisMeta: AX_META,
    levelInfo: LEVEL_INFO,
    esc
  });
}

function rEvaluationLogSummary() { return ""; }

function rJobIntro() {
  return renderJobIntroSection({
    state,
    jobDefs: JOB_DEFS,
    jobInfo: JOB_INFO,
    esc
  });
}

function rCompat() {
  return renderCompatibilitySection({
    state,
    jobDefs: JOB_DEFS
  });
}

function rQuad() {
  return renderQuadSection({
    selectedJobs: state.selectedJobs,
    jobDefs: JOB_DEFS,
    jobInsights: state.resultSummary?.jobInsights || {}
  });
}

function rRec() {
  return renderRecommendationsSection({
    state,
    jobDefs: JOB_DEFS,
    axes: AXES,
    axisLabels: AX_LABELS,
    radarScale: RADAR_SCALE
  });
}

function rGap() {
  return renderGapSection({
    state,
    jobDefs: JOB_DEFS,
    getMissionsByJobCode: getMissions,
    esc
  });
}

function flowContext() {
  const ctx = {
    appNode: APP,
    state,
    getCurrentMission: curMission,
    getMissionsByJobCode: getMissions,
    scoreAnswer,
    saveEvaluationLog,
    fetchResultSummary,
    render,
    resetState
  };
  return {
    ...ctx,
    refreshMissionNextButton: () => refreshMissionNextButtonAction(ctx),
    advanceMissionFlow: () => advanceMissionFlowAction(ctx)
  };
}

function bindEvents() { bindAppEvents(flowContext()); }

async function bootstrap() {
  APP.innerHTML = `<div class="boot-state">
    <div class="loading-dots"><span></span><span></span><span></span></div>
    <div class="boot-msg">미션을 불러오는 중…</div>
    <div class="boot-sub">missions/index.json 과 각 미션 JSON 을 가져오고 있어요.</div>
  </div>`;
  try {
    let catalog;
    try {
      catalog = await bootstrapCatalogRequest();
    } catch (apiErr) {
      console.warn("Bootstrap API unavailable. Falling back to static mission files.", apiErr);
      catalog = await loadMissionsCatalog({
        axes: AXES,
        axisMeta: AX_META
      });
    }
    MISSIONS_INDEX = catalog.missionsIndex;
    JOB_DEFS = catalog.jobDefs;
    JOB_INFO = catalog.jobInfo;
    ALL_MISSIONS = catalog.allMissions;
  } catch (err) {
    console.error("미션 로드 실패:", err);
    LOAD_ERROR = (err && err.message) ? err.message : String(err);
    APP.innerHTML = `<div class="boot-state err">
      <div class="boot-msg">미션을 불러올 수 없습니다.</div>
      <div class="boot-sub">${esc(LOAD_ERROR)}<br><br>
      ・ 정적 서버를 <code>app/</code> 가 아니라 프로젝트 루트에서 실행했는지 확인하세요.<br>
      ・ <code>missions/index.json</code> 과 각 미션 파일 경로가 존재하는지 확인하세요.</div>
    </div>`;
    return;
  }
  if (!ALL_MISSIONS.length) {
    APP.innerHTML = `<div class="boot-state err">
      <div class="boot-msg">표시할 미션이 없습니다.</div>
      <div class="boot-sub">missions/index.json 의 missions 배열을 확인하세요.</div>
    </div>`;
    return;
  }
  render();
}
bootstrap();
