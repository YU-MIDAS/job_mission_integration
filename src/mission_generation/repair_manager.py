# validator 실패 원인을 바탕으로 미션 draft를 LLM 또는 로컬 규칙으로 보정한다.

from __future__ import annotations

import copy
import json
from typing import Any

from .llm_runtime import OpenAIResponsesRuntime


class RepairPromptBuilder:
    """validator 오류를 LLM repair 요청에 필요한 입력 패키지로 바꾼다."""

    locked_fields = [
        "job_identity",
        "target_exec_job",
        "mission.difficulty",
        "mission.task_type",
        "mission.secondary_task_types",
        "mission.allowed_material_types",
    ]

    def build(
        self,
        system_decisions: dict[str, Any],
        mission_output_draft: dict[str, Any],
        validator_result: dict[str, Any],
        allowed_evidence_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """validator가 지적한 항목만 고치도록 제한한 repair_request를 만든다."""

        return {
            "schema_version": "repair_request.v1",
            "attempt": 1,
            "locked_fields": self.locked_fields,
            "system_decisions": system_decisions,
            "allowed_evidence_names": allowed_evidence_names or [],
            "mission_output_draft": mission_output_draft,
            "validator_errors": validator_result.get("errors", []),
            "validator_warnings": validator_result.get("warnings", []),
            "repair_rules": [
                "material.evidence_source must use only exact strings from allowed_evidence_names.",
                "Do not use source_ref file names, XML field names, or invented evidence labels.",
                "For table materials, data.columns keys must be option, strength, weakness, priority and rows must use the same keys.",
                "mission_fact_refs must use only real mission_facts keys such as org_name, domain, period, trend_pattern, main_issue, feedback_themes, and decision_goal.",
                "evaluation.rubric points must sum exactly to 100.",
                "evaluation.rubric.linked_evidence must use job profile evidence names, not material ids.",
                "Respect validator material size limits for chart, log, checklist, memo, email, table, schedule, and card materials.",
                "Chart series count must be 1 or 2.",
                "Keep learner-facing text beginner-friendly and job-experience oriented; do not add professional knowledge requirements.",
                "Make the mission easier than a real workplace task.",
                "Prefer everyday workplace words over specialist terms.",
                "Avoid legal, financial, technical, policy, compliance, or expert judgment unless the material explains it in beginner terms.",
                "Each task must require only one learner action or deliverable.",
                "JSON만 출력한다.",
                "target_exec_job, task_type, secondary_task_types, difficulty는 변경하지 않는다.",
                "allowed_material_types 밖의 material을 추가하지 않는다.",
                "reliability는 {\"status\":\"pending_validation\"}로 둔다.",
                "validator_errors와 validator_warnings에 해당하는 부분만 수정한다.",
            ],
        }


class RepairManager:
    """미션 draft가 validator를 통과하지 못했을 때 실제 LLM 또는 명시적으로 허용된 mock repair를 실행한다."""

    def __init__(
        self,
        runtime: OpenAIResponsesRuntime | None = None,
        allow_mock_without_key: bool = False,
        force_mock: bool = False,
    ) -> None:
        self.runtime = runtime or OpenAIResponsesRuntime()
        self.allow_mock_without_key = allow_mock_without_key
        self.force_mock = force_mock

    def repair(
        self,
        *,
        repair_request: dict[str, Any],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """기본은 실제 API repair를 사용하고, 옵션으로 허용된 경우에만 로컬 규칙 repair를 수행한다."""

        config = self.runtime.config
        if self.force_mock or (not self.runtime.api_key_available() and self.allow_mock_without_key):
            repaired = LocalRuleRepairer().repair(repair_request)
            return {
                "llm_call_result": {
                    "schema_version": "llm_call_result.v1",
                    "provider": "mock",
                    "api": "local_rule_repair",
                    "model": config.model,
                    "call_type": "repair_generation",
                    "reasoning_effort": config.reasoning_effort,
                    "configured_temperature": config.repair_temperature,
                    "temperature_applied": False,
                    "temperature_omitted_reason": config.temperature_application()["temperature_omitted_reason"],
                    "status": "mocked",
                    "output_json": repaired,
                    "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
                    "errors": [],
                    "attempt_count": 1,
                    "retry_count": 0,
                    "retry_errors": [],
                },
                "mission_draft": repaired,
            }

        prompts = self._repair_prompts(repair_request)
        call_result = self.runtime.call_structured(
            call_type="repair_generation",
            system_prompt=prompts["system"],
            user_prompt=prompts["user"],
            json_schema=json_schema,
            temperature=config.repair_temperature,
            max_output_tokens=config.max_output_tokens_repair,
        )
        return {"llm_call_result": call_result, "mission_draft": call_result.get("output_json")}

    def _repair_prompts(self, repair_request: dict[str, Any]) -> dict[str, str]:
        """validator 오류를 고치는 데 필요한 최소한의 repair prompt를 만든다."""

        system = (
            "너는 validator가 지적한 문제만 고치는 JSON repair 작성자다. "
            "시스템 결정 필드와 allowed_material_types를 변경하지 않는다. "
            "reliability score나 passed를 만들지 않는다."
        )
        user = (
            "아래 repair_request에 따라 mission_output draft를 수정하라. JSON만 출력한다.\n"
            "Return one complete valid JSON object.\n"
            "Do not truncate the JSON.\n"
            "Do not include comments, trailing commas, Markdown, or text outside JSON.\n"
            "Ensure all strings are properly closed and escaped.\n"
            "Stability requirements:\n"
            "- Use mission_fact_refs as key names only, such as org_name, domain, period, trend_pattern, main_issue, feedback_themes, and decision_goal.\n"
            "- Do not use mission_facts.period, mission_fact_period, source_ref fields, XML fields, or invented fact labels as mission_fact_refs.\n"
            "- Make evaluation.rubric points sum exactly to 100.\n"
            "- Use exact job_profile evidence item names in evaluation.rubric.linked_evidence; do not use material ids such as mat_001, mat_002, or m1.\n"
            "- Respect material size limits for chart, log, checklist, memo, email, table, schedule, and card materials.\n"
            "- Keep chart series count at 1 or 2.\n"
            "- Preserve beginner-friendly job-experience wording; do not add external research or professional knowledge requirements.\n"
            "- Make the mission easier than a real workplace task.\n"
            "- Prefer everyday workplace words over specialist terms.\n"
            "- Avoid legal, financial, technical, policy, compliance, or expert judgment unless the material explains it in beginner terms.\n"
            "- Preserve the exact difficulty counts: easy has 1 material and 1 task, normal has 2 materials and 2 tasks, hard has 3 materials and 2 tasks.\n"
            "- Easy answers should be 1-2 short sentences and should identify one obvious issue, choose one option, or explain one visible pattern.\n"
            "- Normal tasks should ask for only one simple comparison or one simple recommendation; each answer should be 2-3 short sentences.\n"
            "- Hard means more materials, not expert-level reasoning; ask for one decision and one caution, each answer 3-5 short sentences, with all clues visible in the materials.\n"
            "- Each task must require only one learner action or deliverable.\n"
            "- Each task must ask for a short descriptive written response, not a code-only, letter-only, number-only, or single-word answer.\n"
            f"{json.dumps(repair_request, ensure_ascii=False)}"
        )
        return {"system": system, "user": user}


class LocalRuleRepairer:
    """API를 쓰지 않는 mock 경로에서 최소한의 구조 오류를 보정하는 repairer."""

    def repair(self, repair_request: dict[str, Any]) -> dict[str, Any]:
        """system_decisions와 material/fact 참조를 기준으로 draft를 보정한다."""

        draft = copy.deepcopy(repair_request["mission_output_draft"])
        decisions = repair_request["system_decisions"]
        draft["schema_version"] = "mission_output.v1"
        draft["mission_id"] = "draft"
        draft["target_exec_job"] = copy.deepcopy(decisions["selected_exec_job"])
        draft.setdefault("mission", {})
        draft["mission"]["task_type"] = decisions["primary_task_type"]
        draft["mission"]["secondary_task_types"] = copy.deepcopy(decisions.get("secondary_task_types", []))
        draft["mission"]["difficulty"] = copy.deepcopy(decisions["difficulty"])
        draft["reliability"] = {"status": "pending_validation"}
        draft.pop("evidence_chain", None)

        allowed = set(decisions.get("allowed_material_types", []))
        materials = draft.get("mission", {}).get("materials", [])
        if isinstance(materials, list):
            draft["mission"]["materials"] = [item for item in materials if item.get("type") in allowed]
        facts = draft.get("mission_facts") or {}
        fact_keys = set(facts)
        for material in draft.get("mission", {}).get("materials", []):
            material.setdefault("factual_status", "synthetic_mission_material")
            refs = [ref for ref in material.get("mission_fact_refs", []) if ref in fact_keys]
            if not refs and fact_keys:
                refs = list(fact_keys)[:1]
            material["mission_fact_refs"] = refs
        self._repair_rubric_points(draft)
        return draft

    def _repair_rubric_points(self, draft: dict[str, Any]) -> None:
        """mock repair에서 rubric 점수 합계가 100이 되도록 균등 보정한다."""

        rubric = draft.get("evaluation", {}).get("rubric")
        if not isinstance(rubric, list) or not rubric:
            return
        total = sum(item.get("points", 0) for item in rubric if isinstance(item.get("points"), int))
        if total == 100:
            return
        base = 100 // len(rubric)
        remainder = 100 - base * len(rubric)
        for idx, item in enumerate(rubric):
            item["points"] = base + (remainder if idx == 0 else 0)
