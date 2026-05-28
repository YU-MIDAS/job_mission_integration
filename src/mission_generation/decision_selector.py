# 최종 미션 생성 전에 LLM으로 수행직무, task type, 자료 유형 방향을 고른다.

from __future__ import annotations

import json
from typing import Any

from .config import EXCLUDED_MATERIAL_TYPES, MATERIAL_TYPES, RuntimeConfig, TASK_TYPES
from .llm_runtime import OpenAIResponsesRuntime
from .system_decision_builder import MISSION_DESIGN_TYPES


CONFIDENCE_VALUES = {"high", "medium", "low"}


class DecisionSelectorInputBuilder:
    """최종 미션 작성 전에 LLM이 고를 수 있는 후보 목록만 압축해 만든다."""

    def build(self, job_profile: dict[str, Any], difficulty_code: str) -> dict[str, Any]:
        """job_profile에서 selector가 판단할 수행직무, evidence, material 후보만 추린다."""

        material_count_range = {
            "easy": [1, 1],
            "normal": [2, 2],
            "hard": [3, 3],
        }.get(difficulty_code, [2, 2])
        evidence = job_profile.get("evidence", {})
        return {
            "schema_version": "decision_selector_input.v1",
            "job_identity": {
                "job_cd": job_profile.get("job_identity", {}).get("job_cd", ""),
                "job_name": job_profile.get("job_identity", {}).get("job_smcl_nm", ""),
            },
            "difficulty": {
                "level": difficulty_code,
                "material_count_range": material_count_range,
            },
            "exec_jobs": [
                {
                    "exec_job_id": item.get("exec_job_id", ""),
                    "text": item.get("text", ""),
                    "source_ref": item.get("source_ref", {}),
                }
                for item in job_profile.get("work", {}).get("exec_jobs", [])
            ],
            "top_evidence": {
                "abilities": self._top_evidence(evidence.get("abilities", [])),
                "knowledge": self._top_evidence(evidence.get("knowledge", [])),
                "work_activities": self._top_evidence(evidence.get("work_activities", [])),
            },
            "allowed_task_types": sorted(TASK_TYPES),
            "allowed_material_types": sorted(MATERIAL_TYPES - EXCLUDED_MATERIAL_TYPES),
            "allowed_mission_design_types": sorted(MISSION_DESIGN_TYPES),
        }

    def _top_evidence(self, items: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        """selector prompt에 넣기 좋게 evidence의 이름, 점수, 설명만 남긴다."""

        return [
            {
                "name": item.get("name", ""),
                "score": item.get("score"),
                "description": item.get("description", ""),
            }
            for item in items[:limit]
            if item.get("name")
        ]


class DecisionSelectorPromptBuilder:
    """MissionDecisionSelector가 사용할 system/user prompt를 만든다."""

    def prompts(self, selector_input: dict[str, Any]) -> dict[str, str]:
        """selector_input을 final mission이 아닌 방향 선택용 prompt로 직렬화한다."""

        system_prompt = (
            "You select system decision candidates for a job-based mission generator. "
            "Do not write the final learner-facing mission. Return only valid JSON."
        )
        user_prompt = (
            "다음 decision_selector_input을 보고 system_decisions 후보만 선택하라.\n"
            "selected_exec_job_id는 반드시 exec_jobs 중 하나여야 한다.\n"
            "primary_task_type은 allowed_task_types 중 하나여야 한다.\n"
            "selected_material_types는 allowed_material_types 중에서 난이도 material_count_range에 맞게 고른다.\n"
            "mission_design_type은 allowed_mission_design_types 중 하나여야 한다.\n"
            "matched_evidence는 입력에 실제 존재하는 evidence name만 사용한다.\n"
            "selection_reason은 선택 근거를 간결한 한국어로 설명한다.\n\n"
            f"decision_selector_input:\n{json.dumps(selector_input, ensure_ascii=False)}"
        )
        return {"system": system_prompt, "user": user_prompt}


class MissionDecisionSelector:
    """미션 본문이 아니라 수행직무/task/material 같은 생성 방향만 고르는 LLM 호출자."""

    def __init__(
        self,
        runtime: OpenAIResponsesRuntime | None = None,
        force_mock: bool = False,
    ) -> None:
        self.runtime = runtime or OpenAIResponsesRuntime()
        self.force_mock = force_mock

    def select(self, selector_input: dict[str, Any]) -> dict[str, Any]:
        """기본 경로에서 LLM에게 수행직무/task/material 방향을 선택하게 한다."""

        if self.force_mock:
            return self._skipped("DECISION_SELECTOR_MOCK_MODE", "Decision selector is disabled in mock mode.")
        if not self.runtime.api_key_available():
            return self._skipped("OPENAI_API_KEY_MISSING", "OPENAI_API_KEY is not set.")

        prompts = DecisionSelectorPromptBuilder().prompts(selector_input)
        config = self.runtime.config
        call_result = self.runtime.call_structured(
            call_type="decision_selection",
            system_prompt=prompts["system"],
            user_prompt=prompts["user"],
            json_schema=self.structured_output_schema(selector_input),
            schema_name="decision_selector_v1",
            temperature=config.draft_temperature,
            max_output_tokens=min(3000, config.max_output_tokens_draft),
        )
        return {
            "schema_version": "decision_selector_run.v1",
            "llm_call_result": call_result,
            "selector_result": call_result.get("output_json"),
        }

    def structured_output_schema(self, selector_input: dict[str, Any]) -> dict[str, Any]:
        """selector 응답을 허용 후보 안으로 묶는 strict JSON schema를 만든다."""

        exec_job_ids = [item["exec_job_id"] for item in selector_input.get("exec_jobs", []) if item.get("exec_job_id")]
        evidence_names = sorted(self._selector_evidence_names(selector_input))
        material_range = selector_input.get("difficulty", {}).get("material_count_range") or [2, 2]
        material_min, material_max = int(material_range[0]), int(material_range[1])
        matched_evidence_items: dict[str, Any] = {"type": "string"}
        if evidence_names:
            matched_evidence_items["enum"] = evidence_names
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "selected_exec_job_id",
                "primary_task_type",
                "selected_material_types",
                "mission_design_type",
                "matched_evidence",
                "selection_reason",
                "confidence",
            ],
            "properties": {
                "selected_exec_job_id": {"type": "string", "enum": exec_job_ids or [""]},
                "primary_task_type": {"type": "string", "enum": selector_input.get("allowed_task_types", [])},
                "selected_material_types": {
                    "type": "array",
                    "minItems": material_min,
                    "maxItems": material_max,
                    "items": {"type": "string", "enum": selector_input.get("allowed_material_types", [])},
                },
                "mission_design_type": {
                    "type": "string",
                    "enum": selector_input.get("allowed_mission_design_types", []),
                },
                "matched_evidence": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": matched_evidence_items,
                },
                "selection_reason": {"type": "string"},
                "confidence": {"type": "string", "enum": sorted(CONFIDENCE_VALUES)},
            },
        }

    def _skipped(self, code: str, message: str) -> dict[str, Any]:
        """API를 호출하지 않은 selector 결과도 표준 run 구조로 반환한다."""

        config = self.runtime.config
        return {
            "schema_version": "decision_selector_run.v1",
            "llm_call_result": {
                "schema_version": "llm_call_result.v1",
                "provider": "local",
                "api": "not_called",
                "model": config.model,
                "call_type": "decision_selection",
                "reasoning_effort": config.reasoning_effort,
                "configured_temperature": config.draft_temperature,
                "temperature_applied": False,
                "temperature_omitted_reason": config.temperature_application()["temperature_omitted_reason"],
                "status": "skipped",
                "output_json": None,
                "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
                "errors": [{"code": code, "message": message}],
                "attempt_count": 0,
                "retry_count": 0,
                "retry_errors": [],
            },
            "selector_result": None,
        }

    def _selector_evidence_names(self, selector_input: dict[str, Any]) -> set[str]:
        """selector_input 안에서 matched_evidence로 허용할 evidence 이름 집합을 만든다."""

        names: set[str] = set()
        for group in selector_input.get("top_evidence", {}).values():
            for item in group:
                name = item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and name:
                    names.add(name)
        return names


