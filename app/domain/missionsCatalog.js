export const MISSIONS_BASE = "../missions";
const DATA_BASE = "../data/processed";
const JOB_SUMMARY_BY_CODE = {
  K000000997: "상품기획자는 시장·고객 데이터를 바탕으로 상품 콘셉트와 출시 우선순위를 기획하는 직무입니다.",
  K000001080: "데이터분석가는 서비스·운영 데이터를 분석해 문제 원인과 개선 포인트를 찾고 의사결정을 지원하는 직무입니다.",
  K000007519: "보험상품개발자는 위험·손해율·가입자 데이터를 분석해 보험 보장 구조와 상품안을 설계하는 직무입니다.",
  K000001179: "투자분석가는 기업·산업·재무 정보를 분석해 투자 매력도와 리스크를 평가하고 투자 판단을 지원하는 직무입니다."
};

function resolveClusterId(jobCode, clusterByJobCode) {
  return clusterByJobCode[jobCode] || "UNMAPPED";
}

function actionLabelFromTask(task) {
  const expectedAction = String(task?.expected_action || "").toLowerCase();
  const instruction = String(task?.instruction || "");

  if (expectedAction.includes("observe") || /관찰|확인|파악|점검/.test(instruction)) return "지표·현황 점검";
  if (expectedAction.includes("diagnose") || /원인|진단|분석/.test(instruction)) return "원인 진단";
  if (expectedAction.includes("recommend") || /개선|제안|우선|전략/.test(instruction)) return "개선안 제안";
  if (expectedAction.includes("plan") || /계획|설계/.test(instruction)) return "계획 수립";
  return "";
}

function buildDailyDescription({ jobName, domain, missions, avgMinutes }) {
  const missionTitles = missions.map((item) => item.title).filter(Boolean).slice(0, 2);
  const titleText = missionTitles.length ? missionTitles.join(" / ") : `${jobName} 실무 과제`;

  const actionSet = new Set();
  missions.forEach((item) => {
    const tasks = item?._raw?.mission?.tasks || [];
    tasks.forEach((task) => {
      const label = actionLabelFromTask(task);
      if (label) actionSet.add(label);
    });
  });
  const actionFlow = Array.from(actionSet).slice(0, 3);
  const flowText = actionFlow.length
    ? actionFlow.join(" → ")
    : "지표·현황 점검 → 원인 진단 → 개선안 제안";

  const domainPrefix = domain
    ? `${domain} 데이터를 다루며`
    : `${jobName} 관련 데이터를 다루며`;

  return `${domainPrefix} "${titleText}" 같은 과제를 수행해요. 보통 ${flowText} 순서로 진행하고, 미션 1개 기준 약 ${avgMinutes}분 정도의 분석·정리 작업이 필요해요.`;
}

function buildJobSummary({ jobCode, jobName, jobField }) {
  if (JOB_SUMMARY_BY_CODE[jobCode]) {
    return JOB_SUMMARY_BY_CODE[jobCode];
  }
  if (jobField) {
    return `${jobName}는 ${jobField} 분야에서 정보를 분석하고 실행안을 도출해 의사결정을 지원하는 직무입니다.`;
  }
  return `${jobName} 직무를 미션으로 체험합니다.`;
}

function toLegacyMissionView({ indexEntry, raw, clusterByJobCode }) {
  const mission = raw.mission || {};
  const scenario = mission.scenario || {};
  const facts = raw.mission_facts || {};

  const scenarioText = [
    scenario.context,
    scenario.goal ? `목표: ${scenario.goal}` : "",
    facts.main_issue ? `핵심 이슈: ${facts.main_issue}` : "",
    facts.decision_goal ? `의사결정 목표: ${facts.decision_goal}` : "",
    (scenario.constraints || []).length ? `제약: ${(scenario.constraints || []).join(" / ")}` : ""
  ].filter(Boolean).join("\n");

  const tasks = mission.tasks || [];
  const taskText = tasks.length
    ? tasks.map((task, idx) => `${idx + 1}) ${task.instruction}`).join("\n")
    : (mission.title || "미션을 수행하고 답변을 작성하세요.");

  return {
    mission_id: raw.mission_id,
    key: indexEntry.key,
    job_code: indexEntry.job_cd,
    job_name: indexEntry.job_name,
    cluster_id: resolveClusterId(indexEntry.job_cd, clusterByJobCode),
    title: mission.title,
    scenario: scenarioText,
    task: taskText,
    input_type: "text",
    options: null,
    is_sjt: false,
    axis_signals: raw.axis_signals_derived || indexEntry.axis_signals || {
      AX1: 0.2,
      AX2: 0.2,
      AX3: 0.2,
      AX4: 0.2,
      AX5: 0.2
    },
    rubric: { AX1: [], AX2: [], AX3: [], AX4: [], AX5: [] },
    _raw: raw
  };
}

