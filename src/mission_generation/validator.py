# LLM draft가 schema, system_decisions, evidence 규칙을 지키는지 검사한다.

from __future__ import annotations

import copy
import json
from typing import Any

from .config import EXCLUDED_MATERIAL_TYPES, MATERIAL_TYPES


AREA_BY_CODE = {
    "REQUIRED_FIELD_MISSING": "schema",
    "FORBIDDEN_FIELD_CREATED": "schema",
    "LLM_RELIABILITY_SCORE_CREATED": "schema",
    "MISSION_ID_NOT_DRAFT": "schema",
    "TARGET_EXEC_JOB_CHANGED": "system_decisions",
    "TASK_TYPE_CHANGED": "system_decisions",
    "SECONDARY_TASK_TYPES_CHANGED": "system_decisions",
    "DIFFICULTY_CHANGED": "system_decisions",
    "MATERIAL_TYPE_NOT_ALLOWED": "materials",
    "EXCLUDED_MATERIAL_USED": "materials",
    "MATERIAL_SCHEMA_INVALID": "materials",
    "MATERIAL_SIZE_TOO_SMALL": "materials",
    "MATERIAL_SIZE_TOO_LARGE": "materials",
    "MISSION_FACT_REF_NOT_FOUND": "mission_facts",
    "MISSION_FACT_REFS_WEAK": "mission_facts",
    "UNKNOWN_MATERIAL_REFERENCE": "materials",
    "TASK_COUNT_OUT_OF_RANGE": "materials",
    "MATERIAL_COUNT_OUT_OF_RANGE": "materials",
    "EVIDENCE_SOURCE_NOT_FOUND": "evidence",
    "RUBRIC_POINTS_NOT_100": "evaluation",
    "RUBRIC_LINK_WEAK": "evaluation",
    "SUBMISSION_FORMAT_TOO_OPEN": "evaluation",
    "TITLE_TOO_GENERIC": "materials",
    "DESCRIPTION_TOO_SHORT": "materials",
}


