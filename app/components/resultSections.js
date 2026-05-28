function confidenceBar(confidence) {
  const filled = Math.round((confidence || 0) * 5);
  return "<span class=\"conf-dot filled\"></span>".repeat(filled) +
    "<span class=\"conf-dot\"></span>".repeat(5 - filled);
}

export function renderEvidenceSection({ axes, summary, axisMeta, levelInfo, esc }) {
  const hasAnyEvidence = axes.some((axis) => summary[axis].evidence.length > 0 || summary[axis].score > 0);
  if (!hasAnyEvidence) return "";

  const cards = axes.map((axis) => {
    const result = summary[axis];
    const level = Math.min(result.score || 0, 4);
    const meta = axisMeta[axis];
    const info = levelInfo[level];
    const confidence = result.confidence || 0;
    const confidencePct = Math.round(confidence * 100);
    const isLow = level > 0 && confidence < 0.55 && result.evidence.length === 0;

    const foundItems = result.evidence.slice(0, 2).map((evidence) => {
      const behaviorMap = {
        AX1: "수치나 데이터를 활용해 분석했어요",
        AX2: "상황을 면밀히 관찰하고 변수를 짚었어요",
        AX3: "선택지를 비교하거나 우선순위를 정했어요",
        AX4: "팀·조직 관점에서 역할과 흐름을 생각했어요",
        AX5: "상대방 입장을 고려하거나 공감을 표현했어요"
      };
      const behavior = behaviorMap[evidence.primary_axis] || evidence.behavior || "";
      return `<div class="ev-found">
        <div class="ev-found-behavior">💡 ${behavior}</div>
        <div class="ev-found-quote">"${esc(evidence.quote || "")}"</div>
      </div>`;
    }).join("");

    const noEvidenceNote = level === 0
      ? `<div class="ev-none">이 미션에서는 이 능력이 잘 드러나지 않았어요. 다음엔 ${meta.examples[0]}을(를) 포함해 보세요.</div>`
      : "";

    const lowConfNote = isLow
      ? "<div class=\"ev-lowconf\">⚠️ AI가 이 부분을 판단하기 어려웠어요. 답변이 짧거나 관련 내용이 적었을 수 있어요.</div>"
      : "";

    return `<div class="ev-card2 ${level === 0 ? "ev-card2-zero" : level >= 3 ? "ev-card2-high" : ""}">
      <div class="ev2-header">
        <div class="ev2-icon">${meta.icon}</div>
        <div class="ev2-title-wrap">
          <div class="ev2-name">${meta.name}</div>
          <div class="ev2-desc">${meta.desc}</div>
        </div>
        <div class="ev2-badge" style="background:${info.color}20;color:${info.color};border:1px solid ${info.color}40">
          ${info.emoji} ${info.label}
        </div>
      </div>
      <div class="ev2-msg">${info.msg}</div>
      ${foundItems}${noEvidenceNote}${lowConfNote}
      <div class="ev2-conf">
        평가 확실도 ${confidenceBar(confidence)} <span style="color:var(--sub);font-size:11px">${confidencePct < 40 ? "낮음" : confidencePct < 70 ? "보통" : "높음"}</span>
      </div>
    </div>`;
  }).join("");

  return `<div class="rs"><div class="rs-title">✨ 능력별 상세 피드백</div><div class="ev-list">${cards}</div></div>`;
}

export function renderQuadSection({ selectedJobs, jobDefs, jobInsights }) {
  const cards = selectedJobs.map((jobCode) => {
    const job = jobDefs.find((item) => item.job_code === jobCode);
    const insight = jobInsights[jobCode] || {
      quadrant: "q4",
      label: "수행 낮음 · 몰입 추정 낮음",
      detail: `${job?.job_name || jobCode} 평가 데이터가 없어 기본값으로 표시됩니다.`
    };
    return `<div class="quad-cell ${insight.quadrant}${insight.quadrant === "q1" ? " hl" : ""}">
      <div class="quad-jname">${job.icon} ${job.job_name}</div>
      <div class="quad-lbl">${insight.label}</div>
      <div class="quad-desc">${insight.detail}</div>
    </div>`;
  }).join("");

  return `<div class="rs"><div class="rs-title">잘 했나요? 얼마나 몰입했나요?</div><div class="quad-grid">${cards}</div></div>`;
}
