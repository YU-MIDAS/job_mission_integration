const TOKEN_KEY = "jobsim:admin_api_token";
const app = document.getElementById("admin-app");

const state = {
  token: sessionStorage.getItem(TOKEN_KEY) || "",
  authenticated: false,
  authError: "",
  jobs: [],
  search: "",
  selectedJobCode: "",
  difficulty: "normal",
  run: null,
  loadingJobs: false,
  startingRun: false,
  exporting: false,
  deletingRun: false,
  rawLogOpen: false,
  jobsScrollTop: 0
};

let pollTimer = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  }[ch]));
}

function headers() {
  return {
    "Content-Type": "application/json",
    ...(state.token ? { "x-api-token": state.token } : {})
  };
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...headers(),
      ...(options.headers || {})
    }
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const message = data?.error?.message || data?.error?.code || text || response.statusText;
    const error = new Error(message);
    error.status = response.status;
    error.details = data?.error?.details;
    throw error;
  }
  return data;
}

function selectedJob() {
  return state.jobs.find((job) => job.jobCode === state.selectedJobCode) || null;
}

function filteredJobs() {
  const q = state.search.trim().toLowerCase();
  if (!q) return state.jobs;
  return state.jobs.filter((job) => [
    job.jobCode,
    job.jobName,
    job.jobField,
    job.summary
  ].some((value) => String(value || "").toLowerCase().includes(q)));
}

function statusText(status) {
  return ({
    running: "생성 중",
    succeeded: "검토 가능",
    failed: "실패",
    exported: "반영 완료"
  })[status] || "대기";
}

