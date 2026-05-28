# 별도 CLI 유틸이다. PilotRunner가 미션 생성 중에 직접 호출하지 않는다.
# run 저장 후 `python -m mission_generation.ui_exporter --view ...`로 실행한다.

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .config import default_pilot_config
from .utils import ensure_inside_workspace, project_path, scan_text_for_secrets


DEFAULT_RUN_ID = "pilot_v1_20260524_055451"

TASK_TYPE_LABELS = {
    "research_and_analysis": "조사·분석형",
    "coordination_and_negotiation": "협업·조율형",
    "communication_and_reporting": "소통·보고형",
    "planning_and_proposal": "기획·제안형",
    "decision_making": "의사결정형",
    "operation_and_scheduling": "운영·일정관리형",
}

MATERIAL_TYPE_LABELS = {
    "chart": "차트 자료",
    "table": "표 자료",
    "memo": "메모 자료",
    "email": "이메일 자료",
    "log": "로그 자료",
    "checklist": "체크리스트",
    "schedule": "일정표",
}

SUBMISSION_TYPE_LABELS = {
    "text": "서술형",
    "short_text": "짧은 서술형",
}

LENGTH_HINT_LABELS = {
    "1-2 short sentences": "짧은 문장 1~2개",
    "2-3 short sentences per task": "과제별 짧은 문장 2~3개",
}

PRIVATE_LEARNER_KEYS = {
    "confidence",
    "evidence_chain",
    "evidence_source",
    "expected_action",
    "linked_evidence",
    "mission_fact_refs",
    "mission_id",
    "reliability",
    "repair_count",
    "source_ref",
    "source_refs",
    "task_id",
    "warning_count",
}


