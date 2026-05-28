function missionTheme(mission) {
  if (!mission) return null;
  const topAxis = Object.entries(mission.axis_signals || {}).sort((a, b) => b[1] - a[1])[0]?.[0];
  const themeMap = {
    AX1: "데이터·분석 중심",
    AX2: "관찰·탐색 중심",
    AX3: "전략·판단 중심",
    AX4: "조직·협업 중심",
    AX5: "고객·소통 중심"
  };
  return themeMap[topAxis] || mission.title;
}

function similarityToPct(similarity) {
  const normalized = Math.min(Math.max(typeof similarity === "number" ? similarity : 0, 0), 1);
  return Math.round(Math.pow(normalized, 1.5) * 100);
}

function gapCompareText(answer, missions) {
  const themes = missions.filter(Boolean).map(missionTheme).filter(Boolean);
  if (!themes.length) return "미션 데이터 없음";
  const hasFirstAnswer = answer.q1 && answer.q1.trim();
  if (!hasFirstAnswer) return `실제 미션 유형: ${themes.join(" / ")}`;
  return `기대: ${answer.q1.length > 30 ? `${answer.q1.slice(0, 30)}…` : answer.q1} / 실제: ${themes.join(", ")} 수행`;
}

function axisAbsoluteMetrics({ axes, evaluationLogs, axisEvidenceSummary, axisCoverage }) {
  return Object.fromEntries(axes.map((axis) => {
    const measured = axisCoverage?.[axis]?.measured !== false;
    if (!measured) {
      return [axis, {
        measured: false,
        score: null,
        ratio: 0,
        pct: 0
      }];
    }

    const scores = (evaluationLogs || [])
      .map((log) => Number(log?.evaluation?.axes?.[axis]?.score))
      .filter((value) => Number.isFinite(value));
    const fallbackScore = Math.max(0, Math.min(4, Number(axisEvidenceSummary?.[axis]?.score ?? 0)));
    const avgScore = scores.length
      ? scores.reduce((sum, value) => sum + value, 0) / scores.length
      : fallbackScore;
    const clamped = Math.max(0, Math.min(4, avgScore));
    const pct = Math.round((clamped / 4) * 100);
    return [axis, {
      measured: true,
      score: clamped,
      ratio: clamped / 4,
      pct
    }];
  }));
}

