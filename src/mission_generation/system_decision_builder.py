# selector 결과 또는 이전 규칙을 LLM이 따라야 할 system_decisions로 확정한다.

from __future__ import annotations

from typing import Any

from .config import EXCLUDED_MATERIAL_TYPES, MATERIAL_TYPES, PILOT_JOB_CONFIGS, TASK_TYPES


class SystemDecisionError(RuntimeError):
    """수행직무, task type, 난이도 등 system_decisions 구성이 실패했을 때의 오류."""

    pass


TASK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "research_and_analysis": ("조사", "수집", "분석", "파악", "검토", "예측", "처리"),
    "planning_and_proposal": ("기획", "개발", "제안", "계획", "설계", "수립"),
    "decision_making": ("선택", "판단", "결정", "평가", "우선순위"),
    "coordination_and_negotiation": ("협의", "조율", "계약", "협상", "조정"),
    "diagnosis_and_improvement": ("진단", "개선", "수정", "문제점", "점검", "보완"),
    "operation_and_scheduling": ("일정", "운영", "관리", "배치"),
    "communication_and_reporting": ("보고", "전달", "설명", "작성", "발표"),
}

GENERAL_EXEC_JOB_KEYWORDS: dict[str, int] = {
    "조사": 3,
    "수집": 3,
    "분석": 3,
    "파악": 3,
    "검토": 3,
    "기획": 3,
    "개발": 3,
    "제안": 3,
    "설계": 3,
    "선택": 2,
    "판단": 2,
    "결정": 2,
    "평가": 2,
    "우선순위": 2,
    "협의": 2,
    "조율": 2,
    "계약": 2,
    "협상": 2,
    "일정": 2,
    "운영": 2,
    "관리": 2,
    "보고": 2,
    "전달": 2,
    "작성": 2,
}

EVIDENCE_MATERIAL_HINTS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("이메일", "소통", "문서 주고받기"), ("email",)),
    (("일정", "우선순위", "마감"), ("schedule",)),
    (("정보 처리", "컴퓨터", "데이터"), ("log", "table")),
    (("기준", "평가", "위험", "점검"), ("checklist",)),
    (("비교", "대안", "후보", "사람", "서비스"), ("card", "table")),
    (("정보 수집", "자료 분석", "정보, 자료 분석", "분석"), ("chart", "table")),
    (("목표", "전략"), ("table", "schedule", "card")),
]


MISSION_DESIGN_TYPES = {
    "market_feedback_prioritization",
    "data_diagnosis",
    "financial_research_judgment",
    "product_design_with_constraints",
    "general_research_analysis",
}

MISSION_DESIGN_INTENTS: dict[str, str] = {
    "market_feedback_prioritization": "고객 반응, 판매 흐름, 후보안을 비교해 개선 우선순위를 정하는 미션",
    "data_diagnosis": "지표와 로그를 바탕으로 원인을 진단하고 개선 방향을 판단하는 미션",
    "financial_research_judgment": "경제, 산업, 금융 성격의 자료를 종합해 우선순위나 전망을 판단하는 미션",
    "product_design_with_constraints": "수요, 비용, 제약조건을 고려해 상품 또는 서비스 설계안을 제안하는 미션",
    "general_research_analysis": "제공 자료를 조사, 비교, 분석해 근거 기반 결론을 도출하는 일반 분석 미션",
}

MISSION_DESIGN_SIGNALS: dict[str, tuple[str, ...]] = {
    "market_feedback_prioritization": (
        "소비자",
        "고객",
        "구매",
        "판매",
        "마케팅",
        "영업",
        "상품",
        "기획",
        "시장성",
        "만족",
        "평가",
        "피드백",
        "취향",
        "디자인",
    ),
    "data_diagnosis": (
        "데이터",
        "대용량",
        "처리",
        "플랫폼",
        "마이닝",
        "네트워크",
        "클러스터",
        "시각화",
        "컴퓨터",
        "전자공학",
        "전산",
        "로그",
        "수리력",
        "논리적 분석",
        "정보 처리",
        "정보, 자료 분석",
        "기술 분석",
    ),
    "financial_research_judgment": (
        "투자",
        "주식",
        "채권",
        "금융",
        "경제",
        "산업",
        "기업",
        "재무",
        "회계",
        "수익",
        "주가",
        "시장",
        "파생상품",
        "거래량",
        "보고서",
        "전망",
        "평가방법",
    ),
    "product_design_with_constraints": (
        "보험",
        "보험상품",
        "보험료",
        "책임준비금",
        "약관",
        "사망률",
        "재해율",
        "질병",
        "장애",
        "퇴직률",
        "수리",
        "통계",
        "사회환경",
        "경제실정",
        "준비금",
        "위험성",
        "수요",
        "비용",
        "제약",
    ),
}

