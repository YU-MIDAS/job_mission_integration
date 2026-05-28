function renderMaterial(material, esc) {
  const title = `<div class="mat-title">${esc(material.title || material.subtype || material.material_id)}</div>`;
  const description = material.description ? `<div class="mat-desc">${esc(material.description)}</div>` : "";
  const usage = material.used_for ? `<div class="mat-use">📝 ${esc(material.used_for)}</div>` : "";
  const data = material.data || {};

  let body = "";
  if (material.type === "chart") {
    body = renderChart(data, esc);
  } else if (material.type === "table") {
    body = renderTable(data, esc);
  } else if (material.type === "memo") {
    body = renderMemo(data, esc);
  } else if (material.type === "log") {
    body = renderLog(data, esc);
  } else {
    body = `<div class="mat-fallback"><pre>${esc(JSON.stringify(data, null, 2))}</pre></div>`;
  }

  return `<div class="material mat-${esc(material.type || "other")}">
    ${title}${description}${usage}
    <div class="mat-body">${body}</div>
  </div>`;
}

function renderChart(data, esc) {
  const series = (data.series || []).filter((item) => Array.isArray(item.values) && item.values.length);
  const xValues = data.x_axis?.values || [];
  if (!series.length || !xValues.length) {
    if (Array.isArray(data.rows) && data.rows.length) return renderTable(data, esc);
    if (Array.isArray(data.items) && data.items.length) {
      return `<ul class="mat-items">${data.items.map((item) => `<li>${esc(typeof item === "string" ? item : JSON.stringify(item))}</li>`).join("")}</ul>`;
    }
    return "<div class=\"mat-empty\">표시할 차트 데이터가 없습니다.</div>";
  }

  const width = 480;
  const height = 200;
  const padding = 36;
  const allValues = series.flatMap((item) => item.values);
  const maxValue = Math.max(...allValues);
  const minValue = Math.min(0, Math.min(...allValues));
  const xStep = (width - padding * 2) / Math.max(xValues.length - 1, 1);
  const yScale = (value) => height - padding - (value - minValue) / (maxValue - minValue + 0.0001) * (height - padding * 2);
  const colors = ["var(--accent)", "var(--success)", "var(--warn)", "var(--danger)"];
  const chartType = data.chart_type || "line";

  let body = "";
  series.forEach((item, seriesIndex) => {
    const color = colors[seriesIndex % colors.length];
    if (chartType === "bar") {
      const barWidth = (width - padding * 2) / xValues.length * 0.7 / series.length;
      item.values.forEach((value, itemIndex) => {
        const x = padding + itemIndex * xStep - barWidth * series.length / 2 + barWidth * seriesIndex;
        const y = yScale(value);
        body += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${(height - padding - y).toFixed(1)}" fill="${color}" opacity="0.85"/>`;
      });
      return;
    }

    const path = item.values.map((value, itemIndex) => `${itemIndex === 0 ? "M" : "L"}${(padding + itemIndex * xStep).toFixed(1)},${yScale(value).toFixed(1)}`).join(" ");
    body += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2"/>`;
    item.values.forEach((value, itemIndex) => {
      body += `<circle cx="${(padding + itemIndex * xStep).toFixed(1)}" cy="${yScale(value).toFixed(1)}" r="3" fill="${color}"/>`;
    });
  });

  const xLabels = xValues.map((item, index) => `<text x="${(padding + index * xStep).toFixed(1)}" y="${height - 12}" fill="var(--sub)" font-size="10" text-anchor="middle">${esc(item)}</text>`).join("");
  const axisLine = `<line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}" stroke="var(--border)"/>`;
  const yLabels = `<text x="${padding - 6}" y="${(padding + 5).toFixed(1)}" fill="var(--sub)" font-size="10" text-anchor="end">${maxValue}</text>
                   <text x="${padding - 6}" y="${height - padding}" fill="var(--sub)" font-size="10" text-anchor="end">${minValue}</text>`;
  const legend = series.length > 1
    ? `<div class="chart-legend">${series.map((item, index) => `<span class="leg-item"><span class="leg-dot" style="background:${colors[index % colors.length]}"></span>${esc(item.name || (`계열 ${index + 1}`))}</span>`).join("")}</div>`
    : "";

  return `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${esc(data.chart_type || "chart")}">${axisLine}${body}${xLabels}${yLabels}</svg>${legend}`;
}