export async function loadMissionsCatalog({ axes, axisMeta }) {
  const indexResponse = await fetch(`${MISSIONS_BASE}/index.json`, { cache: "no-cache" });
  if (!indexResponse.ok) {
    throw new Error(`missions/index.json 을 불러올 수 없습니다 (HTTP ${indexResponse.status})`);
  }
  const missionsIndex = await indexResponse.json();
  let clusterByJobCode = {};
  try {
    const weightsRes = await fetch(`${DATA_BASE}/job_weights.json`, { cache: "no-cache" });
    if (weightsRes.ok) {
      const weights = await weightsRes.json();
      clusterByJobCode = Object.fromEntries(
        Object.entries(weights || {}).map(([jobCode, value]) => [
          jobCode,
          (typeof value?.cluster_id === "string" && value.cluster_id.trim()) ? value.cluster_id.trim() : "UNMAPPED"
        ])
      );
    }
  } catch (_) {
    clusterByJobCode = {};
  }

  const loaded = await Promise.all(missionsIndex.missions.map(async (entry) => {
    const response = await fetch(`${MISSIONS_BASE}/${entry.path}`, { cache: "no-cache" });
    if (!response.ok) throw new Error(`${entry.path} (HTTP ${response.status})`);
    const raw = await response.json();
    return { indexEntry: entry, raw, clusterByJobCode };
  }));

  const allMissions = loaded.map((item) => toLegacyMissionView(item));
  const jobDefs = missionsIndex.jobs.map((job) => ({
    job_code: job.job_cd,
    real_job_code: job.job_cd,
    job_name: job.job_name,
    cluster_id: resolveClusterId(job.job_cd, clusterByJobCode),
    icon: job.icon || "💼",
    desc: job.job_mdcl_nm || ""
  }));

  const jobInfo = Object.fromEntries(jobDefs.map((job) => {
    const jobMeta = missionsIndex.jobs.find((item) => item.job_cd === job.job_code);
    const missions = allMissions.filter((item) => item.job_code === job.job_code);
    const first = missions[0]?._raw;
    const domain = first?.mission_facts?.domain || "";
    const titles = missions.map((item) => item.title);
    const axisSums = Object.fromEntries(axes.map((axis) => [axis, 0]));
    missions.forEach((item) => axes.forEach((axis) => {
      axisSums[axis] += (item.axis_signals?.[axis] || 0);
    }));
    const sortedAxes = axes.slice().sort((a, b) => (axisSums[b] || 0) - (axisSums[a] || 0));
    const topAxes = sortedAxes.slice(0, 3).map((axis) => axisMeta[axis]?.name || axis);
    const avgMinutes = Math.round(
      missions.reduce((sum, item) => sum + (item._raw?.mission?.difficulty?.estimated_time_minutes || 15), 0)
      / Math.max(missions.length, 1)
    );

    return [job.job_code, {
      summary: buildJobSummary({
        jobCode: job.job_code,
        jobName: job.job_name,
        jobField: jobMeta?.job_mdcl_nm
      }),
      tasks: titles.length ? titles : [`${job.job_name} 실무 미션 수행`],
      skills: topAxes,
      daily: buildDailyDescription({
        jobName: job.job_name,
        domain,
        missions,
        avgMinutes
      })
    }];
  }));

  return { missionsIndex, allMissions, jobDefs, jobInfo };
}