class MissionValidator:
    """LLM draft가 system decisions와 profile evidence를 지키는지 최종 저장 전에 검사한다."""

    def validate(
        self,
        *,
        job_profile: dict[str, Any],
        system_decisions: dict[str, Any],
        mission_output_draft: dict[str, Any] | str | None,
        attempt: int = 0,
    ) -> dict[str, Any]:
        """draft를 검사하고 저장용 evidence_chain/reliability 계산 결과를 반환한다."""

        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        checks: dict[str, Any] = {
            "json_parse": {},
            "schema": {},
            "system_decisions": {},
            "mission_facts": {},
            "materials": {},
            "tasks": {},
            "evaluation": {},
            "evidence": {},
            "difficulty": {},
        }

        draft = self._coerce_json(mission_output_draft, errors, checks)
        if draft is None:
            return self._result(attempt, errors, warnings, checks, {}, {})

        self._validate_schema(draft, errors, warnings, checks)
        self._validate_system_decisions(draft, system_decisions, errors, checks)
        self._validate_mission_facts(draft, errors, warnings, checks)
        self._validate_materials(draft, job_profile, system_decisions, errors, warnings, checks)
        self._validate_tasks(draft, system_decisions, errors, warnings, checks)
        self._validate_evaluation(draft, errors, warnings, checks)
        final_evidence_chain = self._build_evidence_chain(draft, job_profile, system_decisions, errors, checks)
        reliability = self._calculate_reliability(errors, warnings)
        return self._result(attempt, errors, warnings, checks, final_evidence_chain, reliability)

    def _coerce_json(
        self,
        draft: dict[str, Any] | str | None,
        errors: list[dict[str, Any]],
        checks: dict[str, Any],
    ) -> dict[str, Any] | None:
        """dict 또는 JSON 문자열 draft를 검증 가능한 dict로 맞춘다."""

        if isinstance(draft, dict):
            checks["json_parse"] = {"passed": True}
            return draft
        if isinstance(draft, str):
            try:
                parsed = json.loads(draft)
                checks["json_parse"] = {"passed": True}
                return parsed
            except json.JSONDecodeError:
                self._add(
                    errors,
                    "JSON_PARSE_FAILED",
                    "fail",
                    "$",
                    "mission_output_draft is not valid JSON.",
                    "Return a JSON object only.",
                )
                checks["json_parse"] = {"passed": False}
                return None
        self._add(errors, "JSON_PARSE_FAILED", "fail", "$", "mission_output_draft is empty.", "Return a JSON object.")
        checks["json_parse"] = {"passed": False}
        return None

    def _validate_schema(
        self,
        draft: dict[str, Any],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        checks: dict[str, Any],
    ) -> None:
        """최상위 필드와 learner-facing scenario 구조가 필수 조건을 만족하는지 본다."""

        required = [
            "schema_version",
            "mission_id",
            "job_identity",
            "target_exec_job",
            "mission_facts",
            "mission",
            "evaluation",
            "evidence_chain_draft",
            "reliability",
        ]
        for field in required:
            if field not in draft:
                self._add(errors, "REQUIRED_FIELD_MISSING", "fail", field, f"{field} is required.", f"Add {field}.")
        if draft.get("schema_version") != "mission_output.v1":
            self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "schema_version", "schema_version must be mission_output.v1.", "Set schema_version.")
        if draft.get("mission_id") != "draft":
            self._add(errors, "MISSION_ID_NOT_DRAFT", "fail", "mission_id", "LLM draft mission_id must be draft.", "Set mission_id to draft.")
        if "evidence_chain" in draft:
            self._add(errors, "FORBIDDEN_FIELD_CREATED", "fail", "evidence_chain", "LLM must not create final evidence_chain.", "Remove evidence_chain.")
        reliability = draft.get("reliability")
        if not isinstance(reliability, dict):
            self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "reliability", "reliability must be an object.", "Set reliability.status.")
        else:
            if "score" in reliability or "passed" in reliability:
                self._add(
                    errors,
                    "LLM_RELIABILITY_SCORE_CREATED",
                    "fail",
                    "reliability",
                    "LLM must not create reliability.score or reliability.passed.",
                    "Use only {\"status\":\"pending_validation\"}.",
                )
            if reliability.get("status") != "pending_validation":
                self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "reliability.status", "reliability.status must be pending_validation.", "Set status.")
        mission = draft.get("mission")
        if not isinstance(mission, dict):
            self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "mission", "mission must be an object.", "Add mission object.")
        else:
            for field in ["title", "task_type", "secondary_task_types", "difficulty", "scenario", "materials", "tasks", "submission_format"]:
                if field not in mission:
                    self._add(errors, "REQUIRED_FIELD_MISSING", "fail", f"mission.{field}", f"mission.{field} is required.", "Add the field.")
            if len(str(mission.get("title", "")).strip()) <= 3:
                self._add(warnings, "TITLE_TOO_GENERIC", "warning", "mission.title", "Mission title is too generic.", "Use a more specific title.")
            scenario = mission.get("scenario")
            if isinstance(scenario, dict):
                for field in ["role", "context", "goal", "constraints", "glossary"]:
                    if field not in scenario:
                        self._add(errors, "REQUIRED_FIELD_MISSING", "fail", f"mission.scenario.{field}", f"scenario.{field} is required.", "Add the field.")
                glossary = scenario.get("glossary")
                if not isinstance(glossary, list):
                    self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "mission.scenario.glossary", "scenario.glossary must be an array.", "Use [] when no glossary is needed.")
                else:
                    for index, item in enumerate(glossary):
                        if not isinstance(item, dict) or not item.get("term") or not item.get("definition"):
                            self._add(
                                errors,
                                "GLOSSARY_ITEM_INVALID",
                                "fail",
                                f"mission.scenario.glossary[{index}]",
                                "Each glossary item must include term and definition.",
                                "Add term and definition, or remove the invalid item.",
                            )
        checks["schema"] = {"required_checked": True}

    def _validate_system_decisions(
        self,
        draft: dict[str, Any],
        decisions: dict[str, Any],
        errors: list[dict[str, Any]],
        checks: dict[str, Any],
    ) -> None:
        """LLM이 selector/system_decisions로 고정한 핵심 결정을 바꾸지 않았는지 확인한다."""

        mission = draft.get("mission") or {}
        selected = decisions["selected_exec_job"]
        target = draft.get("target_exec_job") or {}
        if target.get("exec_job_id") != selected.get("exec_job_id") or target.get("text") != selected.get("text"):
            self._add(errors, "TARGET_EXEC_JOB_CHANGED", "fail", "target_exec_job", "target_exec_job differs from system_decisions.", "Copy selected_exec_job exactly.")
        if mission.get("task_type") != decisions.get("primary_task_type"):
            self._add(errors, "TASK_TYPE_CHANGED", "fail", "mission.task_type", "task_type differs from system_decisions.", "Copy primary_task_type.")
        if mission.get("secondary_task_types") != decisions.get("secondary_task_types"):
            self._add(errors, "SECONDARY_TASK_TYPES_CHANGED", "fail", "mission.secondary_task_types", "secondary_task_types differ.", "Copy secondary_task_types.")
        if mission.get("difficulty") != decisions.get("difficulty"):
            self._add(errors, "DIFFICULTY_CHANGED", "fail", "mission.difficulty", "difficulty differs from system_decisions.", "Copy difficulty.")
        checks["system_decisions"] = {"aligned": not any(e["code"].endswith("CHANGED") for e in errors)}

    def _validate_mission_facts(
        self,
        draft: dict[str, Any],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        checks: dict[str, Any],
    ) -> None:
        """materials가 참조할 공통 mission_facts가 충분히 만들어졌는지 확인한다."""

        facts = draft.get("mission_facts")
        if not isinstance(facts, dict) or not facts:
            self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "mission_facts", "mission_facts must be a non-empty object.", "Add mission_facts.")
            return
        if len(facts) < 4:
            self._add(warnings, "MISSION_FACT_REFS_WEAK", "warning", "mission_facts", "mission_facts has few keys.", "Add shared facts for materials.")
        checks["mission_facts"] = {"fact_key_count": len(facts)}

    def _validate_materials(
        self,
        draft: dict[str, Any],
        profile: dict[str, Any],
        decisions: dict[str, Any],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        checks: dict[str, Any],
    ) -> None:
        """자료 개수, 허용 유형, fact/evidence 연결, task 사용 여부를 검사한다."""

        mission = draft.get("mission") or {}
        materials = mission.get("materials")
        tasks = mission.get("tasks") if isinstance(mission.get("tasks"), list) else []
        if not isinstance(materials, list) or not materials:
            self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "mission.materials", "materials must be a non-empty list.", "Add materials.")
            return
        min_count, max_count = decisions["difficulty"]["material_count_range"]
        if len(materials) < min_count:
            self._add(errors, "MATERIAL_COUNT_OUT_OF_RANGE", "fail", "mission.materials", "Too few materials for difficulty.", f"Use exactly {min_count}.")
        elif len(materials) > max_count:
            self._add(errors, "MATERIAL_COUNT_OUT_OF_RANGE", "fail", "mission.materials", "Too many materials for difficulty.", f"Use exactly {max_count}.")

        fact_keys = set((draft.get("mission_facts") or {}).keys())
        allowed = set(decisions.get("allowed_material_types", []))
        evidence_names = self._evidence_name_map(profile)
        used_material_ids: set[str] = set()
        for task in tasks:
            if isinstance(task, dict):
                used_material_ids.update(task.get("required_materials") or [])
        material_ids = set()

        for index, material in enumerate(materials):
            path = f"mission.materials[{index}]"
            if not isinstance(material, dict):
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", path, "material must be an object.", "Use object material.")
                continue
            material_id = material.get("material_id")
            material_type = material.get("type")
            if material_id:
                material_ids.add(material_id)
            for field in ["material_id", "type", "subtype", "title", "description", "factual_status", "used_for", "evidence_source", "mission_fact_refs", "data"]:
                if field not in material:
                    self._add(errors, "REQUIRED_FIELD_MISSING", "fail", f"{path}.{field}", f"{field} is required.", f"Add {field}.")
            if material_type in EXCLUDED_MATERIAL_TYPES:
                self._add(errors, "EXCLUDED_MATERIAL_USED", "fail", f"{path}.type", f"{material_type} is excluded in v1.", "Use structured materials only.")
            elif material_type not in MATERIAL_TYPES or material_type not in allowed:
                self._add(errors, "MATERIAL_TYPE_NOT_ALLOWED", "fail", f"{path}.type", f"{material_type} is not allowed.", "Use allowed_material_types only.")
            if material.get("factual_status") != "synthetic_mission_material":
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.factual_status", "factual_status must be synthetic_mission_material.", "Set factual_status.")
            if len(str(material.get("description", "")).strip()) < 8:
                self._add(warnings, "DESCRIPTION_TOO_SHORT", "warning", f"{path}.description", "description is short.", "Add a clearer description.")
            if str(material.get("title", "")).strip() in {"자료", "자료 1", "material"}:
                self._add(warnings, "TITLE_TOO_GENERIC", "warning", f"{path}.title", "title is too generic.", "Use a specific title.")

            refs = material.get("mission_fact_refs")
            if not isinstance(refs, list) or not refs:
                self._add(errors, "MISSION_FACT_REF_NOT_FOUND", "fail", f"{path}.mission_fact_refs", "mission_fact_refs is required.", "Reference existing mission_facts keys.")
            else:
                missing_refs = [ref for ref in refs if ref not in fact_keys]
                if missing_refs:
                    self._add(errors, "MISSION_FACT_REF_NOT_FOUND", "fail", f"{path}.mission_fact_refs", f"Unknown fact refs: {missing_refs}", "Use existing mission_facts keys.")
            evidence_source = material.get("evidence_source")
            if not isinstance(evidence_source, list) or not evidence_source:
                self._add(errors, "EVIDENCE_SOURCE_NOT_FOUND", "fail", f"{path}.evidence_source", "evidence_source is required.", "Use profile evidence names.")
            else:
                missing = [name for name in evidence_source if name not in evidence_names]
                if missing:
                    self._add(errors, "EVIDENCE_SOURCE_NOT_FOUND", "fail", f"{path}.evidence_source", f"Evidence not found: {missing}", "Use job_profile evidence names.")
            if material_id and material_id not in used_material_ids:
                self._add(errors, "UNKNOWN_MATERIAL_REFERENCE", "fail", f"{path}.material_id", "material is not referenced by any task.", "Reference it from at least one task.")
            self._validate_material_data(material, path, decisions["difficulty"]["level"], errors, warnings)

        checks["materials"] = {"count": len(materials), "material_ids": sorted(material_ids)}

    def _validate_material_data(
        self,
        material: dict[str, Any],
        path: str,
        difficulty: str,
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> None:
        """자료 유형별 data payload가 학습자 화면에서 렌더링 가능한 최소 구조인지 본다."""

        material_type = material.get("type")
        data = material.get("data")
        if not isinstance(data, dict):
            self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data", "data must be an object.", "Add data object.")
            return
        # 자료별 필수 구조와 크기 제한은 prompt의 material size rules와 같은 기준이다.
        if material_type == "chart":
            self._validate_chart(data, path, errors)
            self._check_size(len((data.get("x_axis") or {}).get("values") or []), *self._size_bounds(difficulty, (3, 4), (4, 5), (4, 5)), f"{path}.data.x_axis.values", errors, warnings)
        elif material_type == "table":
            rows = data.get("rows")
            columns = data.get("columns")
            if not isinstance(columns, list) or len(columns) < 2 or not isinstance(rows, list) or not rows:
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data", "table requires columns and rows.", "Add columns and rows.")
            else:
                keys = {column.get("key") for column in columns}
                for row in rows:
                    if set(row.keys()) != keys:
                        self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.rows", "row keys must match columns.", "Align row keys with columns.")
                    if any(not isinstance(value, (str, int, float)) for value in row.values()):
                        self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.rows", "table cells must be string or number.", "Use string or number cells.")
            self._check_size(len(rows or []), 1, {"easy": 3, "normal": 4, "hard": 4}.get(difficulty, 4), f"{path}.data.rows", errors, warnings)
        elif material_type == "memo":
            items = data.get("items")
            if not isinstance(items, list) or not items:
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.items", "memo items are required.", "Add items.")
            self._check_size(len(items or []), *self._size_bounds(difficulty, (1, 2), (2, 3), (2, 3)), f"{path}.data.items", errors, warnings)
        elif material_type == "email":
            thread = data.get("thread")
            if not isinstance(thread, list) or not thread:
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.thread", "email thread is required.", "Add thread.")
            else:
                for item in thread:
                    if not all(item.get(field) for field in ("from", "to", "subject", "body")):
                        self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.thread", "email requires from/to/subject/body.", "Add email fields.")
            self._check_size(len(thread or []), 1, 1, f"{path}.data.thread", errors, warnings)
        elif material_type == "schedule":
            items = data.get("items")
            if not isinstance(items, list) or not items or any(not item.get("period") or not item.get("task") for item in items):
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.items", "schedule items require period and task.", "Add period and task.")
            self._check_size(len(items or []), *self._size_bounds(difficulty, (1, 2), (2, 3), (2, 3)), f"{path}.data.items", errors, warnings)
        elif material_type == "checklist":
            items = data.get("items")
            allowed_status = {"checked", "unchecked", "issue"}
            if not isinstance(items, list) or not items:
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.items", "checklist items are required.", "Add items.")
            else:
                for item in items:
                    if item.get("status") not in allowed_status:
                        self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.items.status", "invalid checklist status.", "Use checked, unchecked, or issue.")
            self._check_size(len(items or []), *self._size_bounds(difficulty, (2, 2), (3, 3), (3, 3)), f"{path}.data.items", errors, warnings)
        elif material_type == "log":
            entries = data.get("entries")
            if not isinstance(entries, list) or not entries or any(not item.get("time") or not item.get("event") for item in entries):
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.entries", "log entries require time and event.", "Add time and event.")
            self._check_size(len(entries or []), *self._size_bounds(difficulty, (2, 3), (3, 4), (3, 4)), f"{path}.data.entries", errors, warnings)
        elif material_type == "card":
            cards = data.get("cards")
            if not isinstance(cards, list) or not cards:
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.cards", "cards are required.", "Add cards.")
            else:
                key_sets = [set((card.get("attributes") or {}).keys()) for card in cards]
                if any(not key_set for key_set in key_sets) or len({tuple(sorted(key_set)) for key_set in key_sets}) > 1:
                    self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.cards", "card attribute keys must be consistent.", "Use consistent attributes.")
            self._check_size(len(cards or []), *self._size_bounds(difficulty, (2, 2), (2, 3), (2, 3)), f"{path}.data.cards", errors, warnings)

    def _validate_chart(self, data: dict[str, Any], path: str, errors: list[dict[str, Any]]) -> None:
        """chart data의 축, series 길이, 숫자값, pie 합계를 검사한다."""

        chart_type = data.get("chart_type")
        if chart_type not in {"line", "bar", "pie"}:
            self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.chart_type", "chart_type is invalid.", "Use line, bar, or pie.")
        x_values = (data.get("x_axis") or {}).get("values")
        series = data.get("series")
        if not isinstance(x_values, list) or not isinstance(series, list) or not series:
            self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data", "chart requires x_axis.values and series.", "Add chart values.")
            return
        if len(series) > 2:
            self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.series", "chart series max is 2.", "Reduce series.")
        for item in series:
            values = item.get("values")
            if not isinstance(values, list) or len(values) != len(x_values):
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.series.values", "series values must match x values.", "Match lengths.")
            elif any(not isinstance(value, (int, float)) for value in values):
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.series.values", "series values must be numeric.", "Use numbers.")
            if chart_type == "pie" and sum(values or []) != 100:
                self._add(errors, "MATERIAL_SCHEMA_INVALID", "fail", f"{path}.data.series.values", "pie values must sum to 100.", "Adjust values.")

    def _validate_tasks(
        self,
        draft: dict[str, Any],
        decisions: dict[str, Any],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        checks: dict[str, Any],
    ) -> None:
        """task 개수와 required_materials 참조가 난이도 정책과 자료 목록에 맞는지 확인한다."""

        mission = draft.get("mission") or {}
        tasks = mission.get("tasks")
        materials = mission.get("materials") if isinstance(mission.get("materials"), list) else []
        material_ids = {material.get("material_id") for material in materials if isinstance(material, dict)}
        if not isinstance(tasks, list) or not tasks:
            self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "mission.tasks", "tasks must be a non-empty list.", "Add tasks.")
            return
        min_count, max_count = decisions["difficulty"]["task_count_range"]
        if len(tasks) < min_count or len(tasks) > max_count:
            self._add(errors, "TASK_COUNT_OUT_OF_RANGE", "fail", "mission.tasks", "task count is outside difficulty range.", f"Use {min_count}-{max_count} tasks.")
        for index, task in enumerate(tasks):
            path = f"mission.tasks[{index}]"
            if not isinstance(task, dict):
                self._add(errors, "REQUIRED_FIELD_MISSING", "fail", path, "task must be an object.", "Use object task.")
                continue
            for field in ["task_id", "instruction", "required_materials", "expected_action"]:
                if field not in task:
                    self._add(errors, "REQUIRED_FIELD_MISSING", "fail", f"{path}.{field}", f"{field} is required.", f"Add {field}.")
            required_materials = task.get("required_materials")
            if not isinstance(required_materials, list) or not required_materials:
                self._add(errors, "UNKNOWN_MATERIAL_REFERENCE", "fail", f"{path}.required_materials", "required_materials is required.", "Use existing material ids.")
            else:
                missing = [material_id for material_id in required_materials if material_id not in material_ids]
                if missing:
                    self._add(errors, "UNKNOWN_MATERIAL_REFERENCE", "fail", f"{path}.required_materials", f"Unknown material ids: {missing}", "Use existing material ids.")
        submission = mission.get("submission_format") or {}
        if not isinstance(submission, dict) or not submission.get("required_sections") or not submission.get("length_hint"):
            self._add(warnings, "SUBMISSION_FORMAT_TOO_OPEN", "warning", "mission.submission_format", "submission format is broad.", "Add sections and length_hint.")
        checks["tasks"] = {"count": len(tasks)}

    def _validate_evaluation(
        self,
        draft: dict[str, Any],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        checks: dict[str, Any],
    ) -> None:
        """rubric 점수 합계와 평가 기준의 기본 완결성을 확인한다."""

        evaluation = draft.get("evaluation")
        if not isinstance(evaluation, dict):
            self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "evaluation", "evaluation is required.", "Add evaluation.")
            return
        rubric = evaluation.get("rubric")
        if not isinstance(rubric, list) or not rubric:
            self._add(errors, "REQUIRED_FIELD_MISSING", "fail", "evaluation.rubric", "rubric is required.", "Add rubric.")
            return
        total = 0
        for index, item in enumerate(rubric):
            if not isinstance(item, dict) or not item.get("criterion") or not item.get("description") or "points" not in item:
                self._add(errors, "REQUIRED_FIELD_MISSING", "fail", f"evaluation.rubric[{index}]", "rubric item is incomplete.", "Add criterion, description, points.")
                continue
            total += item.get("points") if isinstance(item.get("points"), int) else 0
            if not item.get("linked_evidence"):
                self._add(warnings, "RUBRIC_LINK_WEAK", "warning", f"evaluation.rubric[{index}].linked_evidence", "rubric has weak evidence link.", "Add linked_evidence.")
        if total != 100:
            self._add(warnings, "RUBRIC_POINTS_NOT_100", "warning", "evaluation.rubric", f"rubric points sum is {total}.", "Adjust points to 100.")
        checks["evaluation"] = {"rubric_points": total}

    def _build_evidence_chain(
        self,
        draft: dict[str, Any],
        profile: dict[str, Any],
        decisions: dict[str, Any],
        errors: list[dict[str, Any]],
        checks: dict[str, Any],
    ) -> dict[str, Any]:
        """자료의 evidence_source 이름을 실제 profile source_ref로 다시 연결한다."""

        # draft의 evidence_source 이름을 실제 job_profile evidence에 대조해 source_ref를 다시 만든다.
        # 이 값만 final_assembler가 최종 evidence_chain으로 채택한다.
        evidence_by_name = self._evidence_name_map(profile)
        materials = (draft.get("mission") or {}).get("materials") or []
        items = []
        material_map = []
        for material in materials:
            if not isinstance(material, dict):
                continue
            source_refs = []
            supported_by = []
            for name in material.get("evidence_source") or []:
                evidence = evidence_by_name.get(name)
                if evidence:
                    ref = copy.deepcopy(evidence["source_ref"])
                    ref["source_root"] = profile.get("source_root", "data/api_raw")
                    source_refs.append(ref)
                    supported_by.append(name)
            if not source_refs and material.get("material_id"):
                self._add(errors, "SOURCE_REF_NOT_FOUND", "fail", f"evidence_chain.{material.get('material_id')}", "No source_ref can be built.", "Use profile evidence names.")
            items.append(
                {
                    "target_type": "material",
                    "target_id": material.get("material_id"),
                    "mission_fact_refs": material.get("mission_fact_refs", []),
                    "source_refs": source_refs,
                    "connection_reason": "직업 profile evidence와 생성 자료가 간접 연결됨",
                    "traceability": "indirect" if source_refs else "weak",
                }
            )
            material_map.append({"material_id": material.get("material_id"), "supported_by": supported_by})

        chain = {
            "created_by": "validator.v1",
            "source_exec_job": copy.deepcopy(decisions["selected_exec_job"]),
            "linked_evidence": {
                "activities": profile.get("evidence", {}).get("work_activities", [])[:5],
                "abilities": profile.get("evidence", {}).get("abilities", [])[:3],
                "knowledge": profile.get("evidence", {}).get("knowledge", [])[:3],
                "work_environment": profile.get("evidence", {}).get("work_environment", [])[:3],
            },
            "material_evidence_map": material_map,
            "items": items,
        }
        checks["evidence"] = {"final_evidence_chain_created": True, "item_count": len(items)}
        return chain

    def _calculate_reliability(
        self,
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """validator 오류/경고 수를 최종 reliability score로 환산한다."""

        areas = {
            "schema": {"max": 20, "earned": 20},
            "system_decisions": {"max": 15, "earned": 15},
            "materials": {"max": 20, "earned": 20},
            "mission_facts": {"max": 15, "earned": 15},
            "evidence": {"max": 20, "earned": 20},
            "evaluation": {"max": 10, "earned": 10},
            "tasks": {"max": 0, "earned": 0},
        }
        for item in errors:
            area = AREA_BY_CODE.get(item["code"], "schema")
            if area in areas:
                areas[area]["earned"] = max(0, min(areas[area]["earned"], areas[area]["max"] // 2) - 2)
        for item in warnings:
            area = AREA_BY_CODE.get(item["code"], "materials")
            if area in areas:
                areas[area]["earned"] = max(0, areas[area]["earned"] - 2)
        raw_score = sum(area["earned"] for area in areas.values())
        return {
            "score_breakdown": {key: value for key, value in areas.items() if value["max"] > 0},
            "raw_score": raw_score,
            "score": round(raw_score / 100, 2),
            "passed": not errors and raw_score >= 75,
            "calculated_by": "validator.v1",
            "human_review_required": True,
        }

    def _result(
        self,
        attempt: int,
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        checks: dict[str, Any],
        final_evidence_chain: dict[str, Any],
        reliability: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """검증 상태, 오류, 경고, evidence_chain, reliability를 표준 결과로 묶는다."""

        reliability = reliability or self._calculate_reliability(errors, warnings)
        if errors and attempt >= 1:
            status = "discard"
        elif errors:
            status = "repair_required"
        elif reliability["score"] < 0.75 and attempt >= 1:
            status = "discard"
        elif reliability["score"] < 0.75:
            status = "repair_required"
        elif len(warnings) >= 3 and attempt == 0:
            status = "repair_required"
        else:
            status = "pass"
        return {
            "schema_version": "validator_result.v1",
            "attempt": attempt,
            "status": status,
            "passed": status == "pass",
            "repairable": status == "repair_required",
            "discarded": status == "discard",
            "reliability": {
                **reliability,
                "warning_count": len(warnings),
                "fail_count": len(errors),
            },
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
            "final_evidence_chain": final_evidence_chain,
        }

    def _check_size(
        self,
        count: int,
        min_count: int,
        max_count: int,
        path: str,
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> None:
        """자료 항목 수가 난이도별 최소/최대 범위 안에 있는지 확인한다."""

        if count < min_count:
            self._add(errors, "MATERIAL_SIZE_TOO_SMALL", "fail", path, f"count {count} is below minimum {min_count}.", "Add items.")
        elif count > max_count:
            self._add(errors, "MATERIAL_SIZE_TOO_LARGE", "fail", path, f"count {count} exceeds max {max_count}.", "Reduce items.")

    def _size_bounds(
        self,
        difficulty: str,
        easy: tuple[int, int],
        normal: tuple[int, int],
        hard: tuple[int, int],
    ) -> tuple[int, int]:
        """난이도 코드에 맞는 자료 크기 제한을 고른다."""

        return {"easy": easy, "normal": normal, "hard": hard}.get(difficulty, normal)

    def _evidence_name_map(self, profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """profile evidence를 이름으로 빠르게 찾을 수 있게 dict로 색인한다."""

        return {
            item["name"]: item
            for group in profile.get("evidence", {}).values()
            for item in group
            if isinstance(item, dict) and item.get("name")
        }

    def _add(
        self,
        target: list[dict[str, Any]],
        code: str,
        severity: str,
        path: str,
        message: str,
        required_fix: str,
    ) -> None:
        """validator 오류/경고 항목을 공통 구조로 추가한다."""

        target.append(
            {
                "path": path,
                "severity": severity,
                "code": code,
                "message": message,
                "required_fix": required_fix,
                "repairable": code not in {"REPAIR_CHANGED_SYSTEM_DECISION", "REPAIR_ATTEMPT_EXCEEDED"},
            }
        )