class MissionUIExporter:
    """pilot run 산출물을 QA용/학습자용 단일 HTML 파일로 변환한다."""

    def __init__(self, *, output_root: str | Path = "outputs") -> None:
        self.output_root = project_path(output_root)

    def export(
        self,
        *,
        run_id: str = DEFAULT_RUN_ID,
        pilot_run_dir: str | Path | None = None,
        ui_output_dir: str | Path | None = None,
        ) -> Path:
        """기본 review 옵션에서 쓰는 QA용 mission_ui.html을 생성한다."""

        run_dir = project_path(pilot_run_dir) if pilot_run_dir else self.output_root / "pilot" / "v1" / "runs" / run_id
        ensure_inside_workspace(run_dir)
        mission_slots = self._load_mission_slots(run_dir)
        missions = [slot["mission"] for slot in mission_slots if slot["status"] == "saved" and slot.get("mission")]
        summary = self._load_summary(run_dir)
        payload = {
            "schema_version": "mission_ui_payload.v1.1",
            "run_id": run_id,
            "summary": summary,
            "mission_slots": mission_slots,
            "missions": missions,
        }
        html = self._render_html(payload)
        findings = scan_text_for_secrets(html)
        if findings:
            raise ValueError(f"generated HTML contains secret-like patterns: {findings}")
        output_dir = project_path(ui_output_dir) if ui_output_dir else self.output_root / "ui" / "v1" / "runs" / run_id
        ensure_inside_workspace(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "mission_ui.html"
        output_path.write_text(html, encoding="utf-8", newline="\n")
        return output_path

    def export_learner(
        self,
        *,
        run_id: str = DEFAULT_RUN_ID,
        pilot_run_dir: str | Path | None = None,
        ui_output_dir: str | Path | None = None,
    ) -> Path:
        """--view learner 또는 --view both 옵션에서 학습자용 mission_learner.html을 생성한다."""

        run_dir = project_path(pilot_run_dir) if pilot_run_dir else self.output_root / "pilot" / "v1" / "runs" / run_id
        ensure_inside_workspace(run_dir)
        mission_slots = self._load_mission_slots(run_dir)
        payload = self._build_learner_payload(mission_slots)
        html = self._render_learner_html(payload)
        findings = scan_text_for_secrets(html)
        if findings:
            raise ValueError(f"generated learner HTML contains secret-like patterns: {findings}")
        output_dir = project_path(ui_output_dir) if ui_output_dir else self.output_root / "ui" / "v1" / "runs" / run_id
        ensure_inside_workspace(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "mission_learner.html"
        output_path.write_text(html, encoding="utf-8", newline="\n")
        return output_path

    def _load_summary(self, run_dir: Path) -> dict[str, Any]:
        """QA 화면 상단에 보여줄 run summary 핵심값만 읽는다."""

        path = run_dir / "pilot_summary.json"
        if not path.exists():
            return {"saved_count": None, "failed_count": None, "openai_api_called": None}
        summary = json.loads(path.read_text(encoding="utf-8"))
        return {
            "total_targets": summary.get("total_targets"),
            "saved_count": summary.get("saved_count"),
            "failed_count": summary.get("failed_count"),
            "repair_used_count": summary.get("repair_used_count"),
            "average_reliability_score": summary.get("average_reliability_score"),
            "openai_api_called": summary.get("openai_api_called"),
            "post_run_checks": summary.get("post_run_checks"),
        }

    def _load_mission_slots(self, run_dir: Path) -> list[dict[str, Any]]:
        """pilot_config 기준 전체 slot을 읽고 saved/failed/missing 상태를 붙인다."""

        pilot_config = self._load_pilot_config(run_dir)
        artifacts = self._items_by_slot(self._load_optional_json(run_dir / "artifact_index.json").get("items", []))
        failures = self._items_by_slot(self._load_optional_json(run_dir / "_failed" / "failure_index.json").get("items", []))
        slots: list[dict[str, Any]] = []

        for job in pilot_config["jobs"]:
            job_cd = job["job_cd"]
            for difficulty in pilot_config["difficulties"]:
                difficulty_code = difficulty["code"]
                slot_key = self._slot_key(job_cd, difficulty_code)
                artifact = artifacts.get(slot_key, {})
                failure_item = failures.get(slot_key, {})
                mission_relative_path = artifact.get("mission_output_path") or f"jobs/{job_cd}/{difficulty_code}/mission_output.json"
                mission_path = run_dir / mission_relative_path
                run_status_relative_path = (
                    artifact.get("run_status_path")
                    or failure_item.get("run_status_path")
                    or f"jobs/{job_cd}/{difficulty_code}/run_status.json"
                )
                run_status_path = run_dir / run_status_relative_path
                run_status = self._load_optional_json(run_status_path)

                base_slot = {
                    "slot_key": slot_key,
                    "job_cd": job_cd,
                    "job_name": run_status.get("job_name") or job.get("job_name"),
                    "difficulty_code": difficulty_code,
                    "difficulty_label": difficulty.get("label") or self._difficulty_label(difficulty_code),
                }

                if mission_path.exists():
                    mission = self._load_mission(run_dir, mission_path)
                    slots.append(
                        {
                            **base_slot,
                            "job_name": mission.get("job_name") or base_slot["job_name"],
                            "status": "saved",
                            "selectable": True,
                            "mission": mission,
                            "mission_output_path": mission_path.relative_to(run_dir).as_posix(),
                            "run_status_path": run_status_relative_path if run_status_path.exists() else None,
                        }
                    )
                    continue

                has_failed_artifact = bool(artifact or failure_item or run_status_path.exists())
                if has_failed_artifact:
                    slots.append(
                        {
                            **base_slot,
                            "status": "failed",
                            "selectable": False,
                            "failure": self._failure_summary(
                                artifact=artifact,
                                failure_item=failure_item,
                                run_status=run_status,
                                run_status_path=run_status_relative_path if run_status_path.exists() else None,
                            ),
                        }
                    )
                    continue

                slots.append(
                    {
                        **base_slot,
                        "status": "missing",
                        "selectable": False,
                        "failure": {
                            "status": "missing",
                            "reason_code": "MISSION_OUTPUT_MISSING",
                            "message": "Expected target slot has no mission output or run status artifact.",
                            "run_status_path": None,
                            "validator_result_path": None,
                        },
                    }
                )

        return slots

    def _load_pilot_config(self, run_dir: Path) -> dict[str, Any]:
        """run의 pilot_config를 읽고, 없으면 현재 기본 설정으로 보완한다."""

        config = self._load_optional_json(run_dir / "pilot_config.json")
        if not config:
            config = default_pilot_config()
        config.setdefault("jobs", default_pilot_config()["jobs"])
        config.setdefault("difficulties", default_pilot_config()["difficulties"])
        return config

    def _load_optional_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _items_by_slot(self, items: Any) -> dict[str, dict[str, Any]]:
        """artifact/failure item 목록을 job_cd:difficulty_code key로 재색인한다."""

        if not isinstance(items, list):
            return {}
        by_slot: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            job_cd = item.get("job_cd")
            difficulty_code = item.get("difficulty_code")
            if isinstance(job_cd, str) and isinstance(difficulty_code, str):
                by_slot[self._slot_key(job_cd, difficulty_code)] = item
        return by_slot

    def _load_mission(self, run_dir: Path, path: Path) -> dict[str, Any]:
        """mission_output.json에서 UI payload에 필요한 표시/검토 필드만 추출한다."""

        data = json.loads(path.read_text(encoding="utf-8"))
        mission = data["mission"]
        job_identity = data["job_identity"]
        reliability = data.get("reliability", {})
        return {
            "mission_id": data["mission_id"],
            "job_cd": job_identity.get("job_cd"),
            "job_name": job_identity.get("job_smcl_nm"),
            "difficulty": mission.get("difficulty", {}),
            "title": mission.get("title"),
            "task_type": mission.get("task_type"),
            "secondary_task_types": mission.get("secondary_task_types", []),
            "target_exec_job": data.get("target_exec_job", {}),
            "mission_facts": data.get("mission_facts", {}),
            "scenario": self._scenario_with_glossary_notes(mission.get("scenario", {})),
            "materials": mission.get("materials", []),
            "tasks": mission.get("tasks", []),
            "submission_format": mission.get("submission_format", {}),
            "evaluation": data.get("evaluation", {}),
            "reliability": {
                "score": reliability.get("score"),
                "warning_count": reliability.get("warning_count"),
                "fail_count": reliability.get("fail_count"),
                "repair_count": reliability.get("repair_count"),
            },
            "relative_path": path.relative_to(run_dir).as_posix(),
        }

    def _failure_summary(
        self,
        *,
        artifact: dict[str, Any],
        failure_item: dict[str, Any],
        run_status: dict[str, Any],
        run_status_path: str | None,
    ) -> dict[str, Any]:
        """failed/missing slot에 보여줄 이유와 관련 파일 경로를 정리한다."""

        errors = ((run_status.get("error") or {}).get("errors") or [])
        first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
        return {
            "status": run_status.get("status") or failure_item.get("status") or artifact.get("status") or "failed",
            "reason_code": first_error.get("code") or failure_item.get("reason_code") or "TARGET_FAILED",
            "message": first_error.get("message") or (run_status.get("error") or {}).get("message") or "Mission output was not saved.",
            "run_status_path": run_status_path,
            "validator_result_path": artifact.get("validator_result_path") or failure_item.get("validator_result_path"),
        }

    def _slot_key(self, job_cd: str, difficulty_code: str) -> str:
        return f"{job_cd}:{difficulty_code}"

    def _difficulty_label(self, difficulty_code: str) -> str:
        return {"easy": "쉬움", "normal": "보통", "hard": "어려움"}.get(difficulty_code, difficulty_code)

    def _build_learner_payload(self, mission_slots: list[dict[str, Any]]) -> dict[str, Any]:
        """학습자 화면에는 saved mission만 남기고 내부 검토 필드를 제거한다."""

        saved_slots = [slot for slot in mission_slots if slot["status"] == "saved" and slot.get("mission")]
        return {
            "schema_version": "mission_learner_payload.v1",
            "missions": [
                self._learner_mission(slot["mission"], index)
                for index, slot in enumerate(saved_slots, start=1)
            ],
        }

    def _learner_mission(self, mission: dict[str, Any], index: int) -> dict[str, Any]:
        """review payload의 mission을 내부 필드 없는 학습자용 mission으로 바꾼다."""

        difficulty = mission.get("difficulty", {})
        original_materials = mission.get("materials", [])
        material_labels = {
            material.get("material_id"): f"자료 {material_index}"
            for material_index, material in enumerate(original_materials, start=1)
            if isinstance(material, dict)
        }
        materials = [
            self._learner_material(material, material_index)
            for material_index, material in enumerate(original_materials, start=1)
            if isinstance(material, dict)
        ]
        return {
            "uid": f"learner_mission_{index}",
            "job_name": mission.get("job_name"),
            "difficulty_label": difficulty.get("label") or self._difficulty_label(difficulty.get("level", "")),
            "time_limit_minutes": difficulty.get("estimated_time_minutes"),
            "task_type_label": self._task_type_label(mission.get("task_type")),
            "secondary_task_type_labels": [
                self._task_type_label(item) for item in mission.get("secondary_task_types", [])
            ],
            "title": mission.get("title"),
            "scenario": self._learner_scenario(mission.get("scenario", {})),
            "materials": materials,
            "tasks": [
                self._learner_task(task, task_index, material_labels)
                for task_index, task in enumerate(mission.get("tasks", []), start=1)
                if isinstance(task, dict)
            ],
            "submission_format": self._learner_submission(mission.get("submission_format", {})),
            "evaluation": self._learner_evaluation(mission.get("evaluation", {})),
        }

    def _learner_scenario(self, scenario: dict[str, Any]) -> dict[str, Any]:
        """scenario에서 내부 필드를 제거하고 glossary 보정까지 적용한다."""

        cleaned = self._strip_private_fields(scenario)
        if not isinstance(cleaned, dict):
            return {}
        return self._scenario_with_glossary_notes(cleaned)

    def _scenario_with_glossary_notes(self, scenario: dict[str, Any]) -> dict[str, Any]:
        """이전 run의 '용어 설명:' 본문 메모를 glossary 카드 데이터로 보정한다."""

        if not isinstance(scenario, dict):
            return {}
        cleaned = dict(scenario)
        glossary = [
            {"term": str(item.get("term", "")).strip(), "definition": str(item.get("definition", "")).strip()}
            for item in cleaned.get("glossary", [])
            if isinstance(item, dict) and str(item.get("term", "")).strip() and str(item.get("definition", "")).strip()
        ]
        context, context_glossary = self._extract_glossary_note(str(cleaned.get("context") or ""))
        glossary.extend(context_glossary)

        constraints: list[Any] = []
        for item in cleaned.get("constraints", []):
            if not isinstance(item, str):
                constraints.append(item)
                continue
            stripped_item, item_glossary = self._extract_glossary_note(item)
            glossary.extend(item_glossary)
            if stripped_item:
                constraints.append(stripped_item)

        cleaned["context"] = context
        cleaned["constraints"] = constraints
        cleaned["glossary"] = glossary
        return cleaned

    def _extract_glossary_note(self, text: str) -> tuple[str, list[dict[str, str]]]:
        """이전 산출물의 '용어 설명:' 꼬리 문구를 본문과 glossary 후보로 분리한다."""

        match = re.search(r"\s*(용어\s*(?:설명|정리)\s*[:：]\s*)(.+?)\s*$", text, flags=re.S)
        if not match:
            return text, []
        before = text[: match.start()].strip()
        note = match.group(2).strip()
        parsed = self._parse_glossary_note(note)
        return before, [parsed] if parsed else []

    def _parse_glossary_note(self, note: str) -> dict[str, str] | None:
        """'용어는 설명' 형태의 짧은 문장을 glossary 항목으로 바꾼다."""

        note = note.strip()
        if not note:
            return None
        match = re.match(r"[‘'\"“]?([^’'\"”:=：은는]+?)[’'\"”]?\s*(?:은|는|이란|란|=|:|：)\s*(.+)", note, flags=re.S)
        if match:
            term = match.group(1).strip(" ‘'\"“”")
            definition = match.group(2).strip()
            if term and definition:
                return {"term": term, "definition": definition}
        return {"term": "용어", "definition": note}

    def _learner_material(self, material: dict[str, Any], index: int) -> dict[str, Any]:
        """내부 material_id 대신 자료 1, 자료 2 라벨을 가진 학습자용 자료로 바꾼다."""

        material_type = material.get("type")
        return {
            "uid": f"material_{index}",
            "label": f"자료 {index}",
            "type": material_type,
            "type_label": self._material_type_label(material_type),
            "title": material.get("title"),
            "description": material.get("description"),
            "data": self._strip_private_fields(material.get("data", {})),
        }

    def _learner_task(
        self,
        task: dict[str, Any],
        index: int,
        material_labels: dict[Any, str],
    ) -> dict[str, Any]:
        """expected_action과 raw material id를 숨긴 학습자용 task를 만든다."""

        labels = [
            material_labels.get(material_id)
            for material_id in task.get("required_materials", [])
            if material_labels.get(material_id)
        ]
        return {
            "label": f"과제 {index}",
            "instruction": task.get("instruction"),
            "material_labels": labels,
        }

    def _learner_submission(self, submission_format: dict[str, Any]) -> dict[str, Any]:
        """submission_format을 한국어 라벨 중심의 학습자 표시 데이터로 바꾼다."""

        return {
            "type_label": self._submission_type_label(submission_format.get("type")),
            "estimated_time_minutes": submission_format.get("estimated_time_minutes"),
            "length_hint_label": self._length_hint_label(submission_format.get("length_hint")),
            "required_sections": self._strip_private_fields(submission_format.get("required_sections", [])),
        }

    def _learner_evaluation(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        """학습자 화면에는 rubric 세부 점수 대신 criterion 이름만 남긴다."""

        rubric = evaluation.get("rubric", [])
        return {
            "criteria": [
                item.get("criterion")
                for item in rubric
                if isinstance(item, dict) and item.get("criterion")
            ]
        }

    def _strip_private_fields(self, value: Any) -> Any:
        """학습자 payload에서 evidence, source, reliability 같은 내부 필드를 재귀적으로 제거한다."""

        if isinstance(value, dict):
            return {
                key: self._strip_private_fields(item)
                for key, item in value.items()
                if key not in PRIVATE_LEARNER_KEYS
            }
        if isinstance(value, list):
            return [self._strip_private_fields(item) for item in value]
        return value

    def _task_type_label(self, task_type: str | None) -> str:
        if not task_type:
            return ""
        return TASK_TYPE_LABELS.get(task_type, "과제형")

    def _material_type_label(self, material_type: str | None) -> str:
        if not material_type:
            return "자료"
        return MATERIAL_TYPE_LABELS.get(material_type, "자료")

    def _submission_type_label(self, submission_type: str | None) -> str:
        if not submission_type:
            return ""
        return SUBMISSION_TYPE_LABELS.get(submission_type, "서술형")

    def _length_hint_label(self, length_hint: str | None) -> str:
        if not length_hint:
            return ""
        return LENGTH_HINT_LABELS.get(length_hint, length_hint)

    def _render_html(self, payload: dict[str, Any]) -> str:
        """검토자용 QA HTML을 self-contained 정적 파일 문자열로 렌더링한다."""

        encoded_payload = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JOBSIM Mission UI - {payload["run_id"]}</title>
<style>
:root {{
  --bg:#08090a; --bg2:#0f1011; --bg3:#161718; --bg4:#1c1d1f;
  --b0:rgba(255,255,255,.06); --b1:rgba(255,255,255,.1); --b2:rgba(255,255,255,.16);
  --t1:#f7f8f8; --t2:#b8bbc1; --t3:#8c929c; --t4:#626872;
  --a:#5e6ad2; --ah:#7777ff; --ab:rgba(94,106,210,.12); --ab2:rgba(94,106,210,.26);
  --ok:#47c07b; --warn:#f2c94c; --dng:#eb5757;
}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:var(--bg);color:var(--t1);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;-webkit-font-smoothing:antialiased}}
button,textarea{{font:inherit}} button{{cursor:pointer}}
nav{{position:fixed;inset:0 0 auto;height:64px;display:flex;align-items:center;gap:18px;padding:0 24px;background:rgba(8,9,10,.78);backdrop-filter:blur(20px);border-bottom:1px solid var(--b0);z-index:50}}
.logo{{font-weight:650;letter-spacing:-.01em}} .logo span{{color:var(--a)}} .nav-meta{{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}} .chip{{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--b1);background:var(--bg2);color:var(--t2);border-radius:999px;padding:4px 10px;font-size:12px}} .chip.ok{{color:var(--ok);border-color:rgba(71,192,123,.25)}} .chip.a{{color:var(--a);border-color:var(--ab2);background:var(--ab)}}
.wrap{{max-width:1180px;margin:0 auto;padding:104px 24px 72px}}
.hero{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:end;margin-bottom:28px}} .eyebrow{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--t3);margin-bottom:14px}} h1{{margin:0 0 10px;font-size:42px;line-height:1.08;font-weight:620;letter-spacing:-.025em}} .hero p{{margin:0;max-width:680px;color:var(--t2)}} .hero-actions{{display:flex;gap:8px}}
.btn{{border:1px solid var(--b1);background:var(--bg2);color:var(--t1);border-radius:7px;padding:9px 13px;transition:.15s}} .btn:hover{{border-color:var(--b2);background:var(--bg3)}} .btn.primary{{background:var(--a);border-color:var(--a);color:white}} .btn.primary:hover{{background:var(--ah)}}
.job-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:22px}} .job-group{{border:1px solid var(--b0);background:var(--bg2);border-radius:8px;padding:15px}} .job-head{{display:flex;justify-content:space-between;gap:10px;margin-bottom:12px}} .job-code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--t4)}} .job-name{{font-weight:650;letter-spacing:-.01em}} .job-summary{{font-size:12px;color:var(--t3)}} .slot-row{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}} .slot-card{{border:1px solid var(--b0);background:var(--bg3);border-radius:7px;padding:12px;min-height:116px;text-align:left;color:inherit;transition:.15s}} .slot-card.saved:hover{{border-color:var(--b2);background:var(--bg4)}} .slot-card.saved.active{{border-color:var(--ab2);background:var(--ab)}} .slot-card.failed,.slot-card.missing{{cursor:not-allowed;opacity:.82}} .slot-card.failed{{border-color:rgba(235,87,87,.22)}} .slot-card.missing{{border-color:rgba(242,201,76,.22)}} .slot-top{{display:flex;justify-content:space-between;gap:8px;margin-bottom:8px}} .slot-diff{{font-size:12px;color:var(--a)}} .slot-status{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;text-transform:uppercase;color:var(--t4)}} .slot-card.failed .slot-status{{color:var(--dng)}} .slot-card.missing .slot-status{{color:var(--warn)}} .slot-title{{font-size:13px;color:var(--t2);line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}} .slot-reason{{font-size:12px;color:var(--t3);line-height:1.35;margin-top:6px}}
.layout{{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}} .main{{display:flex;flex-direction:column;gap:12px}} .panel,.section{{background:var(--bg2);border:1px solid var(--b0);border-radius:8px}} .section{{padding:24px}} .panel{{position:sticky;top:88px;padding:22px;display:flex;flex-direction:column;gap:18px}} .breadcrumb{{font-size:12px;color:var(--t3);margin-bottom:12px}} .pill-row{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}} h2{{margin:0 0 10px;font-size:28px;line-height:1.24;font-weight:620;letter-spacing:-.018em}} h3{{margin:0 0 14px;font-size:17px;font-weight:620}} .scenario{{color:var(--t2);margin-bottom:14px}} .constraints{{display:grid;gap:7px;margin:14px 0 0;padding:0;list-style:none}} .constraints li{{border:1px solid var(--b0);background:var(--bg3);border-radius:6px;padding:9px 11px;color:var(--t2);font-size:13px}} .glossary{{margin:14px 0 0}} .glossary-title{{font-size:12px;color:var(--a);font-weight:650;margin-bottom:7px}} .glossary-list{{display:grid;gap:7px}} .glossary-card{{border:1px solid var(--b0);background:var(--bg3);border-radius:6px;padding:10px 12px;color:var(--t2);font-size:13px}} .glossary-card b{{display:block;color:var(--t1);margin-bottom:3px}}
.material-tabs{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}} .tab{{font-size:12px;padding:6px 10px;border-radius:999px;border:1px solid var(--b1);background:var(--bg3);color:var(--t2)}} .tab.active{{color:white;background:var(--a);border-color:var(--a)}} .material{{display:none}} .material.active{{display:block}} .mat-head{{display:flex;justify-content:space-between;gap:12px;margin-bottom:14px}} .mat-title{{font-weight:620}} .mat-desc{{color:var(--t2);font-size:13px;margin-top:4px}} .type{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--t3);text-transform:uppercase}} table{{width:100%;border-collapse:collapse;border:1px solid var(--b0);border-radius:6px;overflow:hidden;font-size:13px}} th{{background:var(--bg4);color:var(--t3);font-size:11px;text-align:left;padding:9px 12px;border-bottom:1px solid var(--b0)}} td{{padding:9px 12px;border-bottom:1px solid var(--b0);color:var(--t2)}} tr:last-child td{{border-bottom:0}} .num{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--a);text-align:right}}
.chart{{height:260px;border:1px solid var(--b0);border-radius:7px;background:var(--bg3);padding:10px}} .chart svg{{width:100%;height:100%;display:block}} .card-list{{display:grid;gap:8px}} .info-card{{border:1px solid var(--b0);background:var(--bg3);border-radius:7px;padding:13px}} .info-card b{{display:block;margin-bottom:5px}} .muted{{color:var(--t3)}} .timeline{{display:grid;gap:8px}} .timeline .item{{border-left:2px solid var(--a);padding:4px 0 8px 12px;color:var(--t2)}} .check-row{{display:flex;gap:9px;align-items:flex-start}} .check-dot{{width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:var(--ab);color:var(--a);font-size:12px;flex:0 0 auto;margin-top:2px}}
.task-list{{display:grid;gap:10px;counter-reset:task}} .task{{border:1px solid var(--b0);background:var(--bg3);border-radius:7px;padding:14px;counter-increment:task}} .task-meta{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--a);margin-bottom:6px;text-transform:uppercase}} .task-instruction{{color:var(--t1)}} .task-answer{{margin-top:12px;min-height:132px}} .refs{{margin-top:8px;display:flex;gap:5px;flex-wrap:wrap}} .ref{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--t3);border:1px solid var(--b0);border-radius:999px;padding:2px 7px}}
.panel-label{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--t3);margin-bottom:8px}} .timer{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:34px;line-height:1}} textarea{{width:100%;min-height:180px;border:1px solid var(--b1);background:var(--bg3);border-radius:7px;padding:12px;color:var(--t1);resize:vertical;outline:none}} textarea:focus{{border-color:var(--a);box-shadow:0 0 0 2px var(--ab)}} .char{{text-align:right;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--t4)}} .rubric{{display:grid;gap:8px}} .rubric-row{{display:grid;grid-template-columns:1fr auto;gap:8px;color:var(--t2);font-size:13px}} .score{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ok)}} .empty{{color:var(--t4);font-size:13px}}
@media (max-width:980px){{.job-grid{{grid-template-columns:1fr}} .layout{{grid-template-columns:1fr}} .panel{{position:static}} .hero{{grid-template-columns:1fr}}}}
@media (max-width:620px){{nav{{height:auto;min-height:64px;align-items:flex-start;flex-direction:column;padding:14px 16px}} .nav-meta{{margin-left:0}} .wrap{{padding:126px 14px 48px}} h1{{font-size:32px}} .slot-row{{grid-template-columns:1fr}} .section{{padding:18px}}}}
</style>
</head>
<body>
<nav>
  <div class="logo">JOB<span>SIM</span> Mission UI</div>
  <div class="nav-meta" id="navMeta"></div>