function statusDifficulty(difficulty) {
  return ({
    easy: "쉬움",
    normal: "보통",
    hard: "어려움"
  })[difficulty] || difficulty || "-";
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function formatScore(score) {
  const n = Number(score);
  if (!Number.isFinite(n)) return "-";
  return n <= 1 ? n.toFixed(2) : Math.round(n).toString();
}

function scorePercent(score) {
  const n = Number(score);
  if (!Number.isFinite(n)) return "0%";
  const normalized = n <= 1 ? n : n / 100;
  return `${Math.max(0, Math.min(100, normalized * 100)).toFixed(0)}%`;
}

function latestLogTime(run, fallbackIndex = 0) {
  const logs = run?.logs || [];
  return logs[Math.min(fallbackIndex, Math.max(0, logs.length - 1))]?.at || run?.updatedAt || run?.createdAt;
}

function findRunLog(run, patterns) {
  const logs = run?.logs || [];
  const needles = patterns.map((pattern) => pattern.toLowerCase());
  return logs.find((line) => {
    const message = String(line.message || "").toLowerCase();
    return needles.some((needle) => message.includes(needle));
  }) || null;
}

function eventTime(run, patterns) {
  return findRunLog(run, patterns)?.at || null;
}

function timelineMilestones(run) {
  if (!run) return [];
  const complete = run.status === "succeeded" || run.status === "exported";
  return [
    {
      title: "미션 생성 시작",
      desc: `직무: ${selectedJob()?.jobName || run.jobCode}, 난이도: ${statusDifficulty(run.difficulty)}`,
      time: run.createdAt || eventTime(run, ["run started", "target started", "Starting"]),
      reached: true
    },
    {
      title: "자료 불러오기",
      desc: "원천 직무 데이터와 실무 조사 시트를 준비합니다.",
      time: eventTime(run, ["profile loading", "profile loaded", "target started", "decisions started", "practice sheet background"]),
      reached: Boolean(eventTime(run, ["profile loading", "profile loaded", "target started", "decisions started", "practice sheet background"]))
    },
    {
      title: "시나리오 구성",
      desc: "시나리오와 참고 자료 초안을 생성합니다.",
      time: eventTime(run, ["draft LLM started"]),
      reached: Boolean(eventTime(run, ["draft LLM started"]))
    },
    {
      title: "수행 과제 생성",
      desc: "초안의 수행 과제와 제출 형식을 정리합니다.",
      time: eventTime(run, ["draft LLM finished"]),
      reached: Boolean(eventTime(run, ["draft LLM finished"]))
    },
    {
      title: "신뢰도 검증",
      desc: "validator로 reliability, warning, fail count를 확인합니다.",
      time: eventTime(run, ["validator attempt", "repair LLM started", "repair LLM finished"]),
      reached: Boolean(eventTime(run, ["validator attempt", "repair LLM started", "repair LLM finished"]))
    },
    {
      title: "미션 생성 완료",
      desc: complete ? "검토할 수 있습니다." : "미리보기 생성 대기 중",
      time: eventTime(run, [" saved", "run finished", "Generation succeeded"]) || (complete ? run.updatedAt : null),
      reached: complete
    },
    {
      title: "내보내기 승인",
      desc: run.status === "exported"
        ? "미션 카탈로그가 갱신되었습니다."
        : state.exporting
          ? "내보내기 검증을 실행 중입니다."
          : "승인 전까지 사용자 화면에는 반영되지 않습니다.",
      time: run.status === "exported" ? run.updatedAt : null,
      reached: run.status === "exported" || state.exporting
    }
  ];
}

function timelineActiveIndex(run) {
  if (!run) return -1;
  if (state.exporting || run.status === "exported") return 6;
  if (run.status === "succeeded") return 5;
  const stages = timelineMilestones(run);
  const reachedIndexes = stages
    .map((stage, index) => stage.reached ? index : -1)
    .filter((index) => index >= 0);
  const lastReachedIndex = reachedIndexes.length ? Math.max(...reachedIndexes) : 0;
  return Math.max(0, lastReachedIndex);
}

function timelineRenderKey(run) {
  if (!run) return "none";
  return [
    run.status,
    timelineActiveIndex(run),
    Boolean(run.missionPreview),
    Boolean(run.exportResult),
    state.exporting
  ].join(":");
}

function renderAuth() {
  app.innerHTML = `<div class="topbar">
    <div class="brand">JOB<span>SIM</span> Admin</div>
    <div class="spacer"></div>
    <span class="status-pill">Token required</span>
  </div>
  <main class="auth-wrap">
    <form class="auth-card" id="auth-form">
      <div class="eyebrow">미션 생성 관리</div>
      <h1>관리자 암호 입력</h1>
      <p class="help">서버의 <code>API_SHARED_TOKEN</code> 값을 입력하면 이 브라우저 세션에서 관리자 생성 API를 호출합니다. 암호는 코드에 저장되지 않습니다.</p>
      <div class="field">
        <label class="label" for="admin-token">Admin password</label>
        <input class="input" id="admin-token" type="password" autocomplete="current-password" autofocus>
      </div>
      ${state.authError ? `<div class="error">${esc(state.authError)}</div>` : ""}
      <div style="display:flex;justify-content:flex-end;margin-top:20px">
        <button class="btn primary" type="submit">Enter admin</button>
      </div>
    </form>
  </main>`;

  document.getElementById("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    state.token = document.getElementById("admin-token").value.trim();
    sessionStorage.setItem(TOKEN_KEY, state.token);
    await loadJobs();
  });
}

function renderTopbar() {
  const runStatus = state.run ? statusText(state.run.status) : "대기";
  return `<div class="topbar">
    <div class="brand">JOB<span>SIM</span> Admin</div>
    <span class="status-pill good">토큰 활성</span>
    <span class="status-pill hide-sm">상태: ${esc(runStatus)}</span>
    <div class="spacer"></div>
    <a class="btn" href="./">사용자 화면</a>
    <button class="btn danger" id="logout">로그아웃</button>
  </div>`;
}

