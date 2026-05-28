# 기본 경로가 아닌 옵션용 코드다.
# PilotRunner에서 use_practice_sheet_background=False이거나 CLI에서 --mission-seed를 줄 때만 사용한다.

from __future__ import annotations

import re
from typing import Any


class MissionSeedBuilder:
    """구조화된 practice_profile을 이전 방식의 mission_seed 입력으로 축약한다."""

    def build(
        self,
        *,
        job_profile: dict[str, Any],
        practice_profile: dict[str, Any] | None,
        system_decisions: dict[str, Any],
    ) -> dict[str, Any] | None:
        """practice_profile이 있을 때 normal 난이도용 seed를 만든다."""

        if not practice_profile or system_decisions["difficulty"]["level"] != "normal":
            return None
        if not (practice_profile.get("profile_quality") or {}).get("ready_for_normal_mission", False):
            return None

        decision = self._select_decision(practice_profile)
        if decision is None:
            return None
        practice_tasks = self._select_practice_tasks(practice_profile, decision)
        collaboration = self._first(practice_profile.get("collaboration_contexts") or [])
        deliverable = self._first(practice_profile.get("deliverable_formats") or [])
        response_flow = (practice_profile.get("response_flow") or [])[:4]
        material_blueprints = self._material_blueprints(
            practice_profile=practice_profile,
            decision=decision,
            system_decisions=system_decisions,
        )
        if len(material_blueprints) < 2 or collaboration is None or deliverable is None or len(response_flow) < 3:
            return None

        request_example = self._first(practice_profile.get("request_examples") or [])
        scenario_basis = {
            "learner_role": self._learner_role(job_profile, practice_profile),
            "collaborator_role": collaboration.get("role", ""),
            "work_context": self._work_context(collaboration, decision),
            "request_sentence": request_example.get("text", "") if request_example else "",
            "goal": decision.get("decision_to_make", ""),
        }

        return {
            "schema_version": "mission_seed.normal.v1",
            "job_cd": practice_profile["job_identity"]["job_cd"],
            "difficulty": "normal",
            "selected_decision_situation_id": decision["id"],
            "selected_practice_task_ids": [item["id"] for item in practice_tasks],
            "selected_collaboration_context_id": collaboration["id"],
            "selected_request_example_id": request_example.get("id") if request_example else None,
            "selected_response_step_ids": [item["id"] for item in response_flow],
            "selected_deliverable_format_id": deliverable["id"],
            "scenario_basis": scenario_basis,
            "material_blueprints": material_blueprints,
            "task_plan": self._task_plan(material_blueprints, decision, deliverable),
            "guide_plan": self._guide_plan(response_flow),
            "evaluation_basis": self._evaluation_basis(decision, deliverable),
            "source_refs": self._source_refs(decision, practice_tasks, material_blueprints, collaboration, deliverable),
        }

    def excerpt(self, practice_profile: dict[str, Any], mission_seed: dict[str, Any]) -> dict[str, Any]:
        """LLM 입력에 필요한 practice_profile 일부만 seed 기준으로 잘라낸다."""

        decision = self._find_by_id(practice_profile.get("decision_situations") or [], mission_seed["selected_decision_situation_id"])
        task_ids = set(mission_seed.get("selected_practice_task_ids") or [])
        material_ids = {item["source_practice_material_id"] for item in mission_seed.get("material_blueprints") or []}
        response_step_ids = set(mission_seed.get("selected_response_step_ids") or [])
        deliverable_id = mission_seed.get("selected_deliverable_format_id")
        return {
            "job_identity": practice_profile.get("job_identity", {}),
            "selected_decision_situation": decision or {},
            "selected_practice_tasks": [
                item for item in practice_profile.get("practice_tasks", []) if item.get("id") in task_ids
            ],
            "selected_practice_materials": [
                item for item in practice_profile.get("practice_materials", []) if item.get("id") in material_ids
            ],
            "selected_collaboration_context": self._find_by_id(
                practice_profile.get("collaboration_contexts") or [],
                mission_seed.get("selected_collaboration_context_id"),
            )
            or {},
            "selected_request_example": self._find_by_id(
                practice_profile.get("request_examples") or [],
                mission_seed.get("selected_request_example_id"),
            ),
            "selected_response_flow": [
                item for item in practice_profile.get("response_flow", []) if item.get("id") in response_step_ids
            ],
            "selected_deliverable_format": self._find_by_id(
                practice_profile.get("deliverable_formats") or [],
                deliverable_id,
            )
            or {},
        }

    def _select_decision(self, practice_profile: dict[str, Any]) -> dict[str, Any] | None:
        """practice profile에서 normal 미션으로 만들 수 있는 결정 상황을 고른다."""

        decisions = practice_profile.get("decision_situations") or []
        valid = [
            item
            for item in decisions
            if item.get("decision_to_make") and len(item.get("factors") or []) >= 3 and item.get("good_vs_bad_branch")
        ]
        return self._first(valid)

    def _select_practice_tasks(
        self,
        practice_profile: dict[str, Any],
        decision: dict[str, Any],
    ) -> list[dict[str, Any]]:
        tasks = practice_profile.get("practice_tasks") or []
        scored = sorted(
            ((self._score(item, decision), index, item) for index, item in enumerate(tasks)),
            key=lambda item: (-item[0], item[1]),
        )
        selected = [item for score, _, item in scored if score > 0][:2]
        return selected or tasks[:1]

    def _material_blueprints(
        self,
        *,
        practice_profile: dict[str, Any],
        decision: dict[str, Any],
        system_decisions: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """practice_material을 LLM이 만들 learner-visible 자료 설계도로 바꾼다."""

        materials = practice_profile.get("practice_materials") or []
        min_count, max_count = system_decisions["difficulty"]["material_count_range"]
        target_count = max(2, min(min_count, max_count))
        allowed_types = system_decisions.get("allowed_material_types") or []
        scored = sorted(
            ((self._score(item, decision), index, item) for index, item in enumerate(materials)),
            key=lambda item: (-item[0], item[1]),
        )
        selected = [item for _, _, item in scored[:target_count]]
        blueprints = []
        used_types: set[str] = set()
        for index, material in enumerate(selected):
            material_type = self._material_type(material, allowed_types, used_types)
            used_types.add(material_type)
            role = "primary" if index == 0 else "supporting"
            blueprints.append(
                {
                    "source_practice_material_id": material["id"],
                    "learner_visible_material_type": material_type,
                    "material_role": role,
                    "material_name": material["name"],
                    "sample_generation_instruction": self._sample_instruction(material, material_type, role),
                    "source_refs": material.get("source_refs", []),
                }
            )
        return blueprints

    def _material_type(self, material: dict[str, Any], allowed_types: list[str], used_types: set[str]) -> str:
        text = f"{material.get('name', '')} {material.get('format', '')}"
        preferences: list[str] = []
        if any(keyword in text for keyword in ("리뷰", "클레임", "메모", "히스토리", "컨셉안")):
            preferences.extend(["memo", "card", "table"])
        if any(keyword in text for keyword in ("로그", "이벤트")):
            preferences.extend(["log", "table"])
        if any(keyword in text for keyword in ("KPI", "지표", "현황", "성과", "시장", "모객")):
            preferences.extend(["chart", "table"])
        if any(keyword in text for keyword in ("테이블", "자료", "데이터", "조사")):
            preferences.extend(["table", "chart"])
        preferences.extend(allowed_types)
        for material_type in preferences:
            if material_type in allowed_types and material_type not in used_types:
                return material_type
        for material_type in preferences:
            if material_type in allowed_types:
                return material_type
        return allowed_types[0]

    def _task_plan(
        self,
        material_blueprints: list[dict[str, Any]],
        decision: dict[str, Any],
        deliverable: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """mission_seed 기반 normal 미션의 task instruction 초안을 만든다."""

        primary = material_blueprints[0]["material_name"]
        supporting = material_blueprints[1]["material_name"]
        factors = (decision.get("factors") or [])[:3]
        return [
            {
                "id": "task_01",
                "instruction": f"{primary}에서 가장 먼저 확인해야 할 변화나 문제를 1~2개 고른다.",
                "expected_action": "observe",
                "material_roles": ["primary"],
            },
            {
                "id": "task_02",
                "instruction": f"{supporting}까지 함께 보고 {', '.join(factors[:2])} 기준으로 의미를 해석한다.",
                "expected_action": "interpret",
                "material_roles": ["primary", "supporting"],
            },
            {
                "id": "task_03",
                "instruction": f"{deliverable.get('name', '짧은 메모')} 형식으로 다음 행동 또는 제안을 정리한다.",
                "expected_action": "recommend",
                "material_roles": ["primary", "supporting"],
            },
        ]

    def _guide_plan(self, response_flow: list[dict[str, Any]]) -> list[str]:
        return [item.get("action", "") for item in response_flow[:4] if item.get("action")]

    def _evaluation_basis(self, decision: dict[str, Any], deliverable: dict[str, Any]) -> list[str]:
        factors = decision.get("factors") or []
        branch = decision.get("good_vs_bad_branch") or {}
        return [
            f"자료 근거를 사용해 {factors[0]}을 고려했는가" if factors else "자료 근거를 사용했는가",
            f"좋은 판단 기준({branch.get('good', '')})을 반영했는가",
            f"{deliverable.get('name', '산출물')}로 다음 행동을 정할 수 있게 정리했는가",
        ]

    def _learner_role(self, job_profile: dict[str, Any], practice_profile: dict[str, Any]) -> str:
        job_name = practice_profile["job_identity"].get("job_name") or job_profile["job_identity"].get("job_smcl_nm", "실무자")
        if "데이터" in job_name:
            return "주니어 데이터분석가"
        if "상품기획" in job_name:
            return "신입 상품기획자"
        return f"주니어 {job_name}"

    def _work_context(self, collaboration: dict[str, Any], decision: dict[str, Any]) -> str:
        role = collaboration.get("role", "협업자")
        context = decision.get("common_context", "실무 판단이 필요한 상황")
        return f"{role}와 함께 {context}"

    def _sample_instruction(self, material: dict[str, Any], material_type: str, role: str) -> str:
        role_text = "핵심 판단 축이 보이도록" if role == "primary" else "해석을 보완하도록"
        return (
            f"{material.get('name', '실무 자료')}를 바탕으로 {role_text} "
            f"{material_type} 자료를 생성한다. 실제 기업명이나 개인명은 사용하지 않는다."
        )

    def _source_refs(
        self,
        decision: dict[str, Any],
        practice_tasks: list[dict[str, Any]],
        material_blueprints: list[dict[str, Any]],
        collaboration: dict[str, Any],
        deliverable: dict[str, Any],
    ) -> list[str]:
        """seed가 어떤 practice profile 항목에서 왔는지 추적할 id 목록을 남긴다."""

        refs: list[str] = []
        for item in [decision, *practice_tasks, *material_blueprints, collaboration, deliverable]:
            for ref in item.get("source_refs") or []:
                if ref not in refs:
                    refs.append(ref)
        return refs

    def _score(self, item: dict[str, Any], decision: dict[str, Any]) -> int:
        haystack = self._tokens(" ".join(str(value) for value in item.values() if not isinstance(value, list)))
        needle_parts = [
            decision.get("decision_to_make", ""),
            decision.get("common_context", ""),
            " ".join(decision.get("factors") or []),
        ]
        needles = self._tokens(" ".join(needle_parts))
        return sum(1 for token in needles if token in haystack)

    def _tokens(self, text: str) -> set[str]:
        return {token for token in re.split(r"[\s·/(),.]+", text) if len(token) >= 2}

    def _first(self, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        return items[0] if items else None

    def _find_by_id(self, items: list[dict[str, Any]], item_id: str | None) -> dict[str, Any] | None:
        if not item_id:
            return None
        return next((item for item in items if item.get("id") == item_id), None)