</nav>
<main class="wrap">
  <header class="hero">
    <div>
      <div class="eyebrow">mission pilot viewer</div>
      <h1>생성 미션을 실제 과제 화면처럼 검토합니다</h1>
      <p>파일럿 run의 8개 미션을 직무와 난이도별로 선택해 자료, 수행 과제, 제출 형식, 평가 기준을 한 화면에서 확인합니다.</p>
    </div>
    <div class="hero-actions">
      <button class="btn" id="prevBtn">이전</button>
      <button class="btn primary" id="nextBtn">다음</button>
    </div>
  </header>
  <section class="job-grid" id="jobGrid" aria-label="mission target slots"></section>
  <section class="layout">
    <div class="main">
      <article class="section" id="missionHeader"></article>
      <article class="section">
        <h3>제공 자료</h3>
        <div class="material-tabs" id="materialTabs"></div>
        <div id="materialBody"></div>
      </article>
      <article class="section">
        <h3>수행 과제</h3>
        <div class="task-list" id="taskList"></div>
      </article>
    </div>
    <aside class="panel">
      <div>
        <div class="panel-label">time limit</div>
        <div class="timer" id="timerVal">15:00</div>
      </div>
      <div>
        <div class="panel-label">submission format</div>
        <div id="submissionBox" class="muted"></div>
      </div>
      <div>
        <div class="panel-label">rubric</div>
        <div class="rubric" id="rubricBox"></div>
      </div>
      <div>
        <div class="panel-label">reliability</div>
        <div id="reliabilityBox"></div>
      </div>
    </aside>
  </section>
