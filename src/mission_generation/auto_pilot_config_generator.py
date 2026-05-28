# 이전 방식 유틸이다. 현재 기본 PilotRunner 경로는 auto_pilot_config를 만들거나 사용하지 않는다.
# 과거 결과 비교와 관련 테스트를 위해 모듈만 유지한다.

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .config import EXCLUDED_MATERIAL_TYPES, MATERIAL_TYPES, TASK_TYPES


GENERATOR_VERSION = "auto_pilot_config_generator.v1"

TASK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "research_and_analysis": ("조사", "수집", "분석", "파악", "검토", "예측", "처리", "해석"),
    "planning_and_proposal": ("기획", "개발", "제안", "계획", "설계", "수립"),
    "decision_making": ("선택", "판단", "결정", "평가", "우선순위", "선정"),
    "coordination_and_negotiation": ("협의", "조율", "계약", "협상", "조정"),
    "diagnosis_and_improvement": ("진단", "개선", "수정", "문제점", "평가", "보완"),
    "operation_and_scheduling": ("일정", "운영", "관리", "배치"),
    "communication_and_reporting": ("보고", "전달", "설명", "작성", "발표"),
}

EXEC_JOB_ACTION_WEIGHTS: dict[str, int] = {
    "조사": 3,
    "수집": 3,
    "분석": 3,
    "파악": 3,
    "검토": 3,
    "예측": 3,
    "처리": 3,
    "해석": 3,
    "기획": 3,
    "개발": 3,
    "제안": 3,
    "계획": 3,
    "설계": 3,
    "수립": 3,
    "선택": 2,
    "판단": 2,
    "결정": 2,
    "평가": 2,
    "우선순위": 2,
    "선정": 2,
    "협의": 2,
    "조율": 2,
    "계약": 2,
    "협상": 2,
    "조정": 2,
    "일정": 2,
    "운영": 2,
    "관리": 2,
    "배치": 2,
    "보고": 2,
    "전달": 2,
    "설명": 2,
    "작성": 2,
    "발표": 2,
}

TASK_TIE_PRIORITY = {
    "planning_and_proposal": 7,
    "research_and_analysis": 6,
    "decision_making": 5,
    "diagnosis_and_improvement": 4,
    "coordination_and_negotiation": 3,
    "operation_and_scheduling": 2,
    "communication_and_reporting": 1,
}

TOKEN_STOPWORDS = {
    "업무",
    "관련",
    "수행",
    "관리",
    "자료",
    "정보",
    "직무",
    "필요",
    "기반",
    "대상",
    "결과",
    "사물",
    "행동",
    "사건",
    "파악",
    "활동",
    "목표",
    "전략",
    "현재",
    "미래",
    "특정",
    "대한",
    "관한",
    "통해",
    "토대로",
    "국내",
    "국외",
    "맞는",
    "각종",
    "사용",
    "활용",
    "등을",
    "등에",
    "한다",
    "하여",
    "하고",
    "되는",
    "있는",
    "없는",
}

KEYWORD_STOPWORDS = TOKEN_STOPWORDS - {"정보", "자료", "결과"}
TITLE_TERMS = ("데이터", "분석", "상품", "기획", "투자", "보험", "개발", "금융", "판매", "서비스")

MATERIAL_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("이메일", "소통", "문서 주고받기"), ("email",)),
    (("일정", "우선순위", "마감"), ("schedule",)),
    (("정보 처리", "컴퓨터", "데이터"), ("log", "table")),
    (("기준", "평가", "위험", "점검"), ("checklist",)),
    (("비교", "대안", "후보", "사람", "서비스"), ("card", "table")),
    (("정보 수집", "자료 분석", "분석"), ("chart", "table")),
    (("목표", "전략"), ("table", "schedule", "card")),
)