export function renderRadarSection({
  state,
  axes,
  axisLabels,
  axisMeta,
  levelInfo,
  radarScale,
  emptyAxisProfile,
  jobDefs
}) {
  const profile = state.axisProfile || emptyAxisProfile();
  const axisDefs = axes.map((key) => ({ key, label: axisLabels[key] }));
  const evidenceSummary = state.resultSummary?.axisEvidenceSummary || {};
  const axisCoverage = state.resultSummary?.axisCoverage || {};
  const measuredAxes = state.resultSummary?.measuredAxes || [];
  const absolute = axisAbsoluteMetrics({
    axes,
    evaluationLogs: state.evaluationLogs,
    axisEvidenceSummary: evidenceSummary,
    axisCoverage
  });
  const axisCount = 5;
  const centerX = 185;
  const centerY = 175;
  const radius = 120;
  const labelRadius = radius + 34;
  function point(r, index) {
    const angle = (Math.PI * 2 * index / axisCount) - Math.PI / 2;
    return [centerX + r * Math.cos(angle), centerY + r * Math.sin(angle)];
  }

  let grid = "";
  [0.2, 0.4, 0.6, 0.8, 1].forEach((ratio) => {
    const points = axisDefs.map((_, index) => point(radius * ratio, index).join(","));
    grid += `<polygon points="${points.join(" ")}" fill="none" stroke="#1e2025" stroke-width="1"/>`;
  });

  const axisLines = axisDefs.map((_, index) => {
    const [x, y] = point(radius, index);
    return `<line x1="${centerX}" y1="${centerY}" x2="${x}" y2="${y}" stroke="#1e2025" stroke-width="1"/>`;
  }).join("");

  const myPoints = axisDefs.map((axis, index) => point(radius * (absolute[axis.key]?.ratio || 0), index).join(","));
  const myPolygon = `<polygon points="${myPoints.join(" ")}" fill="rgba(94,106,210,0.30)" stroke="#5e6ad2" stroke-width="2.5"/>`;

  const bestMatch = state.resultSummary?.bestMatch || null;

  const labels = axisDefs.map((axis, index) => {
    const [labelX, labelY] = point(labelRadius, index);
    const textAnchor = labelX < centerX - 8 ? "end" : labelX > centerX + 8 ? "start" : "middle";
    const value = absolute[axis.key]?.pct || 0;
    const [profileX, profileY] = point(radius * (absolute[axis.key]?.ratio || 0) + 14, index);
    return `<text x="${labelX}" y="${labelY + 4}" text-anchor="${textAnchor}" style="fill:#f7f8f8;font-size:12px;font-weight:600">${axis.label}</text>
    <text x="${profileX}" y="${profileY + 4}" text-anchor="middle" style="fill:#7b83e0;font-size:11px;font-weight:700">${value}%</text>`;
  }).join("");

  const bestWeights = bestMatch?.referenceWeights || null;
  const scoreRows = axes.map((axis) => {
    const axisScore = absolute[axis]?.score ?? 0;
    const skillPct = absolute[axis]?.pct ?? 0;
    const absoluteScoreText = `${axisScore.toFixed(1)}/4.0`;
    const measured = absolute[axis]?.measured !== false;
    const reference = bestWeights ? Math.round((bestWeights[axis] || 0) * 100) : 0;
    const meta = axisMeta[axis];
    const level = Math.max(0, Math.min(4, Math.round(axisScore)));
    const info = levelInfo[level];

    if (!measured) {
      return `<div class="axis-score axis-score-unmeasured" title="${meta.desc}">
        <div style="font-size:16px;margin-bottom:4px">${meta.icon}</div>
        <div class="axis-score-k">${meta.name.replace("해요", "")}</div>
        <div class="axis-score-v" style="color:var(--muted)">미측정</div>
        <div class="axis-score-sub">이번 미션 구성에서 이 축 신호가 부족합니다.</div>
        <div class="axis-score-sub">관련 문항 추가 후 측정 가능합니다.</div>
      </div>`;
    }

    return `<div class="axis-score" title="${meta.desc}">
      <div style="font-size:16px;margin-bottom:4px">${meta.icon}</div>
      <div class="axis-score-k">${meta.name.replace("해요", "")}</div>
      <div class="axis-score-v" style="color:${info.color}">${info.emoji} ${absoluteScoreText}</div>
      <div class="axis-score-sub">절대 수준 ${absoluteScoreText} (${skillPct}%)</div>
      ${reference > 0 ? `<div class="axis-score-ref">직무 기준 비중(참고) ${reference}%</div>` : ""}
    </div>`;
  }).join("");

  return `<div class="rs">
  <div class="rs-title">나의 5가지 사고 스타일</div>
  <div class="axis-note">각 축은 독립적인 절대값(0~100)으로 표시됩니다. 축 퍼센트를 합산하지 않습니다.</div>
  ${measuredAxes.length < axes.length ? `<div class="axis-note">⚠️ AX4/AX5처럼 일부 축은 이번 미션 세트에서 미측정일 수 있습니다.</div>` : ""}
  <div class="radar-wrap"><svg width="370" height="335" viewBox="0 0 370 335" style="overflow:visible">
    ${grid}${axisLines}${myPolygon}${labels}
  </svg></div><div class="axis-score-grid">${scoreRows}</div></div>`;
}