</main>
<script id="missionPayload" type="application/json">{encoded_payload}</script>
<script>
const DATA = JSON.parse(document.getElementById('missionPayload').textContent);
const SLOTS = DATA.mission_slots || (DATA.missions || []).map(m => ({{
  slot_key: `${{m.job_cd}}:${{m.difficulty?.level || ''}}`,
  job_cd: m.job_cd,
  job_name: m.job_name,
  difficulty_code: m.difficulty?.level || '',
  difficulty_label: m.difficulty?.label || m.difficulty?.level || '',
  status: 'saved',
  selectable: true,
  mission: m
}}));
let currentSlotKey = null;
let activeMaterial = 0;

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
const nl = value => esc(value).replace(/\\n/g, '<br>');
const savedSlots = () => SLOTS.filter(slot => slot.status === 'saved' && slot.selectable && slot.mission);
const label = m => `${{m.job_cd}} / ${{m.difficulty?.level || ''}}`;

function init() {{
  renderNav();
  renderJobGrid();
  const first = savedSlots()[0];
  if (first) renderMission(first.slot_key);
  else renderEmptyState();
  $('prevBtn').onclick = () => moveMission(-1);
  $('nextBtn').onclick = () => moveMission(1);
}}

function renderNav() {{
  const s = DATA.summary || {{}};
  $('navMeta').innerHTML = [
    `<span class="chip a">${{esc(DATA.run_id)}}</span>`,
    `<span class="chip ok">saved ${{esc(s.saved_count)}} / failed ${{esc(s.failed_count)}}</span>`,
    `<span class="chip">API ${{s.openai_api_called ? 'on' : 'off'}}</span>`
  ].join('');
}}

function renderJobGrid() {{
  const byJob = new Map();
  SLOTS.forEach(slot => {{
    if (!byJob.has(slot.job_cd)) {{
      byJob.set(slot.job_cd, {{ job_cd: slot.job_cd, job_name: slot.job_name, slots: [] }});
    }}
    byJob.get(slot.job_cd).slots.push(slot);
  }});
  $('jobGrid').innerHTML = [...byJob.values()].map(group => {{
    const saved = group.slots.filter(slot => slot.status === 'saved').length;
    const unavailable = group.slots.length - saved;
    return `
      <article class="job-group">
        <div class="job-head">
          <div><div class="job-code">${{esc(group.job_cd)}}</div><div class="job-name">${{esc(group.job_name)}}</div></div>
          <div class="job-summary">${{saved}} saved / ${{unavailable}} unavailable</div>
        </div>
        <div class="slot-row">${{group.slots.map(renderSlotCard).join('')}}</div>
      </article>
    `;
  }}).join('');
  document.querySelectorAll('.slot-card.saved').forEach(btn => {{
    btn.addEventListener('click', () => renderMission(btn.dataset.slotKey));
  }});
}}

function renderSlotCard(slot) {{
  const m = slot.mission || {{}};
  const failure = slot.failure || {{}};
  const title = slot.status === 'saved' ? m.title : (failure.reason_code || slot.status);
  const detail = slot.status === 'saved' ? (m.mission_id || '') : (failure.message || failure.run_status_path || 'No mission output saved.');
  const attrs = slot.status === 'saved' ? `data-slot-key="${{esc(slot.slot_key)}}"` : 'aria-disabled="true"';
  return `
    <button class="slot-card ${{esc(slot.status)}}" id="slot-${{esc(slot.slot_key)}}" ${{attrs}}>
      <div class="slot-top"><span class="slot-diff">${{esc(slot.difficulty_label || slot.difficulty_code)}}</span><span class="slot-status">${{esc(slot.status)}}</span></div>
      <div class="slot-title">${{esc(title)}}</div>
      <div class="slot-reason">${{esc(detail)}}</div>
    </button>
  `;
}}

function moveMission(delta) {{
  const saved = savedSlots();
  if (!saved.length) return;
  const index = Math.max(0, saved.findIndex(slot => slot.slot_key === currentSlotKey));
  const next = saved[(index + delta + saved.length) % saved.length];
  renderMission(next.slot_key);
}}