function renderJobsPanel() {
  const jobs = filteredJobs();
  const enabledCount = state.jobs.filter((job) => job.enabled).length;
  const cards = jobs.map((job) => {
    const selected = job.jobCode === state.selectedJobCode;
    return `<div class="job-card ${selected ? "selected" : ""} ${job.enabled ? "" : "disabled"}" data-job="${esc(job.jobCode)}">
      <div class="job-top">
        <span class="job-code">${esc(job.jobCode)}</span>
        <span class="chip job-state ${job.enabled ? "good" : "warn"}">${job.enabled ? "생성가능" : "추가자료 필요"}</span>
      </div>
      <div class="job-name">${esc(job.jobName || job.jobCode)}</div>
      <div class="job-meta">${esc(job.jobField || "분류 정보 없음")}</div>
    </div>`;
  }).join("");

  return `<section class="panel">
    <div class="panel-head">
      <div class="panel-title">직무 선택</div>
      <div class="panel-sub">전체 raw ${state.jobs.length}개 / 생성 가능 ${enabledCount}개</div>
    </div>
    <div class="panel-body">
      <input class="input" id="job-search" placeholder="직무명 또는 코드 검색" value="${esc(state.search)}">
      <div style="height:12px"></div>
      <div class="jobs-list">${cards || `<div class="empty">검색 결과가 없습니다.</div>`}</div>
    </div>
  </section>`;
}

function renderControlsPanel() {
  const job = selectedJob();
  const run = state.run;
  const canGenerate = job?.enabled && !state.startingRun && run?.status !== "running";
  const canExport = run?.status === "succeeded" && !state.exporting;
  const canDelete = run?.runDir && ["succeeded", "failed"].includes(run.status) && !state.deletingRun;

  return `<section class="panel">
    <div class="panel-head">
      <div class="panel-title">미션 생성</div>
      <div class="panel-sub">직무와 난이도를 선택한 뒤 생성하고, 미리보기 확인 후 내보내기 승인합니다.</div>
    </div>
    <div class="panel-body">
      ${job ? `<div class="selected-box">
        <div class="eyebrow">선택한 직무</div>
        <div class="selected-name">${esc(job.jobName || job.jobCode)}</div>
        <div class="selected-detail">${esc(job.summary || job.jobField || "요약 정보 없음")}</div>
        <div class="preview-meta">
          <span class="chip ${job.enabled ? "good" : "warn"}">${job.enabled ? "생성 가능" : "실무 조사 시트 필요"}</span>
          <span class="chip">${esc(job.jobCode)}</span>
        </div>
      </div>` : `<div class="empty">왼쪽에서 생성 가능한 직무를 선택하세요.</div>`}

      <label class="label">난이도</label>
      <div class="segmented" id="difficulty">
        ${[
          ["easy", "쉬움"],
          ["normal", "보통"],
          ["hard", "어려움"]
        ].map(([value, label]) => `<button class="seg ${state.difficulty === value ? "active" : ""}" data-difficulty="${value}">${label}</button>`).join("")}
      </div>

      <div class="control-grid">
        <button class="btn primary" id="generate" ${canGenerate ? "" : "disabled"}><span class="btn-ico play"></span>${state.startingRun ? "시작 중..." : "미션 생성"}</button>
        <button class="btn warn" id="export" ${canExport ? "" : "disabled"}><span class="btn-ico export"></span>${state.exporting ? "반영 중..." : "내보내기 승인"}</button>
        ${run?.runDir ? `<button class="btn danger" id="delete-run" ${canDelete ? "" : "disabled"}>${state.deletingRun ? "삭제 중..." : "생성 결과 삭제"}</button>` : ""}
      </div>

      <div class="admin-note">생성 결과는 먼저 <code>outputs/pilot/v1/runs</code>에 저장됩니다. 사용자 화면에 반영하려면 미리보기 확인 후 내보내기 승인을 눌러주세요.</div>
      ${run?.error ? `<div class="error">${esc(run.error)}</div>` : ""}
      ${run?.exportResult ? `<div class="${run.exportResult.status === "succeeded" ? "status-pill good" : "error"}" style="margin-top:14px;display:inline-flex">Export ${esc(run.exportResult.status)}</div>` : ""}
    </div>
  </section>`;
}

