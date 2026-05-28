# validator를 통과한 미션 draft를 최종 mission_output.json 구조로 조립한다.

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

# axis_mapping.json 경로 (프로젝트 루트 기준)
_AXIS_MAPPING_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "axis_mapping.json"

# axis_mapping에 없는 KNOW 항목에 대한 확장 매핑 (abilities, knowledge 등)
_EXTENDED_AXIS_MAPPING: dict[str, str] = {
    "논리적 분석": "AX1", "수리력": "AX1", "추리력": "AX1", "읽고 이해하기": "AX1",
    "쓰기": "AX1", "기술 분석": "AX1", "산수와 수학": "AX1", "경제와 회계": "AX1",
    "과학": "AX1", "물리": "AX1", "화학": "AX1", "사무": "AX1",
    "사물, 서비스, 사람의 질 판단": "AX2", "공학과 기술": "AX2", "생산과 공정": "AX2",
    "품질 관리 분석": "AX2",
    "판단과 의사결정": "AX3", "복잡한 문제 해결": "AX3", "시스템 평가": "AX3",
    "시스템 분석": "AX3",
    "경영 및 행정": "AX4", "재정 관리": "AX4", "인사 관리": "AX4", "자원 배분": "AX4",
    "설득": "AX5", "서비스 지향성": "AX5", "사회적 인지": "AX5", "타인 모니터링": "AX5",
    "사람들을 훈련, 교육": "AX5", "조직 외부인과 소통": "AX5",
}

_AXES = ["AX1", "AX2", "AX3", "AX4", "AX5"]


def _load_item_to_axis() -> dict[str, str]:
    item_to_axis: dict[str, str] = {}
    try:
        with open(_AXIS_MAPPING_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        for ax_code, ax_info in raw.items():
            for item in ax_info.get("items", []):
                item_to_axis[item] = ax_code
    except Exception:
        pass
    item_to_axis.update(_EXTENDED_AXIS_MAPPING)
    return item_to_axis


def _compute_axis_signals(rubric: list[dict[str, Any]]) -> dict[str, float]:
    """
    evaluation.rubric의 linked_evidence 항목들을 5축으로 매핑하고
    각 기준의 points를 linked_evidence 항목 수로 균등 배분해 정규화한다.
    """
    item_to_axis = _load_item_to_axis()
    ax_score: dict[str, float] = {ax: 0.0 for ax in _AXES}

    for criterion in rubric:
        points = float(criterion.get("points", 0))
        evidence_items = criterion.get("linked_evidence", [])
        if not evidence_items or points <= 0:
            continue
        per_item = points / len(evidence_items)
        for item in evidence_items:
            ax = item_to_axis.get(item)
            if ax in ax_score:
                ax_score[ax] += per_item
            else:
                split = per_item / len(_AXES)
                for a in _AXES:
                    ax_score[a] += split

    total = sum(ax_score.values())
    if total <= 0:
        return {ax: round(1.0 / len(_AXES), 4) for ax in _AXES}
    return {ax: round(ax_score[ax] / total, 4) for ax in _AXES}


class FinalMissionAssembler:
    """검증을 통과한 draft를 공개 가능한 최종 mission_output으로 정리한다."""

    def assemble(
        self,
        *,
        mission_output_draft: dict[str, Any],
        validator_result: dict[str, Any],
        job_cd: str,
        difficulty_code: str,
        repair_count: int,
        sequence: int = 1,
    ) -> dict[str, Any]:
        """draft mission_id와 evidence/reliability를 최종 저장 형식으로 교체한다."""

        if not validator_result.get("passed"):
            raise ValueError("validator_result must pass before final assembly")
        final_output = copy.deepcopy(mission_output_draft)
        final_output["schema_version"] = "mission_output.v1"
        final_output["mission_id"] = f"mission_{job_cd}_{difficulty_code}_{sequence:03d}"
        # LLM이 만든 임시 근거 추적값은 신뢰하지 않고, validator가 profile evidence로 재구성한 chain만 남긴다.
        final_output.pop("evidence_chain_draft", None)
        final_output.pop("evidence_chain", None)
        final_output["evidence_chain"] = copy.deepcopy(validator_result["final_evidence_chain"])
        reliability = validator_result["reliability"]
        final_output["reliability"] = {
            "score": reliability["score"],
            "raw_score": reliability["raw_score"],
            "passed": True,
            "calculated_by": "validator.v1",
            "human_review_required": True,
            "warning_count": reliability["warning_count"],
            "fail_count": reliability["fail_count"],
            "repair_count": repair_count,
            "score_breakdown": copy.deepcopy(reliability.get("score_breakdown", {})),
        }
        # 웹앱 채점에 필요한 축별 신호 강도를 rubric에서 자동 계산한다.
        rubric = final_output.get("evaluation", {}).get("rubric", [])
        final_output["axis_signals_derived"] = _compute_axis_signals(rubric)
        return final_output