function renderMission(slotKey) {{
  const slot = SLOTS.find(item => item.slot_key === slotKey);
  if (!slot || slot.status !== 'saved' || !slot.mission) return;
  currentSlotKey = slotKey;
  activeMaterial = 0;
  const m = slot.mission;
  document.querySelectorAll('.slot-card').forEach(el => el.classList.remove('active'));
  const card = document.getElementById('slot-' + currentSlotKey);
  if (card) card.classList.add('active');
  $('timerVal').textContent = `${{String(m.difficulty?.estimated_time_minutes || 15).padStart(2,'0')}}:00`;
  renderHeader(m);
  renderMaterials(m);
  renderTasks(m);
  renderSide(m);
}}

function renderEmptyState() {{
  $('missionHeader').innerHTML = '<div class="empty">No saved mission is available for this run.</div>';
  $('materialTabs').innerHTML = '';
  $('materialBody').innerHTML = '';
  $('taskList').innerHTML = '';
  $('submissionBox').innerHTML = '';
  $('rubricBox').innerHTML = '';
  $('reliabilityBox').innerHTML = '';
}}

function renderHeader(m) {{
  const scenario = m.scenario || {{}};
  $('missionHeader').innerHTML = `
    <div class="breadcrumb">${{esc(label(m))}} <span class="muted">/ ${{esc(m.mission_id)}}</span></div>
    <div class="pill-row">
      <span class="chip a">${{esc(m.task_type)}}</span>
      ${{(m.secondary_task_types || []).map(t => `<span class="chip">${{esc(t)}}</span>`).join('')}}
      <span class="chip">score ${{esc(m.reliability?.score)}}</span>
    </div>
    <h2>${{esc(m.title)}}</h2>
    <p class="scenario"><b>${{esc(scenario.role)}}</b><br>${{esc(scenario.context)}}</p>
    <p class="scenario">${{esc(scenario.goal)}}</p>
    <ul class="constraints">${{(scenario.constraints || []).map(c => `<li>${{esc(c)}}</li>`).join('')}}</ul>
    ${{renderGlossary(scenario.glossary)}}
  `;
}}

function renderGlossary(items) {{
  if (!Array.isArray(items) || !items.length) return '';
  return `<div class="glossary"><div class="glossary-title">용어 정리</div><div class="glossary-list">${{items.map(item => `<div class="glossary-card"><b>${{esc(item.term)}}</b><div>${{esc(item.definition)}}</div></div>`).join('')}}</div></div>`;
}}

function renderMaterials(m) {{
  const materials = m.materials || [];
  $('materialTabs').innerHTML = materials.map((mat, i) => `<button class="tab ${{i===0?'active':''}}" onclick="selectMaterial(${{i}})">${{esc(mat.material_id)}} · ${{esc(mat.type)}}</button>`).join('');
  $('materialBody').innerHTML = materials.map((mat, i) => `<div class="material ${{i===0?'active':''}}" id="mat-${{i}}">${{renderMaterial(mat)}}</div>`).join('');
}}

function selectMaterial(i) {{
  activeMaterial = i;
  document.querySelectorAll('.tab').forEach((el, idx) => el.classList.toggle('active', idx === i));
  document.querySelectorAll('.material').forEach((el, idx) => el.classList.toggle('active', idx === i));
}}

function renderMaterial(mat) {{
  return `
    <div class="mat-head">
      <div><div class="mat-title">${{esc(mat.title)}}</div><div class="mat-desc">${{esc(mat.description)}}</div></div>
      <div class="type">${{esc(mat.type)}} / ${{esc(mat.subtype)}}</div>
    </div>
    ${{renderMaterialData(mat)}}
    <div class="refs">${{(mat.evidence_source || []).map(e => `<span class="ref">${{esc(e)}}</span>`).join('')}}</div>
  `;
}}

function renderMaterialData(mat) {{
  const d = mat.data || {{}};
  if (mat.type === 'chart') return renderChart(d);
  if (mat.type === 'table') return renderTable(d);
  if (mat.type === 'memo') return renderMemo(d);
  if (mat.type === 'email') return renderEmail(d);
  if (mat.type === 'log') return renderLog(d);
  if (mat.type === 'checklist') return renderChecklist(d);
  if (mat.type === 'schedule') return renderSchedule(d);
  return `<div class="empty">지원되지 않는 자료 유형입니다.</div>`;
}}