class AutoPilotConfigGenerator:
    """LLM 없이 job_profile을 점수화해 수행직무/task/material 후보 config를 만든다."""

    def build(self, job_profile: dict[str, Any], generated_from: str | None = None) -> dict[str, Any]:
        """job_profile 하나를 auto_pilot_config.v1 구조로 변환한다."""

        warnings: list[dict[str, str]] = []
        trace: list[dict[str, Any]] = []
        job_identity = job_profile.get("job_identity", {})
        job_cd = str(job_identity.get("job_cd") or "")
        job_name = str(job_identity.get("job_smcl_nm") or "")

        selected_exec_job, exec_metrics = self._select_exec_job(job_profile, trace, warnings)
        selected_text = str((selected_exec_job or {}).get("text") or "")
        primary_task_type, task_metrics = self._primary_task_type(job_profile, selected_text, trace, warnings)
        easy_materials, easy_metrics = self._materials(
            job_profile,
            selected_text,
            primary_task_type,
            "easy",
            trace,
            warnings,
        )
        normal_materials, normal_metrics = self._materials(
            job_profile,
            selected_text,
            primary_task_type,
            "normal",
            trace,
            warnings,
        )
        hard_materials, hard_metrics = self._materials(
            job_profile,
            selected_text,
            primary_task_type,
            "hard",
            trace,
            warnings,
        )
        config = {
            "preferred_exec_job_id": str((selected_exec_job or {}).get("exec_job_id") or ""),
            "preferred_exec_job_keywords": self._preferred_keywords(job_profile, selected_text),
            "preferred_primary_task_type": primary_task_type,
            "materials": {
                "easy": easy_materials,
                "normal": normal_materials,
                "hard": hard_materials,
            },
        }
        confidence = self._confidence(
            exec_metrics=exec_metrics,
            task_metrics=task_metrics,
            material_metrics=[easy_metrics, normal_metrics, hard_metrics],
            warnings=warnings,
        )
        if confidence["score"] < 0.5 and not any(item["code"] == "AUTO_CONFIG_LOW_CONFIDENCE" for item in warnings):
            warnings.append(
                {
                    "code": "AUTO_CONFIG_LOW_CONFIDENCE",
                    "severity": "warning",
                    "message": "Auto pilot config confidence score is below 0.50.",
                }
            )
            confidence["review_required"] = True
            confidence["reasons"].append("confidence score is below 0.50")

        return {
            "schema_version": "auto_pilot_config.v1",
            "job_cd": job_cd,
            "job_name": job_name,
            "source": {
                "profile_schema_version": job_profile.get("schema_version", ""),
                "generated_from": generated_from,
                "generator_version": GENERATOR_VERSION,
            },
            "config": config,
            "confidence": confidence,
            "decision_trace": trace,
            "decision_warnings": warnings,
        }

    def _select_exec_job(
        self,
        profile: dict[str, Any],
        trace: list[dict[str, Any]],
        warnings: list[dict[str, str]],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """이전 auto config 방식에서 수행직무 후보를 점수화해 하나 선택한다."""

        exec_jobs = list(profile.get("work", {}).get("exec_jobs") or [])
        if not exec_jobs:
            warnings.append(
                {
                    "code": "EXEC_JOBS_MISSING",
                    "severity": "warning",
                    "message": "job_profile.work.exec_jobs is empty; auto config needs human review.",
                }
            )
            trace.append(
                {
                    "step": "select_preferred_exec_job",
                    "method": "missing_exec_jobs",
                    "selected": None,
                    "reason": "No exec_jobs were available.",
                }
            )
            return None, {"score": 0, "fallback": True}

        evidence_tokens = self._evidence_tokens(profile)
        title_tokens = self._title_tokens(profile)
        scored: list[dict[str, Any]] = []
        for index, item in enumerate(exec_jobs):
            text = str(item.get("text") or "")
            action_score = self._action_score(text)
            evidence_score = self._evidence_score(text, evidence_tokens)
            title_score = sum(2 for token in title_tokens if token in text)
            combo_score = self._combo_score(text)
            total_score = action_score + evidence_score + title_score + combo_score
            scored.append(
                {
                    "index": index,
                    "item": item,
                    "total_score": total_score,
                    "action_score": action_score,
                    "evidence_score": evidence_score,
                    "title_score": title_score,
                    "combo_score": combo_score,
                }
            )

        best = max(
            scored,
            key=lambda item: (
                item["total_score"],
                item["evidence_score"],
                item["action_score"],
                -item["index"],
            ),
        )
        if best["total_score"] <= 0:
            warnings.append(
                {
                    "code": "TARGET_EXEC_JOB_FALLBACK_USED",
                    "severity": "warning",
                    "message": "Could not select target exec_job by rules; first exec_job is used.",
                }
            )
            best = scored[0]
        trace.append(
            {
                "step": "select_preferred_exec_job",
                "method": "auto_exec_job_score",
                "selected": best["item"].get("exec_job_id"),
                "reason": (
                    f"total={best['total_score']}, action={best['action_score']}, "
                    f"evidence={best['evidence_score']}, combo={best['combo_score']}."
                ),
            }
        )
        return best["item"], {"score": best["total_score"], "fallback": best["total_score"] <= 0}

    def _primary_task_type(
        self,
        profile: dict[str, Any],
        selected_text: str,
        trace: list[dict[str, Any]],
        warnings: list[dict[str, str]],
    ) -> tuple[str, dict[str, Any]]:
        """이전 auto config 방식에서 수행직무/evidence 기반 대표 task type을 고른다."""

        scores = self._task_scores(selected_text)
        method = "exec_job_verb_rule"
        best_task, best_score = self._best_task(scores)
        if best_score == 0:
            evidence_text = " ".join(
                str(item.get("name") or "")
                for item in profile.get("evidence", {}).get("work_activities", [])[:5]
                if isinstance(item, dict)
            )
            scores = self._task_scores(evidence_text)
            method = "work_activities_rule"
            best_task, best_score = self._best_task(scores)
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
                "step": "classify_primary_task_type",
                "method": method,
                "selected": best_task,
                "reason": f"Keyword score {best_score}.",
            }
        )
        return best_task, {"score": best_score, "fallback": best_score == 0}

    def _materials(
        self,
        profile: dict[str, Any],
        selected_text: str,
        task_type: str,
        difficulty: str,
        trace: list[dict[str, Any]],
        warnings: list[dict[str, str]],
    ) -> tuple[list[str], dict[str, Any]]:
        """이전 auto config 방식에서 난이도별 자료 유형 후보를 만든다."""

        max_count = {"easy": 1, "normal": 2, "hard": 3}.get(difficulty, 2)
        candidates = self._base_materials(selected_text, task_type, difficulty)
        evidence_text = self._high_score_evidence_text(profile)
        evidence_supported = False
        trimmed_candidates: list[str] = []
        for signals, material_types in MATERIAL_HINTS:
            if any(signal in evidence_text or signal in selected_text for signal in signals):
                evidence_supported = True
                for material_type in material_types:
                    if material_type in candidates:
                        continue
                    if len(candidates) < max_count:
                        candidates.append(material_type)
                    elif material_type not in trimmed_candidates:
                        trimmed_candidates.append(material_type)

        allowed = []
        for material_type in candidates:
            if material_type in EXCLUDED_MATERIAL_TYPES or material_type not in MATERIAL_TYPES:
                warnings.append(
                    {
                        "code": "INVALID_MATERIAL_TYPE_REMOVED",
                        "severity": "warning",
                        "message": f"{material_type} is not an allowed material type.",
                    }
                )
                continue
            if material_type not in allowed:
                allowed.append(material_type)
        allowed = allowed[:max_count]
        if len(allowed) < max_count:
            warnings.append(
                {
                    "code": "MATERIAL_CANDIDATE_BELOW_TARGET",
                    "severity": "warning",
                    "message": f"{difficulty} material candidates are below target count {max_count}.",
                }
            )
        trace.append(
            {
                "step": f"select_materials_{difficulty}",
                "method": "auto_material_rule",
                "selected": allowed,
                "reason": f"{difficulty} material candidates selected within max {max_count}.",
                "trimmed_candidates": trimmed_candidates,
            }
        )
        return allowed, {"score": len(allowed), "evidence_supported": evidence_supported or bool(selected_text)}

    def _base_materials(self, selected_text: str, task_type: str, difficulty: str) -> list[str]:
        is_data = "데이터" in selected_text and ("처리" in selected_text or "플랫폼" in selected_text)
        is_market_feedback = any(signal in selected_text for signal in ("판매수준", "소비자", "평가"))
        if difficulty == "easy":
            if is_data:
                return ["table"]
            if task_type == "planning_and_proposal":
                return ["table"]
            if is_market_feedback:
                return ["memo"]
            return ["chart"]
        if difficulty == "normal":
            if is_data:
                return ["chart", "table"]
            if task_type == "planning_and_proposal":
                return ["table", "chart"]
            if is_market_feedback:
                return ["chart", "memo"]
            return ["chart", "table"]

        if is_data:
            return ["chart", "table", "log"]
        if task_type == "planning_and_proposal":
            return ["email", "table", "chart"]
        if is_market_feedback:
            return ["email", "chart", "table"]
        return ["email", "chart", "table"]

    def _confidence(
        self,
        *,
        exec_metrics: dict[str, Any],
        task_metrics: dict[str, Any],
        material_metrics: list[dict[str, Any]],
        warnings: list[dict[str, str]],
    ) -> dict[str, Any]:
        exec_part = 0.0 if exec_metrics.get("fallback") else min(0.40, 0.18 + min(float(exec_metrics.get("score", 0)), 30.0) / 30.0 * 0.22)
        task_part = 0.08 if task_metrics.get("fallback") else min(0.25, 0.12 + min(float(task_metrics.get("score", 0)), 4.0) / 4.0 * 0.13)
        material_supported = any(item.get("evidence_supported") for item in material_metrics)
        material_part = 0.25 if material_supported else 0.12
        warning_part = 0.10 if not warnings else 0.0
        score = round(min(1.0, exec_part + task_part + material_part + warning_part), 2)
        level = "high" if score >= 0.80 else "medium" if score >= 0.50 else "low"
        review_required = level == "low" or any(
            item["code"]
            in {
                "EXEC_JOBS_MISSING",
                "TARGET_EXEC_JOB_FALLBACK_USED",
                "TASK_TYPE_RULE_LOW_CONFIDENCE",
                "MATERIAL_RULE_LOW_CONFIDENCE",
            }
            for item in warnings
        )
        reasons = [
            f"exec_job score contribution={round(exec_part, 2)}",
            f"task_type score contribution={round(task_part, 2)}",
            f"material score contribution={round(material_part, 2)}",
        ]
        if warnings:
            reasons.append("warnings require review")
        return {"score": score, "level": level, "review_required": review_required, "reasons": reasons}

    def _action_score(self, text: str) -> int:
        return sum(weight * text.count(keyword) for keyword, weight in EXEC_JOB_ACTION_WEIGHTS.items() if keyword in text)

    def _evidence_score(self, text: str, evidence_tokens: Counter[str]) -> int:
        return sum(weight for token, weight in evidence_tokens.items() if token in text)

    def _combo_score(self, text: str) -> int:
        score = 0
        if all(signal in text for signal in ("정보", "수집", "분석")):
            score += 5
        if "데이터" in text and "처리" in text:
            score += 10
        if "플랫폼" in text and "처리" in text:
            score += 3
        if "분석결과" in text and "개발" in text:
            score += 10
        if all(signal in text for signal in ("경제상황", "산업", "기업", "정보")):
            score += 8
        if "판매수준" in text or ("소비자" in text and "평가" in text):
            score += 4
        if "보고서" in text or "발표" in text:
            score -= 4
        return score

    def _task_scores(self, text: str) -> dict[str, int]:
        return {
            task_type: sum(text.count(keyword) for keyword in keywords)
            for task_type, keywords in TASK_KEYWORDS.items()
            if task_type in TASK_TYPES
        }

    def _best_task(self, scores: dict[str, int]) -> tuple[str, int]:
        task_type, score = max(
            scores.items(),
            key=lambda item: (item[1], TASK_TIE_PRIORITY.get(item[0], 0)),
        )
        return task_type, score

    def _evidence_tokens(self, profile: dict[str, Any]) -> Counter[str]:
        tokens: Counter[str] = Counter()
        group_weights = {"work_activities": 2, "knowledge": 1, "abilities": 1}
        for group_name in ("work_activities", "knowledge", "abilities"):
            for item in profile.get("evidence", {}).get(group_name, []):
                if not isinstance(item, dict):
                    continue
                score = float(item.get("score") or 0)
                if score < 70:
                    continue
                score_weight = 3 if score >= 90 else 2 if score >= 80 else 1
                weight = score_weight * group_weights[group_name]
                text = f"{item.get('name') or ''} {item.get('description') or ''}"
                for token in self._tokens(text, TOKEN_STOPWORDS):
                    tokens[token] += weight
        return tokens

    def _title_tokens(self, profile: dict[str, Any]) -> list[str]:
        title = str(profile.get("job_identity", {}).get("job_smcl_nm") or "")
        tokens = self._tokens(title, TOKEN_STOPWORDS)
        for term in TITLE_TERMS:
            if term in title and term not in tokens:
                tokens.append(term)
        return tokens

    def _preferred_keywords(self, profile: dict[str, Any], selected_text: str) -> list[str]:
        """수행직무와 evidence에 반복되는 키워드를 auto config 힌트로 추린다."""

        ordered: list[str] = []
        counts: Counter[str] = Counter()
        source_text = selected_text + " " + self._linked_evidence_text(profile, selected_text)
        for token in self._tokens(source_text, KEYWORD_STOPWORDS):
            if token not in ordered:
                ordered.append(token)
            counts[token] += 1
        for keyword in EXEC_JOB_ACTION_WEIGHTS:
            if keyword in selected_text and keyword not in ordered:
                ordered.append(keyword)
                counts[keyword] += 1
        ranked = sorted(ordered, key=lambda token: (-counts[token], ordered.index(token), token))
        return ranked[:6]

    def _linked_evidence_text(self, profile: dict[str, Any], selected_text: str) -> str:
        parts: list[str] = []
        for group_name in ("work_activities", "knowledge", "abilities"):
            for item in profile.get("evidence", {}).get(group_name, [])[:6]:
                if not isinstance(item, dict):
                    continue
                text = f"{item.get('name') or ''} {item.get('description') or ''}"
                if any(token in selected_text for token in self._tokens(text, TOKEN_STOPWORDS)):
                    parts.append(text)
        return " ".join(parts)

    def _high_score_evidence_text(self, profile: dict[str, Any]) -> str:
        parts: list[str] = []
        for group in profile.get("evidence", {}).values():
            for item in group:
                if isinstance(item, dict) and float(item.get("score") or 0) >= 80:
                    parts.append(str(item.get("name") or ""))
                    parts.append(str(item.get("description") or ""))
        return " ".join(parts)

    def _tokens(self, text: str, stopwords: set[str]) -> list[str]:
        tokens: list[str] = []
        for raw in re.findall(r"[가-힣A-Za-z0-9]+", text):
            token = self._strip_suffix(raw)
            if len(token) < 2 or token in stopwords:
                continue
            if token not in tokens:
                tokens.append(token)
        return tokens

    def _strip_suffix(self, token: str) -> str:
        for suffix in ("으로부터", "으로서", "으로써", "에서", "에게", "으로", "하고", "하여", "하는", "한다", "들과", "들을", "들의"):
            if token.endswith(suffix) and len(token) > len(suffix) + 1:
                token = token[: -len(suffix)]
        for suffix in ("이다", "하며", "하고", "하여", "되는", "하는", "한다", "부터", "까지", "에게", "에서", "으로", "로서", "로써", "와", "과", "을", "를", "은", "는", "이", "가", "의", "로", "에"):
            if token.endswith(suffix) and len(token) > len(suffix) + 1:
                token = token[: -len(suffix)]
        return token