export function renderJobIntroSection({ state, jobDefs, jobInfo, esc }) {
  const cards = state.selectedJobs.map((jobCode) => {
    const job = jobDefs.find((item) => item.job_code === jobCode);
    const info = jobInfo[jobCode];
    if (!job || !info) return "";
    const taskList = info.tasks.map((task) => `<li>${esc(task)}</li>`).join("");
    const skillList = info.skills.map((skill) => `<li>${esc(skill)}</li>`).join("");
    return `<div class="job-intro-card">
      <div class="job-intro-hdr">
        <span class="job-intro-icon">${job.icon}</span>
        <span class="job-intro-name">${job.job_name}</span>
      </div>
      <div class="job-intro-summary">${esc(info.summary)}</div>
      <div class="job-intro-section">
        <div class="job-intro-label">🛠️ 이런 일을 해요</div>
        <ul class="job-intro-list">${taskList}</ul>
      </div>
      <div class="job-intro-section">
        <div class="job-intro-label">💪 이런 능력이 필요해요</div>
        <ul class="job-intro-list">${skillList}</ul>
      </div>
    </div>`;
  }).join("");

  if (!cards.trim()) return "";
  return `<div class="rs"><div class="rs-title">이 직업은 어떤 일을 해요?</div>${cards}</div>`;
}

export function renderCompatibilitySection({ state, jobDefs }) {
  const metaMap = state.resultSummary?.selectedJobMeta || {};
  const items = state.selectedJobs.map((jobCode) => {
    const job = jobDefs.find((item) => item.job_code === jobCode);
    const score = state.compatibility[jobCode] || 0;
    const meta = metaMap[jobCode] || {};
    return {
      jobCode,
      name: job?.job_name || jobCode,
      icon: job?.icon || "💼",
      score,
      referenceName: meta.referenceJobName || "",
      cluster: meta.clusterId || "",
      reliable: meta.reliable
    };
  }).sort((a, b) => b.score - a.score);

  const rows = items.map((item) => {
    const color = item.score >= 70 ? "var(--success)" : item.score >= 50 ? "var(--accent)" : "var(--muted)";
    const warning = item.reliable === false && item.referenceName
      ? "<div class=\"sil-warn\">⚠️ 이 직무는 다양한 능력이 섞여 있어 점수가 크게 달라질 수 있어요.</div>"
      : "";
    const referenceText = item.referenceName
      ? `분석 기준: ${item.referenceName}${item.cluster ? ` (군집 ${item.cluster})` : ""}`
      : "분석 기준 데이터 없음";
    return `<div class="compat-row">
      <div class="compat-top">
        <span class="compat-name">${item.icon} ${item.name}</span>
        <div class="compat-bw"><div class="compat-bf" style="width:${item.score}%;background:${color}"></div></div>
        <span class="compat-sc" style="color:${color}">${item.score}점</span>
      </div>
      <div class="compat-ref">${referenceText}</div>
      ${warning}
    </div>`;
  }).join("");

  return `<div class="rs"><div class="rs-title">이 직무, 나랑 얼마나 잘 맞을까?</div><div class="compat-list">${rows}</div></div>`;
}