function renderChart(d) {{
  const values = (d.series || []).flatMap(s => s.values || []);
  if (!values.length) return '<div class="empty">차트 데이터 없음</div>';
  const min = Math.min(...values), max = Math.max(...values), span = Math.max(max - min, 1);
  const xVals = d.x_axis?.values || [];
  const colors = ['#5e6ad2', '#47c07b'];
  const series = (d.series || []).map((s, si) => {{
    const pts = (s.values || []).map((v, i) => {{
      const x = 48 + (i * (640 / Math.max((s.values || []).length - 1, 1)));
      const y = 196 - ((v - min) / span) * 150;
      return `${{x}},${{y}}`;
    }}).join(' ');
    return `<polyline points="${{pts}}" fill="none" stroke="${{colors[si % colors.length]}}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
  }}).join('');
  const labels = xVals.map((x, i) => `<text x="${{48 + (i * (640 / Math.max(xVals.length - 1, 1)))}}" y="228" text-anchor="middle" fill="#8c929c" font-size="12">${{esc(x)}}</text>`).join('');
  const legend = (d.series || []).map((s, i) => `<span class="ref" style="border-color:${{colors[i % colors.length]}}">${{esc(s.name)}}</span>`).join('');
  return `<div class="chart"><svg viewBox="0 0 736 248" role="img" aria-label="chart"><line x1="48" y1="200" x2="688" y2="200" stroke="rgba(255,255,255,.12)"/><line x1="48" y1="36" x2="48" y2="200" stroke="rgba(255,255,255,.12)"/>${{series}}${{labels}}</svg></div><div class="refs">${{legend}}</div>`;
}}

function renderTable(d) {{
  const cols = d.columns || [];
  const rows = d.rows || [];
  if (!cols.length || !rows.length) return '<div class="empty">표 데이터 없음</div>';
  return `<table><thead><tr>${{cols.map(c => `<th>${{esc(c.label || c.key)}}</th>`).join('')}}</tr></thead><tbody>${{rows.map(r => `<tr>${{cols.map(c => `<td class="${{typeof r[c.key] === 'number' ? 'num' : ''}}">${{esc(r[c.key])}}</td>`).join('')}}</tr>`).join('')}}</tbody></table>`;
}}

function renderMemo(d) {{
  const items = d.items || [];
  return `<div class="card-list">${{items.map(it => `<div class="info-card"><b>${{esc(it.label || it.period || 'memo')}}</b><div>${{esc(it.text || it.task)}}</div>${{it.constraint ? `<div class="muted">제약: ${{esc(it.constraint)}}</div>` : ''}}</div>`).join('') || '<div class="empty">메모 없음</div>'}}</div>`;
}}

function renderEmail(d) {{
  const thread = d.thread || [];
  return `<div class="card-list">${{thread.map(mail => `<div class="info-card"><b>${{esc(mail.subject)}}</b><div class="muted">${{esc(mail.from)}} → ${{esc(mail.to)}}</div><p>${{nl(mail.body)}}</p></div>`).join('') || '<div class="empty">이메일 없음</div>'}}</div>`;
}}

function renderLog(d) {{
  const entries = d.entries || [];
  return `<div class="timeline">${{entries.map(e => `<div class="item"><b>${{esc(e.time)}} · ${{esc(e.actor)}}</b><br>${{esc(e.event)}}<div class="muted">${{esc(e.note)}}</div></div>`).join('') || '<div class="empty">로그 없음</div>'}}</div>`;
}}

function renderChecklist(d) {{
  const items = d.items || [];
  const statusLabel = status => status === 'checked' ? '확인됨' : status === 'issue' ? '주의' : '미확인';
  return `<div class="card-list">${{items.map(it => {{
    const meta = [...new Set([it.label, statusLabel(it.status), it.importance].filter(Boolean))].join(' · ');
    return `<div class="info-card check-row"><span class="check-dot">${{it.status === 'checked' ? '✓' : it.status === 'issue' ? '!' : '-'}}</span><div><b>${{esc(it.text || it.label || '체크 항목')}}</b>${{meta ? `<div class="muted">메타: ${{esc(meta)}}</div>` : ''}}${{it.constraint ? `<div class="muted">제약: ${{esc(it.constraint)}}</div>` : ''}}</div></div>`;
  }}).join('') || '<div class="empty">체크리스트 없음</div>'}}</div>`;
}}

function renderSchedule(d) {{
  const items = d.items || [];
  const hasTable = (d.columns || []).length && (d.rows || []).length;
  const table = hasTable ? renderTable(d) : '';
  const timeline = items.length ? `<div class="timeline">${{items.map(it => {{
    const title = it.text || [it.period, it.task].filter(Boolean).join(' · ') || it.label || '일정';
    const meta = [...new Set([it.label, it.period, it.task, it.status, it.importance].filter(Boolean))].join(' · ');
    return `<div class="item"><b>${{esc(title)}}</b>${{meta ? `<div class="muted">메타: ${{esc(meta)}}</div>` : ''}}${{it.constraint ? `<div class="muted">제약: ${{esc(it.constraint)}}</div>` : ''}}</div>`;
  }}).join('')}}</div>` : '';
  return table || timeline ? `${{table}}${{timeline}}` : '<div class="empty">일정 없음</div>';
}}

function renderTasks(m) {{
  $('taskList').innerHTML = (m.tasks || []).map((t, i) => `
    <div class="task">
      <div class="task-meta">TASK ${{i + 1}} / ${{esc(t.task_id || '')}}</div>
      <div class="task-instruction">${{esc(t.instruction)}}</div>
      <div class="refs">${{(t.required_materials || []).map(id => `<span class="ref">${{esc(id)}}</span>`).join('')}}<span class="ref">${{esc(t.expected_action)}}</span></div>
      <textarea class="task-answer" data-task-id="${{esc(t.task_id || ('task_' + (i + 1)))}}" placeholder="이 task에 대한 답변을 작성하세요."></textarea>
      <div class="char task-char">0자</div>
    </div>
  `).join('');
  document.querySelectorAll('.task-answer').forEach(area => {{
    const counter = area.parentElement.querySelector('.task-char');
    area.addEventListener('input', () => {{
      counter.textContent = `${{area.value.length}}자`;
    }});
  }});
}}

function renderSide(m) {{
  const sf = m.submission_format || {{}};
  $('submissionBox').innerHTML = `
    <div>${{esc(sf.type)}} · ${{esc(sf.estimated_time_minutes)}}분</div>
    <div>${{esc(sf.length_hint)}}</div>
    <div class="refs">${{(sf.required_sections || []).map(s => `<span class="ref">${{esc(s)}}</span>`).join('')}}</div>
  `;
  const rubric = m.evaluation?.rubric || [];
  $('rubricBox').innerHTML = rubric.map(r => `<div class="rubric-row"><span>${{esc(r.criterion)}}</span><span>${{esc(r.points)}}점</span></div>`).join('');
  const rel = m.reliability || {{}};
  $('reliabilityBox').innerHTML = `<span class="score">${{esc(rel.score)}}</span> <span class="muted">warnings ${{esc(rel.warning_count)}} · repair ${{esc(rel.repair_count)}}</span>`;
}}

init();
</script>
</body>
</html>
"""

    def _render_learner_html(self, payload: dict[str, Any]) -> str:
        """학습자용 HTML을 self-contained 정적 파일 문자열로 렌더링한다."""

        encoded_payload = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JOBSIM 학습자 과제</title>
<style>
:root {{
  --bg:#f6f7f9; --surface:#ffffff; --surface2:#f0f3f6; --line:#dfe4ea;
  --text:#1b1f24; --muted:#68727f; --soft:#8b96a3;
  --accent:#2f6f6d; --accent2:#e6f2f1; --accent3:#b9d8d5;
  --focus:#315eea;
}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;-webkit-font-smoothing:antialiased}}
button,textarea{{font:inherit}} button{{cursor:pointer}} button:focus-visible,textarea:focus-visible{{outline:3px solid rgba(49,94,234,.25);outline-offset:2px}}
.topbar{{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.92);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}}
.topbar-inner{{max-width:1120px;margin:0 auto;padding:15px 22px;display:flex;align-items:center;gap:16px}}
.brand{{font-weight:700}} .brand span{{color:var(--accent)}} .current-meta{{margin-left:auto;color:var(--muted);font-size:13px}}
.wrap{{max-width:1120px;margin:0 auto;padding:30px 22px 64px}}
.intro{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:end;margin-bottom:22px}}
.eyebrow{{font-size:12px;color:var(--accent);font-weight:700;margin-bottom:8px}} h1{{margin:0;font-size:34px;line-height:1.18;font-weight:720}} .intro p{{margin:8px 0 0;color:var(--muted);max-width:680px}}
.actions{{display:flex;gap:8px}} .btn{{border:1px solid var(--line);background:var(--surface);border-radius:8px;padding:9px 13px;color:var(--text)}} .btn.primary{{background:var(--accent);border-color:var(--accent);color:white}}
.mission-picker{{margin-bottom:20px}} .picker-title{{font-weight:700;margin-bottom:10px}} .mission-grid{{display:grid;gap:18px}}
.mission-job-group{{border-top:1px solid var(--line);padding-top:15px}} .mission-job-group:first-child{{border-top:0;padding-top:0}} .mission-job-head{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:9px}} .mission-job-name{{font-weight:720}} .mission-job-count{{color:var(--muted);font-size:12px;white-space:nowrap}} .mission-job-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}
.mission-option{{border:1px solid var(--line);background:var(--surface);border-radius:8px;padding:14px;text-align:left;min-height:104px;display:flex;flex-direction:column;gap:8px;transition:.15s}}
.mission-option:hover{{border-color:var(--accent3)}} .mission-option.active{{border-color:var(--accent);background:var(--accent2)}} .option-meta{{color:var(--muted);font-size:12px}} .option-title{{font-weight:700;line-height:1.45}}
.workspace{{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px;align-items:start}} .main{{display:grid;gap:14px;min-width:0}} .section,.side{{background:var(--surface);border:1px solid var(--line);border-radius:8px;min-width:0}}
.section{{padding:24px}} .side{{position:sticky;top:82px;padding:20px;display:grid;gap:18px}} h2{{margin:0 0 12px;font-size:28px;line-height:1.25}} h3{{margin:0 0 14px;font-size:18px}} .scenario{{color:var(--muted);margin:0 0 12px}}
.chip-row{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}} .chip{{display:inline-flex;border:1px solid var(--line);background:var(--surface2);border-radius:999px;padding:4px 10px;color:var(--muted);font-size:12px}} .chip.accent{{color:var(--accent);background:var(--accent2);border-color:var(--accent3)}}
.constraints{{display:grid;gap:8px;list-style:none;padding:0;margin:14px 0 0}} .constraints li{{background:var(--surface2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;color:var(--muted)}} .glossary{{margin:16px 0 0}} .glossary-title{{font-size:13px;color:var(--accent);font-weight:700;margin-bottom:8px}} .glossary-list{{display:grid;gap:8px}} .glossary-card{{background:#fbfcfd;border:1px solid var(--line);border-radius:8px;padding:12px;color:var(--muted)}} .glossary-card b{{display:block;color:var(--text);margin-bottom:4px}}
.material-tabs{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:14px}} .tab{{border:1px solid var(--line);background:var(--surface2);border-radius:999px;padding:7px 11px;color:var(--muted);font-size:13px}} .tab.active{{background:var(--accent);border-color:var(--accent);color:white}}
.material{{display:none;min-width:0}} .material.active{{display:block}} .mat-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:14px}} .mat-label{{color:var(--accent);font-weight:700;font-size:13px}} .mat-title{{font-weight:720;margin-top:2px}} .mat-desc{{color:var(--muted);font-size:13px;margin-top:5px}} .type-label{{color:var(--muted);font-size:12px;white-space:nowrap}}
.table-scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:8px;max-width:100%;min-width:0}} table{{width:100%;min-width:640px;border-collapse:collapse;font-size:13px}} th{{background:var(--surface2);text-align:left;color:var(--muted);padding:10px 12px;border-bottom:1px solid var(--line)}} td{{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}} tr:last-child td{{border-bottom:0}} .num{{text-align:right;color:var(--accent);font-weight:700}}
.chart{{height:260px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,#fff,#f8fafb);padding:10px}} .chart svg{{width:100%;height:100%;display:block}}
.card-list{{display:grid;gap:9px}} .info-card{{border:1px solid var(--line);background:var(--surface2);border-radius:8px;padding:13px}} .info-card b{{display:block;margin-bottom:4px}} .muted{{color:var(--muted)}} .timeline{{display:grid;gap:10px}} .timeline .item{{border-left:3px solid var(--accent);padding:3px 0 8px 12px;color:var(--muted)}} .check-row{{display:flex;gap:9px;align-items:flex-start}} .check-dot{{width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:var(--accent2);color:var(--accent);font-size:12px;flex:0 0 auto;margin-top:2px}}
.task-list{{display:grid;gap:12px}} .task{{border:1px solid var(--line);background:#fbfcfd;border-radius:8px;padding:15px}} .task-label{{color:var(--accent);font-size:13px;font-weight:700;margin-bottom:7px}} .task-instruction{{font-weight:620}} .refs{{margin-top:9px;display:flex;gap:6px;flex-wrap:wrap}} .ref{{border:1px solid var(--line);background:var(--surface);border-radius:999px;color:var(--muted);font-size:12px;padding:3px 8px}}
textarea{{width:100%;min-height:150px;margin-top:12px;border:1px solid var(--line);background:white;border-radius:8px;padding:12px;resize:vertical;color:var(--text)}} .char{{text-align:right;color:var(--soft);font-size:12px;margin-top:4px}} .side-label{{font-size:12px;color:var(--muted);font-weight:700;margin-bottom:5px}} .timer{{font-size:34px;line-height:1;font-weight:720;color:var(--accent)}} .criteria{{display:grid;gap:8px;margin:0;padding:0;list-style:none}} .criteria li{{padding:9px 11px;border:1px solid var(--line);border-radius:8px;background:var(--surface2);color:var(--muted)}} .empty{{color:var(--muted);padding:18px}}
@media (max-width:900px){{.intro,.workspace{{grid-template-columns:minmax(0,1fr)}} .side{{position:static}} .mission-job-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media (max-width:760px){{.mission-job-grid{{grid-template-columns:1fr}} .mission-job-head{{align-items:flex-start;flex-direction:column;gap:2px}}}}
@media (max-width:620px){{.topbar-inner{{align-items:flex-start;flex-direction:column;padding:13px 16px}} .current-meta{{margin-left:0}} .wrap{{padding:22px 14px 48px}} h1{{font-size:29px}} h2{{font-size:24px}} .section{{padding:18px}} .intro{{gap:14px}} .actions{{width:100%}} .btn{{flex:1}} table{{min-width:620px}}}}
</style>
</head>
<body>
<nav class="topbar">
  <div class="topbar-inner">
    <div class="brand">JOB<span>SIM</span> 과제</div>
    <div class="current-meta" id="currentMeta"></div>
  </div>
</nav>
<main class="wrap">
  <header class="intro">
    <div>
      <div class="eyebrow">학습자 미션</div>
      <h1>자료를 읽고 과제를 해결해보세요</h1>
      <p>직무 상황에 맞춰 제공된 자료를 확인하고, 과제별 답변을 작성합니다.</p>
    </div>
    <div class="actions">
      <button class="btn" id="prevBtn">이전 미션</button>
      <button class="btn primary" id="nextBtn">다음 미션</button>
    </div>
  </header>
  <section class="mission-picker">
    <div class="picker-title">미션 선택</div>
    <div class="mission-grid" id="missionGrid"></div>
  </section>
  <section class="workspace" id="missionDetail">
    <div class="main">
      <article class="section" id="missionHeader"></article>
      <article class="section">
        <h3>제공 자료</h3>
        <div class="material-tabs" id="materialTabs"></div>
        <div id="materialBody"></div>
      </article>
      <article class="section">
        <h3>수행 과제</h3>
        <div class="task-list" id="taskList"></div>
      </article>
    </div>
    <aside class="side">
      <div><div class="side-label">제한 시간</div><div class="timer" id="timerVal">15분</div></div>
      <div><div class="side-label">제출 형식</div><div id="submissionBox" class="muted"></div></div>
      <div><div class="side-label">평가 기준</div><ul class="criteria" id="criteriaBox"></ul></div>
    </aside>
  </section>
</main>
<script id="learnerPayload" type="application/json">{encoded_payload}</script>
<script>
const DATA = JSON.parse(document.getElementById('learnerPayload').textContent);
const MISSIONS = DATA.missions || [];
let currentIndex = 0;
let activeMaterial = 0;

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
const nl = value => esc(value).replace(/\\n/g, '<br>');

function init() {{
  renderMissionGroups();
  if (MISSIONS.length) renderMission(0, false);
  else renderEmptyState();
  $('prevBtn').onclick = () => moveMission(-1);
  $('nextBtn').onclick = () => moveMission(1);
}}

function moveMission(delta) {{
  if (!MISSIONS.length) return;
  const next = (currentIndex + delta + MISSIONS.length) % MISSIONS.length;
  renderMission(next, true);
}}

function renderMissionGroups() {{
  const groups = missionGroups();
  $('missionGrid').innerHTML = groups.map(group => `
    <section class="mission-job-group">
      <div class="mission-job-head">
        <div class="mission-job-name">${{esc(group.jobName)}}</div>
        <div class="mission-job-count">${{group.items.length}}개 미션</div>
      </div>
      <div class="mission-job-grid">
        ${{group.items.map(item => `
          <button class="mission-option" data-index="${{item.index}}">
            <span class="option-meta">${{esc(item.mission.difficulty_label)}}${{item.mission.task_type_label ? ` · ${{esc(item.mission.task_type_label)}}` : ''}}</span>
            <span class="option-title">${{esc(item.mission.title)}}</span>
          </button>
        `).join('')}}
      </div>
    </section>
  `).join('');
  document.querySelectorAll('.mission-option').forEach(button => {{
    button.addEventListener('click', () => renderMission(Number(button.dataset.index), true));
  }});
}}

function missionGroups() {{
  const groups = [];
  const byJob = new Map();
  MISSIONS.forEach((mission, index) => {{
    const jobName = mission.job_name || '직무 미지정';
    if (!byJob.has(jobName)) {{
      const group = {{ jobName, items: [] }};
      byJob.set(jobName, group);
      groups.push(group);
    }}
    byJob.get(jobName).items.push({{ mission, index }});
  }});
  return groups;
}}

function renderMission(index, shouldScroll) {{
  const mission = MISSIONS[index];
  if (!mission) return;
  currentIndex = index;
  activeMaterial = 0;
  document.querySelectorAll('.mission-option').forEach(button => {{
    button.classList.toggle('active', Number(button.dataset.index) === index);
  }});
  $('currentMeta').textContent = `${{mission.job_name || ''}} · ${{mission.difficulty_label || ''}}`;
  $('timerVal').textContent = `${{mission.time_limit_minutes || 15}}분`;
  renderHeader(mission);
  renderMaterials(mission);
  renderTasks(mission);
  renderSide(mission);
  if (shouldScroll && window.matchMedia('(max-width: 760px)').matches) {{
    $('missionDetail').scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }}
}}

function renderEmptyState() {{
  $('currentMeta').textContent = '';
  $('missionGrid').innerHTML = '<div class="empty">선택할 수 있는 미션이 없습니다.</div>';
  $('missionHeader').innerHTML = '<div class="empty">선택할 수 있는 미션이 없습니다.</div>';
  $('materialTabs').innerHTML = '';
  $('materialBody').innerHTML = '';
  $('taskList').innerHTML = '';
  $('submissionBox').innerHTML = '';
  $('criteriaBox').innerHTML = '';
}}

function renderHeader(mission) {{
  const scenario = mission.scenario || {{}};
  const typeChips = [mission.task_type_label, ...(mission.secondary_task_type_labels || [])]
    .filter(Boolean)
    .map(label => `<span class="chip">${{esc(label)}}</span>`)
    .join('');
  $('missionHeader').innerHTML = `
    <div class="chip-row">
      <span class="chip accent">${{esc(mission.job_name)}}</span>
      <span class="chip accent">${{esc(mission.difficulty_label)}}</span>
      ${{typeChips}}
    </div>
    <h2>${{esc(mission.title)}}</h2>
    <p class="scenario"><b>${{esc(scenario.role)}}</b><br>${{esc(scenario.context)}}</p>
    <p class="scenario">${{esc(scenario.goal)}}</p>
    <ul class="constraints">${{(scenario.constraints || []).map(item => `<li>${{esc(item)}}</li>`).join('')}}</ul>
    ${{renderGlossary(scenario.glossary)}}
  `;
}}

function renderGlossary(items) {{
  if (!Array.isArray(items) || !items.length) return '';
  return `<div class="glossary"><div class="glossary-title">용어 정리</div><div class="glossary-list">${{items.map(item => `<div class="glossary-card"><b>${{esc(item.term)}}</b><div>${{esc(item.definition)}}</div></div>`).join('')}}</div></div>`;
}}

function renderMaterials(mission) {{
  const materials = mission.materials || [];
  $('materialTabs').innerHTML = materials.map((mat, index) => `
    <button class="tab ${{index === 0 ? 'active' : ''}}" onclick="selectMaterial(${{index}})">
      ${{esc(mat.label)}} · ${{esc(mat.type_label)}}
    </button>
  `).join('');
  $('materialBody').innerHTML = materials.map((mat, index) => `
    <div class="material ${{index === 0 ? 'active' : ''}}" id="mat-${{index}}">${{renderMaterial(mat)}}</div>
  `).join('');
}}

function selectMaterial(index) {{
  activeMaterial = index;
  document.querySelectorAll('.tab').forEach((el, idx) => el.classList.toggle('active', idx === index));
  document.querySelectorAll('.material').forEach((el, idx) => el.classList.toggle('active', idx === index));
}}

function renderMaterial(mat) {{
  return `
    <div class="mat-head">
      <div>
        <div class="mat-label">${{esc(mat.label)}} · ${{esc(mat.type_label)}}</div>
        <div class="mat-title">${{esc(mat.title)}}</div>
        <div class="mat-desc">${{esc(mat.description)}}</div>
      </div>
    </div>
    ${{renderMaterialData(mat)}}
  `;
}}

function renderMaterialData(mat) {{
  const data = mat.data || {{}};
  if (mat.type === 'chart') return renderChart(data);
  if (mat.type === 'table') return renderTable(data);
  if (mat.type === 'memo') return renderMemo(data);
  if (mat.type === 'email') return renderEmail(data);
  if (mat.type === 'log') return renderLog(data);
  if (mat.type === 'checklist') return renderChecklist(data);
  if (mat.type === 'schedule') return renderSchedule(data);
  return '<div class="empty">표시할 자료가 없습니다.</div>';
}}

function renderChart(data) {{
  const values = (data.series || []).flatMap(series => series.values || []);
  if (!values.length) return '<div class="empty">차트 데이터가 없습니다.</div>';
  const min = Math.min(...values), max = Math.max(...values), span = Math.max(max - min, 1);
  const xVals = data.x_axis?.values || [];
  const colors = ['#2f6f6d', '#315eea'];
  const series = (data.series || []).map((item, seriesIndex) => {{
    const points = (item.values || []).map((value, valueIndex) => {{
      const x = 48 + (valueIndex * (640 / Math.max((item.values || []).length - 1, 1)));
      const y = 196 - ((value - min) / span) * 150;
      return `${{x}},${{y}}`;
    }}).join(' ');
    return `<polyline points="${{points}}" fill="none" stroke="${{colors[seriesIndex % colors.length]}}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
  }}).join('');
  const labels = xVals.map((label, index) => `<text x="${{48 + (index * (640 / Math.max(xVals.length - 1, 1)))}}" y="228" text-anchor="middle" fill="#68727f" font-size="12">${{esc(label)}}</text>`).join('');
  const legend = (data.series || []).map((item, index) => `<span class="ref" style="border-color:${{colors[index % colors.length]}}">${{esc(item.name)}}</span>`).join('');
  return `<div class="chart"><svg viewBox="0 0 736 248" role="img" aria-label="차트"><line x1="48" y1="200" x2="688" y2="200" stroke="#dfe4ea"/><line x1="48" y1="36" x2="48" y2="200" stroke="#dfe4ea"/>${{series}}${{labels}}</svg></div><div class="refs">${{legend}}</div>`;
}}