function buildTimeline(run) {
  const hasRun = Boolean(run);
  const complete = run?.status === "succeeded" || run?.status === "exported";
  const failed = run?.status === "failed";
  const running = run?.status === "running";
  const exported = run?.status === "exported";
  const exporting = state.exporting;
  const stages = hasRun
    ? timelineMilestones(run)
    : [
      {
        title: "미션 생성 시작",
        desc: "미션 생성을 누르면 실행됩니다.",
        time: null,
        reached: false
      },
      { title: "자료 불러오기", desc: "원천 직무 데이터와 실무 조사 시트를 준비합니다.", time: null, reached: false },
      { title: "시나리오 구성", desc: "시나리오와 참고 자료 초안을 생성합니다.", time: null, reached: false },
      { title: "수행 과제 생성", desc: "초안의 수행 과제와 제출 형식을 정리합니다.", time: null, reached: false },
      { title: "신뢰도 검증", desc: "validator로 reliability, warning, fail count를 확인합니다.", time: null, reached: false },
      { title: "미션 생성 완료", desc: "미리보기 생성 대기 중", time: null, reached: false },
      { title: "내보내기 승인", desc: "승인 전까지 사용자 화면에는 반영되지 않습니다.", time: null, reached: false }
    ];

  const reachedIndexes = stages
    .map((stage, index) => stage.reached ? index : -1)
    .filter((index) => index >= 0);
  const lastReachedIndex = reachedIndexes.length ? Math.max(...reachedIndexes) : -1;
  const activeIndex = !hasRun
    ? -1
    : exported
      ? 6
      : exporting
        ? 6
        : complete
          ? 5
          : failed
            ? Math.max(0, lastReachedIndex)
            : Math.max(0, lastReachedIndex);

  return stages.map((stage, index) => {
    let cls = "pending";
    if (exported && index <= 6) cls = "done";
    else if (exporting && index === 6) cls = "active";
    else if (complete && index <= 5) cls = "done";
    else if (failed && index === activeIndex) cls = "failed";
    else if (failed && index < activeIndex) cls = "done";
    else if (running && index < activeIndex) cls = "done";
    else if (running && index === activeIndex) cls = "active";
    else if (!hasRun && index === 0) cls = "pending";

    const marker = cls === "done" ? "✓" : cls === "failed" ? "!" : String(index + 1);
    const visibleTime = cls === "pending" ? "" : formatTime(stage.time);
    return `<div class="tl-item ${cls}">
      <div class="tl-time">${esc(visibleTime)}</div>
      <div class="tl-dot"><span class="tl-spinner" aria-hidden="true"></span><span class="tl-marker">${esc(marker)}</span></div>
      <div>
        <div class="tl-title">${esc(stage.title)}</div>
        <div class="tl-desc">${esc(stage.desc)}</div>
      </div>
    </div>`;
  }).join("");
}

function renderRunPanel() {
  const run = state.run;
  const logs = run?.logs || [];
  return `<section class="panel">
    <div class="panel-head">
      <div class="panel-title">생성 상태</div>
      <div class="panel-sub">${run ? `현재 상태: ${esc(statusText(run.status))}` : "아직 실행된 미션 생성 작업이 없습니다."}</div>
    </div>
    <div class="panel-body">
      <div class="timeline">${buildTimeline(run)}</div>
      <details class="raw-log" ${state.rawLogOpen ? "open" : ""}>
        <summary>상세 로그 ${logs.length ? `(${logs.length})` : ""}</summary>
        <div class="log-box">
          ${logs.length ? logs.map((line) => `<div class="log-line ${esc(line.stream)}">[${esc(line.stream)}] ${esc(line.message)}</div>`).join("") : `<div class="log-line system">미션 생성을 기다리는 중입니다.</div>`}
        </div>
      </details>
    </div>
  </section>`;
}