PILOT_MISSION_DESIGN_FALLBACK: dict[str, str] = {
    "K000000997": "market_feedback_prioritization",
    "K000001080": "data_diagnosis",
    "K000001179": "financial_research_judgment",
    "K000007519": "product_design_with_constraints",
}


class SystemDecisionBuilder:
    """선택된 수행직무와 난이도를 LLM이 따라야 할 system_decisions로 확정한다."""

    def build(
        self,
        job_profile: dict[str, Any],
        requested_difficulty: str,
        pilot_job_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """LLM selector를 쓰지 않는 옵션/이전 규칙 경로에서 규칙 기반 결정을 만든다."""

        if requested_difficulty not in {"easy", "normal", "hard"}:
            raise SystemDecisionError(f"invalid difficulty: {requested_difficulty}")
        job_cd = job_profile["job_identity"]["job_cd"]
        config = pilot_job_config if pilot_job_config is not None else PILOT_JOB_CONFIGS.get(job_cd, {})
        warnings: list[dict[str, str]] = []
        trace: list[dict[str, Any]] = []
        if not config:
            warnings.append(
                {
                    "code": "PILOT_CONFIG_MISSING",
                    "severity": "warning",
                    "message": f"No pilot job config found for {job_cd}; rule fallback will be used.",
                }
            )

        selected_exec_job = self._select_exec_job(job_profile, config, trace, warnings)
        primary_task_type = self._primary_task_type(job_profile, selected_exec_job, config, trace, warnings)
        secondary_task_types = self._secondary_task_types(selected_exec_job["text"], primary_task_type)
        allowed_material_types = self._allowed_material_types(
            job_profile,
            requested_difficulty,
            config,
            trace,
            warnings,
        )
        mission_design = self._mission_design(job_profile, selected_exec_job, trace)
        difficulty = self._difficulty(requested_difficulty)

        selected_exec_job = dict(selected_exec_job)
        selected_exec_job["selection_reason"] = trace[0]["reason"] if trace else "rule based selection"

        return {
            "schema_version": "system_decisions.v1",
            "job_cd": job_cd,
            "job_name": job_profile["job_identity"].get("job_smcl_nm", ""),
            "difficulty": difficulty,
            "selected_exec_job": selected_exec_job,
            "primary_task_type": primary_task_type,
            "secondary_task_types": secondary_task_types,
            "allowed_material_types": allowed_material_types,
            "mission_design": mission_design,
            "excluded_material_types": sorted(EXCLUDED_MATERIAL_TYPES),
            "generation_constraints": {
                "language": "ko",
                "json_only": True,
                "no_real_company_names": True,
                "no_real_person_names": True,
                "no_external_research": True,
                "non_expert_friendly": True,
                "must_use_provided_materials_only": True,
                "llm_must_not_create_reliability_score": True,
                "factual_status_required": True,
            },
            "decision_trace": trace,
            "decision_warnings": warnings,
        }

    def build_from_selector(
        self,
        job_profile: dict[str, Any],
        requested_difficulty: str,
        selector_result: dict[str, Any],
    ) -> dict[str, Any]:
        """기본 경로에서 MissionDecisionSelector 결과를 system_decisions로 변환한다."""

        if requested_difficulty not in {"easy", "normal", "hard"}:
            raise SystemDecisionError(f"invalid difficulty: {requested_difficulty}")
        exec_jobs = job_profile["work"]["exec_jobs"]
        by_id = {item["exec_job_id"]: item for item in exec_jobs}
        selected_exec_job = dict(by_id[selector_result["selected_exec_job_id"]])
        selected_exec_job["selection_reason"] = selector_result["selection_reason"]
        primary_task_type = selector_result["primary_task_type"]
        mission_design_type = selector_result["mission_design_type"]
        trace = [
            {
                "step": "llm_decision_selector",
                "method": "llm_structured_output",
                "selected": selected_exec_job["exec_job_id"],
                "reason": selector_result["selection_reason"],
                "matched_evidence": selector_result.get("matched_evidence", []),
                "confidence": selector_result.get("confidence"),
                "primary_task_type": primary_task_type,
                "selected_material_types": selector_result.get("selected_material_types", []),
                "mission_design_type": mission_design_type,
            }
        ]
        return {
            "schema_version": "system_decisions.v1",
            "job_cd": job_profile["job_identity"]["job_cd"],
            "job_name": job_profile["job_identity"].get("job_smcl_nm", ""),
            "difficulty": self._difficulty(requested_difficulty),
            "selected_exec_job": selected_exec_job,
            "primary_task_type": primary_task_type,
            "secondary_task_types": self._secondary_task_types(selected_exec_job["text"], primary_task_type),
            "allowed_material_types": list(selector_result["selected_material_types"]),
            "mission_design": {
                "schema_version": "mission_design.v1",
                "mission_design_type": mission_design_type,
                "design_intent": MISSION_DESIGN_INTENTS[mission_design_type],
                "selection_method": "llm_decision_selector",
                "selection_reason": selector_result["selection_reason"],
            },
            "excluded_material_types": sorted(EXCLUDED_MATERIAL_TYPES),
            "generation_constraints": {
                "language": "ko",
                "json_only": True,
                "no_real_company_names": True,
                "no_real_person_names": True,
                "no_external_research": True,
                "non_expert_friendly": True,
                "must_use_provided_materials_only": True,
                "llm_must_not_create_reliability_score": True,
                "factual_status_required": True,
            },
            "decision_trace": trace,
            "decision_warnings": [],
        }

    def _select_exec_job(
        self,
        profile: dict[str, Any],
        config: dict[str, Any],
        trace: list[dict[str, Any]],
        warnings: list[dict[str, str]],
    ) -> dict[str, Any]:
        """설정값, 키워드 점수, fallback 순서로 목표 수행직무를 하나 고른다."""

        exec_jobs = profile["work"]["exec_jobs"]
        by_id = {item["exec_job_id"]: item for item in exec_jobs}
        preferred_id = config.get("preferred_exec_job_id")
        if preferred_id and preferred_id in by_id:
            trace.append(
                {
                    "step": "select_target_exec_job",
                    "method": "pilot_config.preferred_exec_job_id",
                    "selected": preferred_id,
                    "reason": f"Pilot config selected {preferred_id}.",
                }
            )
            return by_id[preferred_id]
        if preferred_id and preferred_id not in by_id:
            warnings.append(
                {
                    "code": "PREFERRED_EXEC_JOB_NOT_FOUND",
                    "severity": "warning",
                    "message": f"{preferred_id} is not present in profile exec_jobs.",
                }
            )

        keywords = config.get("preferred_exec_job_keywords") or []
        if keywords:
            scored = [(sum(1 for keyword in keywords if keyword in item["text"]), item) for item in exec_jobs]
            best_score, best_item = max(scored, key=lambda pair: (pair[0], self._general_exec_score(pair[1]["text"])))
            if best_score > 0:
                trace.append(
                    {
                        "step": "select_target_exec_job",
                        "method": "preferred_exec_job_keywords",
                        "selected": best_item["exec_job_id"],
                        "reason": f"Matched {best_score} preferred keywords.",
                    }
                )
                return best_item

        scored = [(self._general_exec_score(item["text"]), item) for item in exec_jobs]
        best_score, best_item = max(scored, key=lambda pair: pair[0])
        if best_score > 0:
            trace.append(
                {
                    "step": "select_target_exec_job",
                    "method": "general_verb_rule",
                    "selected": best_item["exec_job_id"],
                    "reason": f"General keyword score {best_score}.",
                }
            )
            return best_item

        warnings.append(
            {
                "code": "TARGET_EXEC_JOB_FALLBACK_USED",
                "severity": "warning",
                "message": "Could not select target_exec_job by config or rules; first execJob is used.",
            }
        )
        trace.append(
            {
                "step": "select_target_exec_job",
                "method": "first_exec_job_fallback",
                "selected": exec_jobs[0]["exec_job_id"],
                "reason": "No keyword score was available.",
            }
        )
        return exec_jobs[0]

    def _general_exec_score(self, text: str) -> int:
        return sum(weight for keyword, weight in GENERAL_EXEC_JOB_KEYWORDS.items() if keyword in text)

    def _primary_task_type(
        self,
        profile: dict[str, Any],
        selected_exec_job: dict[str, Any],
        config: dict[str, Any],
        trace: list[dict[str, Any]],
        warnings: list[dict[str, str]],
    ) -> str:
        """수행직무 문장과 활동 evidence를 바탕으로 대표 task type을 정한다."""

        preferred = config.get("preferred_primary_task_type")
        if preferred:
            if preferred not in TASK_TYPES:
                warnings.append(
                    {
                        "code": "TASK_TYPE_RULE_LOW_CONFIDENCE",
                        "severity": "warning",
                        "message": f"Preferred task_type {preferred} is invalid; rule fallback used.",
                    }
                )
            else:
                trace.append(
                    {
                        "step": "classify_task_type",
                        "method": "pilot_config.preferred_primary_task_type",
                        "selected": preferred,
                        "reason": "Pilot config fixed the primary task type.",
                    }
                )
                return preferred

        text = selected_exec_job["text"]
        scores = {task_type: sum(1 for keyword in keywords if keyword in text) for task_type, keywords in TASK_KEYWORDS.items()}
        best_task, best_score = max(scores.items(), key=lambda pair: (pair[1], 1 if pair[0] == "research_and_analysis" else 0))
        if best_score == 0:
            activity_names = " ".join(item["name"] for item in profile["evidence"].get("work_activities", [])[:5])
            scores = {
                task_type: sum(1 for keyword in keywords if keyword in activity_names)
                for task_type, keywords in TASK_KEYWORDS.items()
            }
            best_task, best_score = max(scores.items(), key=lambda pair: pair[1])
        if best_score == 0:
            best_task = "research_and_analysis"
            warnings.append(
                {
                    "code": "TASK_TYPE_RULE_LOW_CONFIDENCE",
                    "severity": "warning",
                    "message": "Task type keyword score was zero; research_and_analysis is used.",
                }
            )
        trace.append(
            {
                "step": "classify_task_type",
                "method": "verb_rule",
                "selected": best_task,
                "reason": f"Keyword score {best_score}.",
            }
        )
        return best_task

    def _secondary_task_types(self, text: str, primary_task_type: str) -> list[str]:
        """대표 task type을 보조할 수 있는 부가 task type을 최대 2개까지 고른다."""

        selected: list[str] = []
        if primary_task_type == "research_and_analysis" and any(word in text for word in ("기획", "개발", "제안", "수립")):
            selected.append("planning_and_proposal")
        if primary_task_type == "planning_and_proposal" and any(word in text for word in ("조사", "수집", "분석", "파악")):
            selected.append("research_and_analysis")
        if any(word in text for word in ("선택", "판단", "결정", "평가", "우선순위")):
            selected.append("decision_making")
        if any(word in text for word in ("보고", "전달", "설명", "작성")):
            selected.append("communication_and_reporting")
        result: list[str] = []
        for task_type in selected:
            if task_type != primary_task_type and task_type not in result:
                result.append(task_type)
        return result[:2]

    def _allowed_material_types(
        self,
        profile: dict[str, Any],
        difficulty: str,
        config: dict[str, Any],
        trace: list[dict[str, Any]],
        warnings: list[dict[str, str]],
    ) -> list[str]:
        """난이도와 evidence 힌트를 기준으로 LLM이 만들 수 있는 자료 유형을 제한한다."""

        max_count = {"easy": 1, "normal": 2, "hard": 3}[difficulty]
        configured = list((config.get("materials") or {}).get(difficulty) or [])
        allowed = [item for item in configured if item in MATERIAL_TYPES]
        if not allowed:
            fallback_materials = {
                "easy": ["memo"],
                "normal": ["chart", "table"],
                "hard": ["chart", "table", "memo"],
            }
            allowed = fallback_materials[difficulty]

        if not configured:
            evidence_text = " ".join(
                item.get("name", "")
                for group in profile.get("evidence", {}).values()
                for item in group
                if item.get("score", 0) >= 80
            )
            for keywords, material_types in EVIDENCE_MATERIAL_HINTS:
                if any(keyword in evidence_text for keyword in keywords):
                    for material_type in material_types:
                        if material_type not in allowed and len(allowed) < max_count:
                            allowed.append(material_type)
                        elif material_type not in allowed and len(allowed) >= max_count:
                            warnings.append(
                                {
                                    "code": "MATERIAL_CANDIDATE_TRIMMED",
                                    "severity": "warning",
                                    "message": f"{material_type} was suggested by evidence but trimmed by max count.",
                                }
                            )
        allowed = allowed[:max_count]
        trace.append(
            {
                "step": "select_allowed_material_types",
                "method": "pilot_job_material_config",
                "selected": allowed,
                "reason": f"{difficulty} material candidates were selected within max {max_count}.",
            }
        )
        return allowed

    def _mission_design(
        self,
        profile: dict[str, Any],
        selected_exec_job: dict[str, Any],
        trace: list[dict[str, Any]],
    ) -> dict[str, str]:
        """직무 profile 신호를 미션 설계 의도 유형으로 압축한다."""

        job_cd = profile["job_identity"]["job_cd"]
        signal_text = self._mission_design_signal_text(profile, selected_exec_job)
        scores = {
            design_type: sum(1 for signal in signals if signal.lower() in signal_text)
            for design_type, signals in MISSION_DESIGN_SIGNALS.items()
        }
        top_score = max(scores.values()) if scores else 0
        top_types = [design_type for design_type, score in scores.items() if score == top_score]

        if top_score >= 1 and len(top_types) == 1:
            selected = top_types[0]
            matched = [signal for signal in MISSION_DESIGN_SIGNALS[selected] if signal.lower() in signal_text]
            selection_method = "profile_signal_rule"
            selection_reason = f"Matched signals: {', '.join(matched[:5])}."
        elif job_cd in PILOT_MISSION_DESIGN_FALLBACK:
            selected = PILOT_MISSION_DESIGN_FALLBACK[job_cd]
            selection_method = "pilot_fallback"
            selection_reason = f"Low-confidence signal result; pilot fallback selected {selected} for {job_cd}."
        else:
            selected = "general_research_analysis"
            selection_method = "general_fallback"
            selection_reason = "No strong mission design signal and no pilot fallback matched."

        trace.append(
            {
                "step": "select_mission_design_type",
                "method": selection_method,
                "selected": selected,
                "reason": selection_reason,
                "scores": scores,
            }
        )
        return {
            "schema_version": "mission_design.v1",
            "mission_design_type": selected,
            "design_intent": MISSION_DESIGN_INTENTS[selected],
            "selection_method": selection_method,
            "selection_reason": selection_reason,
        }

    def _mission_design_signal_text(
        self,
        profile: dict[str, Any],
        selected_exec_job: dict[str, Any],
    ) -> str:
        parts: list[str] = [str(selected_exec_job.get("text", ""))]
        for item in profile.get("work", {}).get("exec_jobs", []):
            parts.append(str(item.get("text", "")))
        evidence = profile.get("evidence", {})
        for group_name in ("knowledge", "abilities", "work_activities"):
            for item in evidence.get(group_name, []):
                parts.append(str(item.get("name", "")))
                parts.append(str(item.get("description", "")))
        return " ".join(parts).lower()

    def _difficulty(self, difficulty: str) -> dict[str, Any]:
        """난이도 코드별 시간, 자료 수, task 수 정책을 반환한다."""

        if difficulty == "easy":
            return {
                "level": "easy",
                "label": "쉬움",
                "estimated_time_minutes": 10,
                "material_bundle_style": "single_work_material",
                "material_count_range": [1, 1],
                "task_count_range": [1, 1],
                "answer_length_hint": "1-2 short sentences",
                "requires_cross_material_reasoning": False,
                "requires_tradeoff_judgment": False,
                "requires_domain_expertise": False,
            }
        if difficulty == "normal":
            return {
                "level": "normal",
                "label": "보통",
                "estimated_time_minutes": 15,
                "material_bundle_style": "light_work_material_bundle",
                "material_count_range": [2, 2],
                "task_count_range": [2, 2],
                "answer_length_hint": "2-3 short sentences per task",
                "requires_cross_material_reasoning": True,
                "requires_tradeoff_judgment": False,
                "requires_domain_expertise": False,
            }
        return {
            "level": "hard",
            "label": "어려움",
            "estimated_time_minutes": 20,
            "material_bundle_style": "work_document_packet",
            "material_count_range": [3, 3],
            "task_count_range": [2, 2],
            "answer_length_hint": "3-5 short sentences per task",
            "requires_cross_material_reasoning": True,
            "requires_tradeoff_judgment": False,
            "requires_domain_expertise": False,
        }