function renderTable(data) {{
  const cols = data.columns || [];
  const rows = data.rows || [];
  if (!cols.length || !rows.length) return '<div class="empty">표 데이터가 없습니다.</div>';
  return `<div class="table-scroll"><table><thead><tr>${{cols.map(col => `<th>${{esc(col.label || col.key)}}</th>`).join('')}}</tr></thead><tbody>${{rows.map(row => `<tr>${{cols.map(col => `<td class="${{typeof row[col.key] === 'number' ? 'num' : ''}}">${{esc(row[col.key])}}</td>`).join('')}}</tr>`).join('')}}</tbody></table></div>`;
}}

function renderMemo(data) {{
  const items = data.items || [];
  return `<div class="card-list">${{items.map(item => `<div class="info-card"><b>${{esc(item.label || item.period || '메모')}}</b><div>${{esc(item.text || item.task)}}</div></div>`).join('') || '<div class="empty">메모가 없습니다.</div>'}}</div>`;
}}

function renderEmail(data) {{
  const thread = data.thread || [];
  return `<div class="card-list">${{thread.map(mail => `<div class="info-card"><b>${{esc(mail.subject)}}</b><div class="muted">${{esc(mail.from)}} → ${{esc(mail.to)}}</div><p>${{nl(mail.body)}}</p></div>`).join('') || '<div class="empty">이메일이 없습니다.</div>'}}</div>`;
}}

