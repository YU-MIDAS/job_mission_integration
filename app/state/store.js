export const AXES = ["AX1", "AX2", "AX3", "AX4", "AX5"];

export function emptyMissionScore() {
  return Object.fromEntries(AXES.map((axis) => [axis, 0]));
}

export function emptyAxisProfile() {
  return Object.fromEntries(AXES.map((axis) => [axis, 0]));
}

export function emptyAxisEvidenceSummary() {
  return Object.fromEntries(AXES.map((axis) => [axis, {
    score: 0,
    confidence: 0,
    evidence: [],
    flags: [],
    sources: 0
  }]));
}

export function createInitialState() {
  return {
    screen: "select",
    selectedJobs: [],
    preqIndex: 0,
    preqAnswers: {},
    missionJobIndex: 0,
    missionStepIndex: 0,
    missionAnswer: "",
    selectedOption: null,
    missionScores: {},
    evaluationLogs: [],
    axisProfile: null,
    compatibility: {},
    pendingScores: [],
    resultSummary: null,
    resultError: null
  };
}

export const state = createInitialState();

export function resetState() {
  Object.assign(state, createInitialState());
}