export function renderRecommendationsSection({
  state,
  jobDefs,
  axes,
  axisLabels,
  radarScale
}) {
  const profile = state.axisProfile || {};
  const summary = state.resultSummary || {};
  const blocks = state.selectedJobs.map((jobCode) => {
    const job = jobDefs.find((item) => item.job_code === jobCode);
    const meta = summary.selectedJobMeta?.[jobCode] || {};
    const score = state.compatibility[jobCode] || 0;
    const recommendations = summary.recommendations?.[jobCode] || [];
    if (!recommendations.length) return "";

    const cards = recommendations.map((recommendation) => {
      const similarityPct = typeof recommendation.similarityPct === "number"
        ? recommendation.similarityPct
        : similarityToPct(recommendation.similarity);

      const bars = axes.map((axis) => {
        const jobValue = Math.min((recommendation.weights?.[axis] || 0) * radarScale * 100, 100);
        const myValue = Math.min((profile[axis] || 0) * radarScale * 100, 100);
        const diff = Math.abs(jobValue - myValue);
        const matchColor = diff < 15 ? "var(--success)" : diff < 30 ? "var(--warn)" : "var(--danger)";
        return `<div class="ax-row">
          <span class="ax-lbl">${axisLabels[axis].split("·")[0]}</span>
          <div class="ax-bw">
            <div class="ax-bf-job" style="width:${jobValue.toFixed(0)}%"></div>
            <div class="ax-bf-me" style="width:${myValue.toFixed(0)}%;opacity:0.85"></div>
          </div>
          <span class="ax-vals" style="color:${matchColor}">${diff < 15 ? "✓" : diff < 30 ? "△" : "▽"}</span>
        </div>`;
      }).join("");

      return `<div class="rec-card">
        <div class="rec-hdr">
          <span class="rec-jname">${recommendation.name}</span>
          <span class="rec-pct">유사도 ${similarityPct}%</span>
        </div>
        <div style="font-size:11px;color:var(--sub);margin-bottom:8px">📊 파란 막대는 내 성향, 금색은 이 직업의 기준이에요</div>
        <div class="ax-bars">${bars}</div>
      </div>`;
    }).join("");

    const lowMessage = score < 60
      ? `<p style="font-size:13px;color:var(--sub);margin-bottom:12px">${job.job_name} 적합도 <b style="color:var(--warn)">${score}점</b> — 같은 계열에서 더 잘 맞는 직업을 찾아봤어요.</p>`
      : `<p style="font-size:13px;color:var(--sub);margin-bottom:12px">같은 직업군${meta.clusterId ? `(군집 ${meta.clusterId})` : ""} 내 유사 직업도 살펴보세요.</p>`;

    return `<div style="margin-bottom:28px">
      <div style="font-size:13px;font-weight:700;color:var(--accent);margin-bottom:8px">${job.icon} ${job.job_name} 관련 추천</div>
      ${lowMessage}<div class="rec-list">${cards}</div>
    </div>`;
  }).join("");

  if (!blocks.trim()) return "";
  return `<div class="rs"><div class="rs-title">👀 이런 직업도 잘 맞을 것 같아요</div>${blocks}</div>`;
}

export function renderGapSection({ state, jobDefs, getMissionsByJobCode, esc }) {
  const rows = state.selectedJobs.map((jobCode) => {
    const job = jobDefs.find((item) => item.job_code === jobCode);
    const answer = state.preqAnswers[jobCode] || {};
    const missions = getMissionsByJobCode(jobCode).slice(0, 2);
    const themes = missions.filter(Boolean).map(missionTheme).filter(Boolean);
    const compareText = gapCompareText(answer, missions);
    return `<tr>
      <td>${job.icon} ${job.job_name}</td>
      <td>
        <div style="margin-bottom:6px"><span style="color:var(--sub);font-size:11px">예상 주요 업무</span><br>${esc(answer.q1 || "(입력 없음)")}</div>
        <div><span style="color:var(--sub);font-size:11px">예상 핵심 능력</span><br>${esc(answer.q2 || "(입력 없음)")}</div>
      </td>
      <td>
        <div style="margin-bottom:6px"><span style="color:var(--sub);font-size:11px">실제 미션 유형</span><br>${esc(themes.length ? themes.join(" / ") : "데이터 없음")}</div>
        <div><span style="color:var(--sub);font-size:11px">기대 대비 실제</span><br>${esc(compareText)}</div>
      </td>
    </tr>`;
  }).join("");
  return `<div class="rs"><div class="rs-title">처음 생각과 실제 미션, 얼마나 달랐나요?</div>
  <div style="overflow-x:auto"><table class="gap-tbl">
    <thead><tr><th>직무</th><th>사전 예상</th><th>실제 미션</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div></div>`;
}
