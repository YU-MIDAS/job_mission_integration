function getApiToken() {
  try {
    return localStorage.getItem("jobsim:api_token") || "";
  } catch (_) {
    return "";
  }
}

function buildHeaders() {
  const token = getApiToken().trim();
  return {
    "Content-Type": "application/json",
    ...(token ? { "x-api-token": token } : {})
  };
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: buildHeaders(),
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw new Error(await response.text());
  }

  return response.json();
}

async function getJson(url) {
  const token = getApiToken().trim();
  const response = await fetch(url, {
    method: "GET",
    headers: token ? { "x-api-token": token } : undefined
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function bootstrapCatalogRequest() {
  const data = await getJson("/api/bootstrap");
  if (!Array.isArray(data?.jobDefs) || !Array.isArray(data?.allMissions) || !data?.jobInfo) {
    throw new Error("부팅 응답 형식이 올바르지 않습니다.");
  }
  return data;
}

export async function sendEvaluationLogRequest(entry) {
  return postJson("/api/logs", entry);
}

export async function evaluateAnswerRequest({ answer, mission }) {
  const data = await postJson("/api/evaluate", { answer, mission });
  if (!data?.missionScore || !data?.evaluation) {
    throw new Error("채점 응답 형식이 올바르지 않습니다.");
  }
  return data;
}

export async function recommendationsRequest({
  selectedJobs,
  missionScoresByJob,
  evaluationLogs,
  topN
}) {
  const data = await postJson("/api/recommendations", {
    selectedJobs,
    missionScoresByJob,
    evaluationLogs,
    topN
  });

  if (!data?.axisProfile || !data?.compatibility || !data?.axisEvidenceSummary || !data?.jobInsights) {
    throw new Error("결과 분석 응답 형식이 올바르지 않습니다.");
  }

  return data;
}