function renderMaterialTable(data) {
  const columns = Array.isArray(data.columns) ? data.columns : [];
  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (!columns.length || !rows.length) return "";
  const header = columns.map((column) => `<th>${esc(column.label || column.key)}</th>`).join("");
  const body = rows.slice(0, 6).map((row) => `<tr>${columns.map((column) => `<td>${esc(row[column.key] ?? "")}</td>`).join("")}</tr>`).join("");
  return `<div style="overflow:auto"><table class="mat-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderMaterialItems(data) {
  const items = Array.isArray(data.items) ? data.items : [];
  if (!items.length) return "";
  return `<ul class="mat-items">${items.slice(0, 8).map((item) => `<li>${esc(typeof item === "string" ? item : (item.text || item.label || JSON.stringify(item)))}</li>`).join("")}</ul>`;
}

function renderMaterialStack(items, className) {
  if (!Array.isArray(items) || !items.length) return "";
  return `<div class="mat-stack">${items.slice(0, 6).map((item) => `<div class="${className}"><strong>${esc(item.title || item.label || item.speaker || "")}</strong>${item.text || item.body || item.message ? `<div>${esc(item.text || item.body || item.message)}</div>` : ""}</div>`).join("")}</div>`;
}

function renderChartPreview(data) {
  const series = Array.isArray(data.series) ? data.series.find((item) => Array.isArray(item.values) && item.values.length) : null;
  const labels = data.x_axis?.values || [];
  if (!series || !labels.length) return "";
  const values = series.values.map((value) => Number(value)).filter((value) => Number.isFinite(value));
  const max = Math.max(...values, 1);
  return `<div class="chart-bars">${values.slice(0, 6).map((value, index) => `<div class="chart-row">
    <div>${esc(labels[index] ?? `#${index + 1}`)}</div>
    <div class="chart-track"><div class="chart-fill" style="width:${Math.max(4, Math.min(100, value / max * 100)).toFixed(0)}%"></div></div>
    <div>${esc(value)}</div>
  </div>`).join("")}</div>`;
}

function renderMaterialPreview(material) {
  const data = material.data || {};
  const body = renderMaterialTable(data)
    || renderMaterialItems(data)
    || renderMaterialStack(data.entries, "mat-entry")
    || renderMaterialStack(data.cards, "mat-card-item")
    || renderMaterialStack(data.thread, "mat-msg")
    || renderChartPreview(data)
    || `<div class="mat-fallback"><pre>${esc(JSON.stringify(data, null, 2))}</pre></div>`;

  return `<article class="material">
    <div class="mat-head">
      <div>
        <div class="mat-title">${esc(material.title || material.subtype || material.material_id || "Untitled material")}</div>
        ${material.description ? `<div class="mat-desc">${esc(material.description)}</div>` : ""}
      </div>
      <span class="chip">${esc(material.type || "material")}</span>
    </div>
    ${material.used_for ? `<div class="mat-use">${esc(material.used_for)}</div>` : ""}
    <div class="mat-body">${body}</div>
  </article>`;
}

function renderMaterials(preview) {
  const materials = Array.isArray(preview.materials) ? preview.materials : [];
  if (!materials.length) return `<div class="empty">표시할 참고 자료가 없습니다.</div>`;
  return `<div class="materials">${materials.map(renderMaterialPreview).join("")}</div>`;
}

function renderPreview() {
  const preview = state.run?.missionPreview;
  const validation = state.run?.exportResult?.validation;
  if (!preview) {
    return `<section class="preview">
      <div class="preview-card"><div class="empty">생성 완료 후 미션 미리보기가 표시됩니다.</div></div>
      <div class="preview-card"><div class="empty">reliability와 export 검증 결과가 여기에 표시됩니다.</div></div>
    </section>`;
  }

  return `<section class="preview">
    <div class="preview-card">
      <div class="eyebrow">미션 미리보기</div>
      <h2 class="preview-title">${esc(preview.title || "Untitled mission")}</h2>
      <div class="preview-meta">
        <span class="chip good">${esc(preview.job_name || state.run.jobCode)}</span>
        <span class="chip">${esc(preview.difficulty)}</span>
        <span class="chip">${preview.estimated_time_minutes ?? "-"} min</span>
        <span class="chip">${preview.materials_count} materials</span>
      </div>
      <div class="section-label">시나리오</div>
      <div class="scenario">${esc(preview.scenario_summary || "시나리오 요약 없음")}</div>
      <div class="section-label">수행 과제</div>
      <ol class="tasks-list">${preview.tasks.map((task) => `<li>${esc(task)}</li>`).join("") || "<li>과제 정보 없음</li>"}</ol>
      <div class="section-label">참고 자료</div>
      ${renderMaterials(preview)}
      <div class="section-label">생성 파일</div>
      <div class="scenario">${esc(preview.mission_output_path)}</div>
    </div>
    <div class="preview-card">
      <div class="eyebrow">품질 확인</div>
      <div class="score-ring" style="--score:${scorePercent(preview.reliability_score)}">
        <div>
          <div class="score-value">${esc(formatScore(preview.reliability_score))}</div>
          <div class="score-label">Reliability</div>
        </div>
      </div>
      <div class="metric-grid">
        <div class="metric"><div class="metric-k">Warnings</div><div class="metric-v">${preview.warning_count}</div></div>
        <div class="metric"><div class="metric-k">Fails</div><div class="metric-v">${preview.fail_count}</div></div>
        <div class="metric"><div class="metric-k">상태</div><div class="metric-v">${esc(statusText(state.run.status))}</div></div>
        <div class="metric"><div class="metric-k">자료</div><div class="metric-v">${preview.materials_count}</div></div>
      </div>
      <div class="section-label">내보내기 검증</div>
      ${validation ? `<div class="scenario">${validation.ok
        ? `OK\njobs=${validation.jobs}\nmissions=${validation.missions}`
        : `FAILED\n${(validation.issues || []).map((issue) => `${issue.code}: ${issue.message}`).join("\n")}`}</div>` : `<div class="empty">내보내기 승인 후 검증 결과가 표시됩니다.</div>`}
    </div>
  </section>`;
}

function renderApp() {
  app.innerHTML = `${renderTopbar()}
  <main class="shell">
    <div class="grid">
      ${renderJobsPanel()}
      ${renderControlsPanel()}
      ${renderRunPanel()}
    </div>
    ${renderPreview()}
  </main>`;
  bindAppEvents();
}

function bindAppEvents() {
  document.getElementById("logout")?.addEventListener("click", () => {
    sessionStorage.removeItem(TOKEN_KEY);
    state.token = "";
    state.authenticated = false;
    state.authError = "";
    state.run = null;
    state.rawLogOpen = false;
    state.deletingRun = false;
    stopPolling();
    renderAuth();
  });

  document.getElementById("job-search")?.addEventListener("input", (event) => {
    state.search = event.target.value;
    state.jobsScrollTop = 0;
    renderApp();
  });

  const jobsList = document.querySelector(".jobs-list");
  if (jobsList) {
    jobsList.scrollTop = state.jobsScrollTop;
    jobsList.addEventListener("scroll", () => {
      state.jobsScrollTop = jobsList.scrollTop;
    }, { passive: true });
  }

  document.querySelectorAll(".job-card").forEach((card) => {
    card.addEventListener("click", () => {
      const code = card.dataset.job;
      const job = state.jobs.find((item) => item.jobCode === code);
      if (!job?.enabled) return;
      state.selectedJobCode = code;
      renderApp();
    });
  });

  document.querySelectorAll(".seg").forEach((button) => {
    button.addEventListener("click", () => {
      state.difficulty = button.dataset.difficulty;
      renderApp();
    });
  });

  document.getElementById("generate")?.addEventListener("click", startRun);
  document.getElementById("export")?.addEventListener("click", exportRun);
  document.getElementById("delete-run")?.addEventListener("click", deleteRun);
  document.querySelector(".raw-log")?.addEventListener("toggle", (event) => {
    state.rawLogOpen = event.currentTarget.open;
  });
}

async function loadJobs() {
  state.loadingJobs = true;
  state.authError = "";
  try {
    const data = await requestJson("/api/admin/mission-generation/jobs");
    state.jobs = data.jobs || [];
    state.authenticated = true;
    const firstEnabled = state.jobs.find((job) => job.enabled);
    if (!state.selectedJobCode && firstEnabled) state.selectedJobCode = firstEnabled.jobCode;
    renderApp();
  } catch (error) {
    sessionStorage.removeItem(TOKEN_KEY);
    state.authenticated = false;
    state.authError = error.status === 401
      ? "암호가 올바르지 않습니다."
      : error.message;
    renderAuth();
  } finally {
    state.loadingJobs = false;
  }
}

async function startRun() {
  const job = selectedJob();
  if (!job?.enabled) return;
  state.startingRun = true;
  state.rawLogOpen = false;
  renderApp();
  try {
    const data = await requestJson("/api/admin/mission-generation/runs", {
      method: "POST",
      body: JSON.stringify({
        jobCode: job.jobCode,
        difficulty: state.difficulty
      })
    });
    state.run = data.run;
    startPolling();
  } catch (error) {
    state.run = {
      status: "failed",
      logs: [{ at: new Date().toISOString(), stream: "system", message: error.message }],
      error: error.message
    };
  } finally {
    state.startingRun = false;
    renderApp();
  }
}

async function refreshRun() {
  if (!state.run?.runId) return;
  try {
    const beforeKey = timelineRenderKey(state.run);
    const data = await requestJson(`/api/admin/mission-generation/runs/${encodeURIComponent(state.run.runId)}`);
    const nextRun = data.run;
    const afterKey = timelineRenderKey(nextRun);
    state.run = nextRun;
    if (state.run.status !== "running") stopPolling();
    if (state.rawLogOpen || beforeKey !== afterKey || state.run.status !== "running") {
      renderApp();
    }
  } catch (error) {
    stopPolling();
    state.run.error = error.message;
    renderApp();
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(refreshRun, 1000);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function deleteRun() {
  if (!state.run?.runId || !state.run?.runDir || state.run.status === "running" || state.run.status === "exported") return;
  const ok = window.confirm("생성된 임시 미션 결과를 삭제할까요? 사용자 화면에 반영된 미션은 삭제되지 않습니다.");
  if (!ok) return;
  state.deletingRun = true;
  renderApp();
  try {
    await requestJson(`/api/admin/mission-generation/runs/${encodeURIComponent(state.run.runId)}`, {
      method: "DELETE"
    });
    state.run = null;
    state.rawLogOpen = false;
    stopPolling();
  } catch (error) {
    state.run.error = error.message;
  } finally {
    state.deletingRun = false;
    renderApp();
  }
}

async function exportRun() {
  if (!state.run?.runId || state.run.status !== "succeeded") return;
  state.exporting = true;
  renderApp();
  try {
    const data = await requestJson(`/api/admin/mission-generation/runs/${encodeURIComponent(state.run.runId)}/export`, {
      method: "POST",
      body: "{}"
    });
    state.run = data.run;
  } catch (error) {
    state.run.exportResult = {
      status: "failed",
      finished_at: new Date().toISOString(),
      output: error.message,
      validation: error.details
    };
    state.run.error = error.message;
  } finally {
    state.exporting = false;
    renderApp();
  }
}

if (state.token) {
  loadJobs();
} else {
  renderAuth();
}