class DecisionSelectorValidator:
    """selector 결과가 실제 profile 후보와 허용 enum 안에 있는지 검사한다."""

    def validate(
        self,
        selector_input: dict[str, Any],
        selector_result: dict[str, Any] | None,
        *,
        job_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """selector_result가 실제 후보 목록과 profile evidence를 벗어나지 않는지 검증한다."""

        errors: list[dict[str, str]] = []
        if not isinstance(selector_result, dict):
            errors.append({"code": "SELECTOR_RESULT_MISSING", "message": "Selector did not return a JSON object."})
            return self._result(errors)

        exec_ids = {item.get("exec_job_id") for item in selector_input.get("exec_jobs", [])}
        if selector_result.get("selected_exec_job_id") not in exec_ids:
            errors.append({"code": "INVALID_EXEC_JOB_ID", "message": "selected_exec_job_id is not in exec_jobs."})

        if selector_result.get("primary_task_type") not in set(selector_input.get("allowed_task_types", [])):
            errors.append({"code": "INVALID_TASK_TYPE", "message": "primary_task_type is not allowed."})

        selected_materials = selector_result.get("selected_material_types")
        allowed_materials = set(selector_input.get("allowed_material_types", []))
        if not isinstance(selected_materials, list):
            errors.append({"code": "INVALID_MATERIAL_TYPES", "message": "selected_material_types must be a list."})
        else:
            for material_type in selected_materials:
                if material_type not in allowed_materials:
                    errors.append({"code": "INVALID_MATERIAL_TYPE", "message": f"{material_type} is not allowed."})
            if len(selected_materials) != len(set(selected_materials)):
                errors.append({"code": "DUPLICATE_MATERIAL_TYPE", "message": "selected_material_types must be unique."})
            material_range = selector_input.get("difficulty", {}).get("material_count_range") or [2, 2]
            if not (int(material_range[0]) <= len(selected_materials) <= int(material_range[1])):
                errors.append({"code": "MATERIAL_COUNT_OUT_OF_RANGE", "message": "selected_material_types count is out of range."})

        if selector_result.get("mission_design_type") not in set(selector_input.get("allowed_mission_design_types", [])):
            errors.append({"code": "INVALID_MISSION_DESIGN_TYPE", "message": "mission_design_type is not allowed."})

        evidence_names = self._evidence_names(selector_input, job_profile)
        matched = selector_result.get("matched_evidence")
        if not isinstance(matched, list) or not matched:
            errors.append({"code": "MATCHED_EVIDENCE_MISSING", "message": "matched_evidence must be a non-empty list."})
        else:
            for name in matched:
                if name not in evidence_names:
                    errors.append({"code": "INVALID_MATCHED_EVIDENCE", "message": f"{name} is not a known evidence name."})
            if len(matched) != len(set(matched)):
                errors.append({"code": "DUPLICATE_MATCHED_EVIDENCE", "message": "matched_evidence must be unique."})

        if selector_result.get("confidence") not in CONFIDENCE_VALUES:
            errors.append({"code": "INVALID_CONFIDENCE", "message": "confidence is not allowed."})

        reason = selector_result.get("selection_reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append({"code": "SELECTION_REASON_MISSING", "message": "selection_reason is required."})

        return self._result(errors)

    def _result(self, errors: list[dict[str, str]]) -> dict[str, Any]:
        """selector validation 오류 목록을 passed/failed 결과 구조로 감싼다."""

        return {
            "schema_version": "decision_selector_validation.v1",
            "status": "failed" if errors else "passed",
            "errors": errors,
        }

    def _evidence_names(self, selector_input: dict[str, Any], job_profile: dict[str, Any] | None) -> set[str]:
        """selector_input과 원본 profile 양쪽에서 검증 가능한 evidence 이름을 모은다."""

        names: set[str] = set()
        for group in selector_input.get("top_evidence", {}).values():
            for item in group:
                name = item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and name:
                    names.add(name)
        if job_profile is not None:
            for group in job_profile.get("evidence", {}).values():
                for item in group:
                    name = item.get("name") if isinstance(item, dict) else None
                    if isinstance(name, str) and name:
                        names.add(name)
        return names