function renderLog(data) {{
  const entries = data.entries || [];
  return `<div class="timeline">${{entries.map(entry => `<div class="item"><b>${{esc(entry.time)}} · ${{esc(entry.actor)}}</b><br>${{esc(entry.event)}}<div class="muted">${{esc(entry.note)}}</div></div>`).join('') || '<div class="empty">로그가 없습니다.</div>'}}</div>`;
}}

function renderChecklist(data) {{
  const items = data.items || [];
  return `<div class="card-list">${{items.map(item => `<div class="info-card"><b>${{esc(item.text || item.label || '체크 항목')}}</b></div>`).join('') || '<div class="empty">체크리스트가 없습니다.</div>'}}</div>`;
}}

function renderSchedule(data) {{
  const items = data.items || [];
  const hasTable = (data.columns || []).length && (data.rows || []).length;
  const table = hasTable ? renderTable(data) : '';
  const timeline = items.length ? `<div class="timeline">${{items.map(item => {{
    const title = item.text || [item.period, item.task].filter(Boolean).join(' · ') || item.label || '일정';
    return `<div class="item"><b>${{esc(title)}}</b>${{item.constraint ? `<div class="muted">${{esc(item.constraint)}}</div>` : ''}}</div>`;
  }}).join('')}}</div>` : '';
  return table || timeline ? `${{table}}${{timeline}}` : '<div class="empty">일정이 없습니다.</div>';
}}

function renderTasks(mission) {{
  $('taskList').innerHTML = (mission.tasks || []).map(task => `
    <div class="task">
      <div class="task-label">${{esc(task.label)}}</div>
      <div class="task-instruction">${{esc(task.instruction)}}</div>
      <div class="refs">${{(task.material_labels || []).map(label => `<span class="ref">${{esc(label)}}</span>`).join('')}}</div>
      <textarea class="answer-input" placeholder="답변을 작성하세요."></textarea>
      <div class="char answer-char">0자</div>
    </div>
  `).join('');
  document.querySelectorAll('.task textarea').forEach(area => {{
    const counter = area.parentElement.querySelector('.char');
    area.addEventListener('input', () => {{
      counter.textContent = `${{area.value.length}}자`;
    }});
  }});
}}

function renderSide(mission) {{
  const submission = mission.submission_format || {{}};
  $('submissionBox').innerHTML = `
    <div>${{esc(submission.type_label)}} · ${{esc(submission.estimated_time_minutes)}}분</div>
    <div>${{esc(submission.length_hint_label)}}</div>
    <div class="refs">${{(submission.required_sections || []).map(section => `<span class="ref">${{esc(section)}}</span>`).join('')}}</div>
  `;
  $('criteriaBox').innerHTML = (mission.evaluation?.criteria || []).map(item => `<li>${{esc(item)}}</li>`).join('');
}}

init();
</script>
</body>
</html>
"""


def main() -> None:
    """CLI에서 review/learner/both HTML export를 실행하는 진입점."""

    parser = argparse.ArgumentParser(description="Export mission outputs to a single static HTML UI.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--pilot-run-dir", default=None)
    parser.add_argument("--ui-output-dir", default=None)
    parser.add_argument("--view", choices=["review", "learner", "both"], default="review")
    args = parser.parse_args()
    exporter = MissionUIExporter()
    output_paths: list[Path] = []
    if args.view in {"review", "both"}:
        output_paths.append(
            exporter.export(
                run_id=args.run_id,
                pilot_run_dir=args.pilot_run_dir,
                ui_output_dir=args.ui_output_dir,
            )
        )
    if args.view in {"learner", "both"}:
        output_paths.append(
            exporter.export_learner(
                run_id=args.run_id,
                pilot_run_dir=args.pilot_run_dir,
                ui_output_dir=args.ui_output_dir,
            )
        )
    for output_path in output_paths:
        print(output_path.as_posix())


if __name__ == "__main__":
    main()