function renderTable(data, esc) {
  const columns = data.columns || [];
  const rows = data.rows || [];
  if (!columns.length || !rows.length) return "<div class=\"mat-empty\">표시할 표 데이터가 없습니다.</div>";

  const header = columns.map((column) => `<th>${esc(column.label || column.key)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((column) => `<td>${esc(row[column.key] ?? "")}</td>`).join("")}</tr>`).join("");
  return `<table class="mat-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderMemo(data, esc) {
  const items = data.items || [];
  const entries = data.entries || [];
  const cards = data.cards || [];
  const thread = data.thread || [];
  if (items.length) {
    return `<ul class="mat-items">${items.map((item) => `<li>${esc(typeof item === "string" ? item : (item.text || JSON.stringify(item)))}</li>`).join("")}</ul>`;
  }
  if (entries.length) {
    return `<div class="mat-entries">${entries.map((entry) => `<div class="mat-entry"><strong>${esc(entry.title || entry.label || "")}</strong>${entry.text ? `<div>${esc(entry.text)}</div>` : ""}</div>`).join("")}</div>`;
  }
  if (cards.length) {
    return `<div class="mat-cards">${cards.map((card) => `<div class="mat-card-item"><strong>${esc(card.title || "")}</strong>${card.body ? `<div>${esc(card.body)}</div>` : ""}</div>`).join("")}</div>`;
  }
  if (thread.length) {
    return `<div class="mat-thread">${thread.map((item) => `<div class="mat-msg"><strong>${esc(item.speaker || "")}:</strong> ${esc(item.text || item.message || "")}</div>`).join("")}</div>`;
  }
  if (data.author) {
    return `<div class="mat-author">— ${esc(data.author)}</div>`;
  }
  return "<div class=\"mat-empty\">메모에 표시할 본문이 없습니다.</div>";
}

function renderLog(data, esc) {
  const items = data.items || data.entries || [];
  if (items.length) {
    return `<pre class="mat-log">${items.map((item) => esc(typeof item === "string" ? item : JSON.stringify(item))).join("\n")}</pre>`;
  }
  return `<pre class="mat-log">${esc(JSON.stringify(data, null, 2))}</pre>`;
}

export function renderSelectScreen({ state, jobDefs, renderBrand }) {
  const cards = jobDefs.map((job) => `<div class="job-card${state.selectedJobs.includes(job.job_code) ? " sel" : ""}" data-jc="${job.job_code}">
      <div class="jicon">${job.icon}</div>
      <div class="jname">${job.job_name}</div>
      <div class="jsub">${job.desc || "미션 기반 탐색"}</div>
    </div>`).join("");

  return `${renderBrand()}<h1 class="stitle">어떤 직무를 탐색할까요?</h1>
  <p class="sdesc">직무를 1개 선택하세요.</p>
  <div class="job-grid">${cards}</div>
  <button class="btn btn-p" id="btn-start"${state.selectedJobs.length ? " " : " disabled"}>시작하기 →</button>`;
}

export function renderPreQuestionScreen({ state, jobDefs, renderBrand }) {
  const jobCode = state.selectedJobs[state.preqIndex];
  const selectedJob = jobDefs.find((item) => item.job_code === jobCode);
  const answers = state.preqAnswers[jobCode] || { q1: "", q2: "" };
  const progress = Math.round(state.preqIndex / state.selectedJobs.length * 100);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  }[ch]));

  return `${renderBrand()}<div class="prog"><div class="prog-f" style="width:${progress}%"></div></div>
  <h1 class="stitle">시작 전 잠깐 — ${state.preqIndex + 1} / ${state.selectedJobs.length}</h1>
  <p class="sdesc">이 직무에 대한 첫인상을 솔직하게 적어주세요.</p>
  <div class="card" style="margin-bottom:14px">
    <div class="pq-lbl">${selectedJob.icon} ${selectedJob.job_name}</div>
    <div class="pq-txt">이 직무가 하루에 가장 많이 하는 일이 뭐일 것 같아?</div>
    <textarea id="pq1" rows="3">${esc(answers.q1)}</textarea>
  </div>
  <div class="card">
    <div class="pq-lbl">${selectedJob.icon} ${selectedJob.job_name}</div>
    <div class="pq-txt">이 직무에서 가장 자주 쓰는 능력이 뭐일 것 같아?</div>
    <textarea id="pq2" rows="3">${esc(answers.q2)}</textarea>
  </div>
  <div style="display:flex;gap:10px;margin-top:24px">
    ${state.preqIndex > 0 ? "<button class=\"btn btn-g\" id=\"btn-pback\">← 이전</button>" : ""}
    <button class="btn btn-p" id="btn-pnext" style="margin-left:auto">${state.preqIndex < state.selectedJobs.length - 1 ? "다음 직무 →" : "미션 시작 →"}</button>
  </div>`;
}

export function renderMissionScreen({
  state,
  mission,
  jobDef,
  missionProgressPct,
  missionCount,
  renderBrand,
  isLastMission,
  esc
}) {
  const rawMission = mission._raw || {};
  const missionSpec = rawMission.mission || {};
  const scenario = missionSpec.scenario || {};
  const facts = rawMission.mission_facts || {};
  const tasks = missionSpec.tasks || [];
  const materials = missionSpec.materials || [];
  const submissionFormat = missionSpec.submission_format || {};

  const scenarioUI = `<div class="scenario">
    ${scenario.role ? `<div class="sc-role"><span class="sc-tag">역할</span> ${esc(scenario.role)}</div>` : ""}
    ${scenario.context ? `<p class="sc-context">${esc(scenario.context)}</p>` : ""}
    ${facts.main_issue ? `<div class="sc-issue"><span class="sc-tag">핵심 이슈</span> ${esc(facts.main_issue)}</div>` : ""}
    ${scenario.goal ? `<div class="sc-goal"><span class="sc-tag">목표</span> ${esc(scenario.goal)}</div>` : ""}
    ${(scenario.constraints || []).length ? `<div class="sc-constraints"><span class="sc-tag">제약</span><ul>${scenario.constraints.map((constraint) => `<li>${esc(constraint)}</li>`).join("")}</ul></div>` : ""}
  </div>`;

  const materialsUI = materials.length
    ? `<div class="materials">
      <div class="mat-hdr">📎 참고 자료 (${materials.length})</div>
      ${materials.map((item) => renderMaterial(item, esc)).join("")}
    </div>`
    : "";

  const tasksUI = tasks.length
    ? `<div class="tasks-block">
      <div class="task-lbl">📋 수행 과제</div>
      <ol class="tasks-list">${tasks.map((task) => `<li>${esc(task.instruction)}</li>`).join("")}</ol>
    </div>`
    : `<div style="margin-bottom:20px"><div class="task-lbl">📋 수행 과제</div><div class="task-txt">${esc(mission.task)}</div></div>`;

  const submitHintUI = (submissionFormat.required_sections?.length || submissionFormat.length_hint)
    ? `<div class="submit-hint">
      ${submissionFormat.required_sections?.length ? `<div><strong>답변 구성:</strong> ${submissionFormat.required_sections.map(esc).join(" · ")}</div>` : ""}
      ${submissionFormat.length_hint ? `<div><strong>분량:</strong> ${esc(submissionFormat.length_hint)}</div>` : ""}
      ${submissionFormat.estimated_time_minutes ? `<div><strong>예상 소요:</strong> 약 ${submissionFormat.estimated_time_minutes}분</div>` : ""}
    </div>`
    : "";

  const answerReady = state.missionAnswer.trim().length > 10;

  return `${renderBrand()}
  <div class="prog"><div class="prog-f" style="width:${missionProgressPct}%"></div></div>
  <div class="mhdr"><span class="badge">${jobDef.icon} ${esc(jobDef.job_name)}</span><span class="mstep">미션 ${state.missionStepIndex + 1} / ${missionCount}</span></div>
  <h1 class="stitle">${esc(mission.title)}</h1>
  ${scenarioUI}
  ${materialsUI}
  ${tasksUI}
  ${submitHintUI}
  <textarea id="m-ans" rows="8" placeholder="여기에 답변을 작성하세요…">${esc(state.missionAnswer)}</textarea>
  <div style="display:flex;gap:10px;margin-top:24px">
    <button class="btn btn-p" id="btn-mnext"${answerReady ? "" : " disabled"}>${isLastMission ? "결과 보기 →" : "다음 →"}</button>
  </div>`;
}
