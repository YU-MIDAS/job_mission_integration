# LLM 출력이 따라야 할 mission_output 제약과 structured output schema를 만든다.

from __future__ import annotations

from typing import Any

from .config import EXCLUDED_MATERIAL_TYPES, MATERIAL_TYPES


class SchemaConstraintsBuilder:
    """LLM 출력이 따라야 할 mission_output 규칙과 strict JSON schema를 만든다."""

    def build(self, evidence_names: list[str] | None = None) -> dict[str, Any]:
        """prompt/validator가 공유하는 제약 조건 패키지를 구성한다."""

        evidence_names = evidence_names or []
        return {
            "schema_version": "schema_constraints.v1",
            "output_schema": "mission_output.v1",
            "material_detail_schema": "material_detail_schema.v1",
            "task_material_taxonomy": "task_and_material_taxonomy.v1",
            "difficulty_policy": "mission_difficulty_policy.v1",
            "json_only": True,
            "required_top_level_fields": [
                "schema_version",
                "mission_id",
                "job_identity",
                "target_exec_job",
                "mission_facts",
                "mission",
                "evaluation",
                "evidence_chain_draft",
                "reliability",
            ],
            "forbidden_fields": ["reliability.score", "reliability.passed", "evidence_chain"],
            "must_copy_from_system_decisions": [
                "target_exec_job",
                "mission.task_type",
                "mission.secondary_task_types",
                "mission.difficulty",
            ],
            "material_rules": {
                "allowed_material_types": sorted(MATERIAL_TYPES),
                "allowed_evidence_names": evidence_names,
                "use_only_allowed_material_types": True,
                "excluded_material_types": sorted(EXCLUDED_MATERIAL_TYPES),
                "factual_status_required": True,
                "mission_fact_refs_required": True,
                "evidence_source_required": True,
                "factual_status_value": "synthetic_mission_material",
            },
            "mission_fact_rules": {
                "must_create_before_materials": True,
                "all_material_refs_must_exist": True,
                "no_cross_material_conflict": True,
            },
            "repair_policy": {
                "max_repair_attempts": 1,
                "repair_only_validator_errors": True,
                "do_not_change_system_decisions": True,
            },
            "structured_output_schema": self.structured_output_schema(evidence_names=evidence_names),
        }

    def structured_output_schema(self, evidence_names: list[str] | None = None) -> dict[str, Any]:
        """OpenAI structured output에 전달할 strict mission_output schema를 반환한다."""

        evidence_names = evidence_names or []
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "mission_id",
                "job_identity",
                "target_exec_job",
                "mission_facts",
                "mission",
                "evaluation",
                "evidence_chain_draft",
                "reliability",
            ],
            "properties": {
                "schema_version": {"enum": ["mission_output.v1"]},
                "mission_id": {"enum": ["draft"]},
                "job_identity": self._job_identity_schema(),
                "target_exec_job": self._target_exec_job_schema(),
                "mission_facts": self._mission_facts_schema(),
                "mission": self._mission_schema(evidence_names),
                "evaluation": self._evaluation_schema(),
                "evidence_chain_draft": self._evidence_chain_draft_schema(),
                "reliability": self._object(
                    {
                        "status": {"enum": ["pending_validation"]},
                    }
                ),
            },
        }

    def _object(self, properties: dict[str, Any]) -> dict[str, Any]:
        """모든 필드를 required로 두는 strict object schema 조각을 만든다."""

        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        }

    def _string_array(self, enum_values: list[str] | None = None) -> dict[str, Any]:
        """문자열 배열 schema를 만들고, 필요하면 enum으로 값을 제한한다."""

        item_schema: dict[str, Any] = {"type": "string"}
        if enum_values:
            item_schema["enum"] = enum_values
        return {"type": "array", "items": item_schema}

    def _number_array(self) -> dict[str, Any]:
        """숫자 배열 schema 조각을 만든다."""

        return {"type": "array", "items": {"type": "number"}}

    def _source_ref_schema(self) -> dict[str, Any]:
        """XML 원천 위치를 표시하는 source_ref schema를 만든다."""

        return self._object(
            {
                "file": {"type": "string"},
                "field": {"type": "string"},
                "index": {"type": ["integer", "null"]},
            }
        )

    def _job_identity_schema(self) -> dict[str, Any]:
        """job_identity 필드의 structured output schema를 만든다."""

        return self._object(
            {
                "job_cd": {"type": "string"},
                "job_lrcl_nm": {"type": "string"},
                "job_mdcl_nm": {"type": "string"},
                "job_smcl_nm": {"type": "string"},
                "source_ref": self._source_ref_schema(),
            }
        )

    def _target_exec_job_schema(self) -> dict[str, Any]:
        """선택된 수행직무 target_exec_job schema를 만든다."""

        return self._object(
            {
                "exec_job_id": {"type": "string"},
                "text": {"type": "string"},
                "source_ref": self._source_ref_schema(),
                "selection_reason": {"type": "string"},
            }
        )

    def _mission_facts_schema(self) -> dict[str, Any]:
        """자료와 task가 공유해서 참조할 mission_facts schema를 만든다."""

        return self._object(
            {
                "org_name": {"type": "string"},
                "domain": {"type": "string"},
                "period": self._string_array(),
                "trend_pattern": {"type": "string"},
                "main_issue": {"type": "string"},
                "feedback_themes": self._string_array(),
                "decision_goal": {"type": "string"},
            }
        )

    def _difficulty_schema(self) -> dict[str, Any]:
        """난이도 정책이 LLM 출력에서 변형되지 않도록 difficulty schema를 만든다."""

        return self._object(
            {
                "level": {"enum": ["easy", "normal", "hard"]},
                "label": {"type": "string"},
                "estimated_time_minutes": {"type": "integer"},
                "material_bundle_style": {"type": "string"},
                "material_count_range": {"type": "array", "items": {"type": "integer"}},
                "task_count_range": {"type": "array", "items": {"type": "integer"}},
                "answer_length_hint": {"type": "string"},
                "requires_cross_material_reasoning": {"type": "boolean"},
                "requires_tradeoff_judgment": {"type": "boolean"},
                "requires_domain_expertise": {"type": "boolean"},
            }
        )

    def _mission_schema(self, evidence_names: list[str]) -> dict[str, Any]:
        """mission 본문 전체의 structured output schema를 만든다."""

        return self._object(
            {
                "title": {"type": "string"},
                "task_type": {"type": "string"},
                "secondary_task_types": self._string_array(),
                "difficulty": self._difficulty_schema(),
                "scenario": self._scenario_schema(),
                "materials": {"type": "array", "items": self._material_schema(evidence_names)},
                "tasks": {"type": "array", "items": self._task_schema()},
                "submission_format": self._submission_format_schema(),
            }
        )

    def _scenario_schema(self) -> dict[str, Any]:
        """role/context/goal/constraints/glossary로 구성된 scenario schema를 만든다."""

        return self._object(
            {
                "role": {"type": "string"},
                "context": {"type": "string"},
                "goal": {"type": "string"},
                "constraints": self._string_array(),
                "glossary": {
                    "type": "array",
                    "items": self._object(
                        {
                            "term": {"type": "string"},
                            "definition": {"type": "string"},
                        }
                    ),
                },
            }
        )

    def _material_schema(self, evidence_names: list[str]) -> dict[str, Any]:
        """자료 하나가 가져야 할 필드와 evidence_source enum schema를 만든다."""

        return self._object(
            {
                "material_id": {"type": "string"},
                "type": {"enum": sorted(MATERIAL_TYPES)},
                "subtype": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "factual_status": {"enum": ["synthetic_mission_material"]},
                "used_for": {"type": "string"},
                "evidence_source": self._string_array(evidence_names),
                "mission_fact_refs": self._string_array(),
                "data": self._material_data_schema(),
                "confidence": self._confidence_schema(),
            }
        )

    def _material_data_schema(self) -> dict[str, Any]:
        """chart/table/memo 등 모든 자료 유형이 공유하는 data payload schema를 만든다."""

        generic_item = self._object(
            {
                "text": {"type": "string"},
                "period": {"type": "string"},
                "task": {"type": "string"},
                "constraint": {"type": "string"},
                "label": {"type": "string"},
                "status": {"enum": ["checked", "unchecked", "issue"]},
                "importance": {"type": "string"},
            }
        )
        return self._object(
            {
                "chart_type": {"enum": ["line", "bar", "pie"]},
                "x_axis": self._object({"label": {"type": "string"}, "values": self._string_array()}),
                "y_axis": self._object({"label": {"type": "string"}, "unit": {"type": "string"}}),
                "series": {"type": "array", "items": self._object({"name": {"type": "string"}, "values": self._number_array()})},
                "columns": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": self._object(
                        {
                            "key": {"enum": ["option", "strength", "weakness", "priority"]},
                            "label": {"type": "string"},
                        }
                    ),
                },
                "rows": {
                    "type": "array",
                    "items": self._object(
                        {
                            "option": {"type": "string"},
                            "strength": {"type": "string"},
                            "weakness": {"type": "string"},
                            "priority": {"type": "integer"},
                        }
                    ),
                },
                "author": {"type": "string"},
                "items": {"type": "array", "items": generic_item},
                "thread": {
                    "type": "array",
                    "items": self._object(
                        {
                            "from": {"type": "string"},
                            "to": {"type": "string"},
                            "subject": {"type": "string"},
                            "body": {"type": "string"},
                        }
                    ),
                },
                "entries": {
                    "type": "array",
                    "items": self._object(
                        {
                            "time": {"type": "string"},
                            "actor": {"type": "string"},
                            "event": {"type": "string"},
                            "note": {"type": "string"},
                        }
                    ),
                },
                "cards": {
                    "type": "array",
                    "items": self._object(
                        {
                            "title": {"type": "string"},
                            "attributes": self._object(
                                {
                                    "strength": {"type": "string"},
                                    "weakness": {"type": "string"},
                                    "fit": {"type": "string"},
                                }
                            ),
                        }
                    ),
                },
            }
        )

    def _confidence_schema(self) -> dict[str, Any]:
        """LLM draft 단계의 synthetic material confidence schema를 만든다."""

        return self._object(
            {
                "score": {"type": "number"},
                "checks": self._object({"synthetic_material": {"type": "boolean"}}),
                "warnings": self._string_array(),
            }
        )

    def _task_schema(self) -> dict[str, Any]:
        """task instruction과 required_materials schema를 만든다."""

        return self._object(
            {
                "task_id": {"type": "string"},
                "instruction": {"type": "string"},
                "required_materials": self._string_array(),
                "expected_action": {"type": "string"},
            }
        )

    def _submission_format_schema(self) -> dict[str, Any]:
        """제출 형식과 길이 힌트 schema를 만든다."""

        return self._object(
            {
                "type": {"type": "string"},
                "estimated_time_minutes": {"type": "integer"},
                "required_sections": self._string_array(),
                "length_hint": {"type": "string"},
            }
        )

    def _evaluation_schema(self) -> dict[str, Any]:
        """expected_insights와 rubric으로 구성된 evaluation schema를 만든다."""

        return self._object(
            {
                "expected_insights": self._string_array(),
                "rubric": {"type": "array", "items": self._rubric_item_schema()},
            }
        )

    def _rubric_item_schema(self) -> dict[str, Any]:
        """rubric 항목 하나의 criterion/points/evidence schema를 만든다."""

        return self._object(
            {
                "criterion": {"type": "string"},
                "description": {"type": "string"},
                "points": {"type": "integer"},
                "linked_evidence": self._string_array(),
            }
        )

    def _evidence_chain_draft_schema(self) -> dict[str, Any]:
        """LLM이 임시로 남기는 evidence_chain_draft schema를 만든다."""

        return self._object(
            {
                "source_exec_job": self._object({"id": {"type": "string"}, "text": {"type": "string"}}),
                "linked_evidence": self._object(
                    {
                        "activities": self._string_array(),
                        "knowledge": self._string_array(),
                        "abilities": self._string_array(),
                    }
                ),
                "material_evidence_map": {
                    "type": "array",
                    "items": self._object(
                        {
                            "material_id": {"type": "string"},
                            "supported_by": self._string_array(),
                        }
                    ),
                },
            }
        )
