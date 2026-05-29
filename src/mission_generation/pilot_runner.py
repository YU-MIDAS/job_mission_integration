# 직무 profile 로드부터 LLM 생성, 검증, 저장까지 pilot run 전체를 실행한다.

from __future__ import annotations

import argparse
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .config import PILOT_JOB_CONFIGS, RuntimeConfig, default_pilot_config
from .decision_selector import DecisionSelectorInputBuilder, DecisionSelectorValidator, MissionDecisionSelector
from .draft_generator import LLMInputPackageBuilder, MissionDraftGenerator
from .final_assembler import FinalMissionAssembler
from .mission_seed_builder import MissionSeedBuilder
from .practice_profile_loader import PracticeProfileLoader
from .practice_sheet_background_loader import PracticeSheetBackgroundLoader
from .progress_reporter import ConsoleProgressReporter
from .profile_loader import JobProfileLoader, ProfileLoadError
from .repair_manager import RepairManager, RepairPromptBuilder
from .schema_constraints_builder import SchemaConstraintsBuilder
from .storage import StorageAdapter
from .system_decision_builder import SystemDecisionBuilder
from .utils import iso_now, normalize_text
from .validator import MissionValidator


class DecisionSelectionError(RuntimeError):
    """LLM selector가 실제 후보를 벗어나 기본 생성을 진행할 수 없을 때의 오류."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "DECISION_SELECTOR_FAILED",
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.errors = errors or []


class PilotRunner:
    """profile 로드부터 LLM 생성, validator, 저장까지 한 target run을 순서대로 실행한다."""

    def __init__(
        self,
        *,
        source_root: str | Path = "data/api_raw",
        output_root: str | Path = "outputs",
        force_mock: bool = False,
        allow_mock_fallback: bool = False,
        concurrency: int = 2,
        target_job_codes: list[str] | None = None,
        target_difficulty_codes: list[str] | None = None,
        use_llm_decision_selector: bool = True,
        use_practice_sheet_background: bool = True,
        practice_sheet_root: str | Path = "data/additional_search",
        progress_enabled: bool = False,
    ) -> None:
        self.source_root = Path(source_root)
        self.output_root = Path(output_root)
        self.force_mock = force_mock
        self.allow_mock_fallback = allow_mock_fallback
        self.concurrency = max(1, int(concurrency))
        self.target_job_codes = list(target_job_codes) if target_job_codes is not None else None
        self.target_difficulty_codes = list(target_difficulty_codes) if target_difficulty_codes is not None else None
        self.use_llm_decision_selector = bool(use_llm_decision_selector)
        self.use_practice_sheet_background = bool(use_practice_sheet_background)
        self.progress = ConsoleProgressReporter(enabled=progress_enabled)
        self._progress_total_targets = 0
        self._progress_positions: dict[tuple[str, str], int] = {}
        self.runtime_config = RuntimeConfig()
        self.profile_loader = JobProfileLoader(source_root=self.source_root, output_root=self.output_root / "profiles" / "v1")
        self.practice_profile_loader = PracticeProfileLoader()
        self.practice_sheet_background_loader = PracticeSheetBackgroundLoader(root=practice_sheet_root)
        self.seed_builder = MissionSeedBuilder()
        self.decision_builder = SystemDecisionBuilder()
        self.selector_input_builder = DecisionSelectorInputBuilder()
        self.decision_selector = MissionDecisionSelector(force_mock=force_mock)
        self.selector_validator = DecisionSelectorValidator()
        self.constraints_builder = SchemaConstraintsBuilder()
        self.input_builder = LLMInputPackageBuilder()
        self.draft_generator = MissionDraftGenerator(
            allow_mock_without_key=allow_mock_fallback,
            force_mock=force_mock,
        )
        self.repair_manager = RepairManager(
            allow_mock_without_key=allow_mock_fallback,
            force_mock=force_mock,
        )
        self.validator = MissionValidator()
        self.assembler = FinalMissionAssembler()
        self.storage = StorageAdapter(output_root=self.output_root)

    def run(self) -> dict[str, Any]:
        """설정된 직무/난이도 target 전체를 실행하고 pilot_summary를 반환한다."""

        started_at = iso_now()
        started_monotonic = time.monotonic()
        pilot_config = self._pilot_config()
        pilot_config["source_root"] = self.source_root.as_posix()
        pilot_config["concurrency"] = self.concurrency
        pilot_config["force_mock"] = self.force_mock
        pilot_config["allow_mock_fallback"] = self.allow_mock_fallback
        pilot_config["use_llm_decision_selector"] = self.use_llm_decision_selector
        pilot_config["use_practice_sheet_background"] = self.use_practice_sheet_background
        run_dir = self.storage.create_run(self.runtime_config, pilot_config)
        self._prepare_progress(pilot_config)
        self.progress.run_started(
            run_id=self.storage.run_id,
            total_targets=self._progress_total_targets,
            concurrency=self.concurrency,
        )
        results_by_order: dict[int, dict[str, Any]] = {}
        usage = self._empty_usage()
        targets: list[tuple[int, dict[str, Any], dict[str, str], dict[str, str]]] = []
        target_order = 0

        for job in pilot_config["jobs"]:
            job_cd = job["job_cd"]
            try:
                self.progress.emit(f"{job_cd} - profile loading")
                profile = self.profile_loader.build(job_cd, save=False)
                self.storage.save_canonical_profile(profile)
                self.storage.save_profile_snapshot(profile)
                self.progress.emit(f"{job_cd} - profile loaded")
            except ProfileLoadError as exc:
                self.progress.emit(f"{job_cd} - profile failed ({exc.errors[0]['code'] if exc.errors else 'PROFILE_FAILED'})")
                for difficulty in pilot_config["difficulties"]:
                    results_by_order[target_order] = self._save_failed_status(
                        job_cd=job_cd,
                        job_name=job["job_name"],
                        difficulty=difficulty,
                        status="profile_failed",
                        reason_code=exc.errors[0]["code"] if exc.errors else "PROFILE_FAILED",
                        error={"errors": exc.errors},
                        results=None,
                    )
                    target_order += 1
                continue

            for difficulty in pilot_config["difficulties"]:
                targets.append((target_order, profile, job, difficulty))
                target_order += 1

        if self.concurrency <= 1:
            for order, profile, job, difficulty in targets:
                try:
                    output = self._run_one_with_usage(profile, job, difficulty)
                except Exception as exc:
                    output = {
                        "result": self._save_failed_status(
                            job_cd=job["job_cd"],
                            job_name=job["job_name"],
                            difficulty=difficulty,
                            status="runner_failed",
                            reason_code="RUNNER_FAILED",
                            error={"message": str(exc)},
                            results=None,
                        ),
                        "usage": self._empty_usage(),
                    }
                results_by_order[order] = output["result"]
                self._merge_usage(usage, output["usage"])
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                future_targets = {
                    executor.submit(self._run_one_with_usage, profile, job, difficulty): (order, job, difficulty)
                    for order, profile, job, difficulty in targets
                }
                for future in as_completed(future_targets):
                    order, job, difficulty = future_targets[future]
                    try:
                        output = future.result()
                    except Exception as exc:
                        output = {
                            "result": self._save_failed_status(
                                job_cd=job["job_cd"],
                                job_name=job["job_name"],
                                difficulty=difficulty,
                                status="runner_failed",
                                reason_code="RUNNER_FAILED",
                                error={"message": str(exc)},
                                results=None,
                            ),
                            "usage": self._empty_usage(),
                        }
                    results_by_order[order] = output["result"]
                    self._merge_usage(usage, output["usage"])

        results = [results_by_order[idx] for idx in sorted(results_by_order)]
        self.storage.flush_indexes()
        finished_at = iso_now()
        duration_seconds = round(time.monotonic() - started_monotonic, 3)
        summary = self._summary(
            results,
            usage,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
        )
        self.storage.save_pilot_summary(summary)
        self.storage.write_human_review_templates(results)
        json_failures = self.storage.validate_json_outputs()
        secret_findings = self.storage.security_scan()
        summary["post_run_checks"] = {
            "json_parse_failures": json_failures,
            "secret_findings": secret_findings,
        }
        self.storage.save_pilot_summary(summary)
        summary["run_dir"] = run_dir.as_posix()
        self.progress.run_finished(
            saved_count=summary["saved_count"],
            failed_count=summary["failed_count"],
            repair_used_count=summary["repair_used_count"],
        )
        return summary

    def _prepare_progress(self, pilot_config: dict[str, Any]) -> None:
        """콘솔 진행 메시지에 표시할 target 순번과 전체 개수를 준비한다."""

        self._progress_positions = {}
        order = 1
        for job in pilot_config["jobs"]:
            for difficulty in pilot_config["difficulties"]:
                self._progress_positions[(job["job_cd"], difficulty["code"])] = order
                order += 1
        self._progress_total_targets = max(0, order - 1)

    def _pilot_config(self) -> dict[str, Any]:
        """기본 pilot 설정에 CLI/API로 지정한 직무와 난이도 필터를 반영한다."""

        base = default_pilot_config()
        pilot_config = {
            **base,
            "jobs": [dict(item) for item in base["jobs"]],
            "difficulties": [dict(item) for item in base["difficulties"]],
        }
        if self.target_job_codes is not None:
            requested = self._dedupe_codes(self.target_job_codes, "target_job_codes")
            known = {item["job_cd"]: item for item in pilot_config["jobs"]}
            jobs: list[dict[str, str]] = []
            unknown: list[str] = []
            for code in requested:
                if code in known:
                    jobs.append(dict(known[code]))
                    continue
                raw_api_job = self._job_from_raw_api(code)
                if raw_api_job is None:
                    unknown.append(code)
                else:
                    jobs.append(raw_api_job)
            if unknown:
                raise ValueError(
                    "Unknown job code(s): "
                    + ", ".join(unknown)
                    + ". Available job codes: "
                    + ", ".join(known)
                    + " or any job code under "
                    + self.source_root.as_posix()
                )
            pilot_config["jobs"] = jobs

        if self.target_difficulty_codes is not None:
            requested = self._dedupe_codes(self.target_difficulty_codes, "target_difficulty_codes")
            known = {item["code"]: item for item in pilot_config["difficulties"]}
            unknown = [code for code in requested if code not in known]
            if unknown:
                raise ValueError(
                    "Unknown difficulty code(s): "
                    + ", ".join(unknown)
                    + ". Available difficulty codes: "
                    + ", ".join(known)
                )
            pilot_config["difficulties"] = [dict(known[code]) for code in requested]

        return pilot_config

    def _dedupe_codes(self, values: list[str], label: str) -> list[str]:
        """CLI/API로 받은 코드 목록에서 공백과 중복을 제거한다."""

        codes = [value.strip() for value in values if isinstance(value, str) and value.strip()]
        if not codes:
            raise ValueError(f"{label} must include at least one code.")
        return list(dict.fromkeys(codes))

    def _job_from_raw_api(self, job_cd: str) -> dict[str, str] | None:
        """기본 pilot 목록에 없는 직무라도 data/api_raw 폴더가 있으면 실행 대상으로 만든다."""

        job_dir = self.source_root / job_cd
        if not job_dir.is_dir():
            return None
        job_name = job_cd
        path = job_dir / "dtlGb_2.xml"
        if path.exists():
            try:
                job_name = normalize_text(ET.parse(path).getroot().findtext("jobSmclNm")) or job_cd
            except ET.ParseError:
                job_name = job_cd
        return {"job_cd": job_cd, "job_name": job_name}

    def _run_one(
        self,
        profile: dict[str, Any],
        job: dict[str, str],
        difficulty: dict[str, str],
        usage: dict[str, int],
    ) -> dict[str, Any]:
        """직무/난이도 하나에 대해 decision, draft, validation, save 단계를 실행한다."""

        job_cd = job["job_cd"]
        difficulty_code = difficulty["code"]
        artifacts: dict[str, str] = {}
        repair_count = 0
        self._progress_target(job_cd, difficulty_code, "target started")

        try:
            # selector/이전 규칙 단계가 끝나야만 draft LLM 입력을 만들 수 있다.
            # 실제 selector 검증 실패는 여기서 decision_failed로 멈추고 fallback하지 않는다.
            # decision_config는 selector 성공 시 쓰이지 않고, 이전 규칙 경로에만 전달된다.
            decision_config = PILOT_JOB_CONFIGS.get(job_cd, {})
            self._progress_target(job_cd, difficulty_code, "decisions started")
            decisions = self._build_system_decisions(
                profile=profile,
                job_cd=job_cd,
                difficulty_code=difficulty_code,
                decision_config=decision_config,
                usage=usage,
                artifacts=artifacts,
            )
            self._progress_target(
                job_cd,
                difficulty_code,
                "decisions ready",
                f"exec={decisions['selected_exec_job']['exec_job_id']} task={decisions['primary_task_type']}",
            )
            evidence_names = self._evidence_names(profile)
            constraints = self.constraints_builder.build(evidence_names=evidence_names)
            practice_profile = None
            practice_sheet_background = None
            # 기본 경로는 Markdown 조사시트만 배경지식으로 쓰고, 이전 mission_seed 입력은 만들지 않는다.
            if self.use_practice_sheet_background:
                mission_seed = None
                practice_excerpt = None
                practice_sheet_background = self.practice_sheet_background_loader.load(job_cd)
                detail = "loaded" if practice_sheet_background is not None else "missing"
                self._progress_target(job_cd, difficulty_code, "practice sheet background", detail)
            else:
                practice_profile = self.practice_profile_loader.load(job_cd)
                mission_seed = self.seed_builder.build(
                    job_profile=profile,
                    practice_profile=practice_profile,
                    system_decisions=decisions,
                )
                practice_excerpt = (
                    self.seed_builder.excerpt(practice_profile, mission_seed)
                    if practice_profile is not None and mission_seed is not None
                    else None
                )
                self._progress_target(job_cd, difficulty_code, "mission seed ready")
            llm_input = self.input_builder.build(
                profile,
                decisions,
                constraints,
                job_practice_profile_excerpt=practice_excerpt,
                mission_seed=mission_seed,
                job_practice_sheet_background=practice_sheet_background,
            )
            self.storage.save_job_artifact(job_cd, difficulty_code, "system_decisions.json", decisions)
            self.storage.save_job_artifact(job_cd, difficulty_code, "schema_constraints.json", constraints)
            self.storage.save_job_artifact(job_cd, difficulty_code, "llm_input_package.json", llm_input)
            artifacts.update(
                {
                    "system_decisions": "system_decisions.json",
                    "schema_constraints": "schema_constraints.json",
                    "llm_input_package": "llm_input_package.json",
                }
            )
            if practice_profile is not None and mission_seed is not None:
                self.storage.save_job_artifact(job_cd, difficulty_code, "job_practice_profile.json", practice_profile)
                self.storage.save_job_artifact(job_cd, difficulty_code, "mission_seed.json", mission_seed)
                artifacts.update(
                    {
                        "job_practice_profile": "job_practice_profile.json",
                        "mission_seed": "mission_seed.json",
                    }
                )
            if practice_sheet_background is not None:
                self.storage.save_job_artifact(job_cd, difficulty_code, "job_practice_sheet_background.json", practice_sheet_background)
                artifacts["job_practice_sheet_background"] = "job_practice_sheet_background.json"
        except DecisionSelectionError as exc:
            self._progress_target(job_cd, difficulty_code, "decision failed", exc.code)
            return self._save_failed_status(
                job_cd=job_cd,
                job_name=job["job_name"],
                difficulty=difficulty,
                status="decision_failed",
                reason_code=exc.code,
                error={"message": str(exc), "errors": exc.errors},
                results=None,
                artifacts=artifacts,
            )
        except Exception as exc:
            self._progress_target(job_cd, difficulty_code, "decision failed", "DECISION_FAILED")
            return self._save_failed_status(
                job_cd=job_cd,
                job_name=job["job_name"],
                difficulty=difficulty,
                status="decision_failed",
                reason_code="DECISION_FAILED",
                error={"message": str(exc)},
                results=None,
                artifacts=artifacts,
            )

        self._progress_target(job_cd, difficulty_code, "draft LLM started")
        generated = self.draft_generator.generate(llm_input)
        call_result = generated["llm_call_result"]
        draft = generated["mission_draft"]
        self._progress_target(job_cd, difficulty_code, "draft LLM finished", str(call_result.get("status") or "unknown"))
        self._collect_usage(call_result, usage)
        if call_result["provider"] == "mock":
            usage["mock_draft_count"] += 1
        elif call_result["status"] == "completed":
            usage["draft_call_count"] += 1
        self.storage.save_job_artifact(job_cd, difficulty_code, "llm_call_result_attempt_0.json", call_result)
        artifacts["llm_call_result_attempt_0"] = "llm_call_result_attempt_0.json"
        if draft is None:
            self._progress_target(job_cd, difficulty_code, "llm failed")
            return self._save_failed_status(
                job_cd=job_cd,
                job_name=job["job_name"],
                difficulty=difficulty,
                status="llm_failed",
                reason_code=(call_result.get("errors") or [{"code": "LLM_FAILED"}])[0]["code"],
                error={"errors": call_result.get("errors", [])},
                results=None,
            )

        self.storage.save_job_artifact(job_cd, difficulty_code, "mission_draft_attempt_0.json", draft)
        validation = self.validator.validate(
            job_profile=profile,
            system_decisions=decisions,
            mission_output_draft=draft,
            attempt=0,
        )
        self._progress_target(job_cd, difficulty_code, "validator attempt 0", validation["status"])
        validator_path = self.storage.save_job_artifact(job_cd, difficulty_code, "validator_result_attempt_0.json", validation)
        artifacts.update(
            {
                "mission_draft_attempt_0": "mission_draft_attempt_0.json",
                "validator_result_attempt_0": "validator_result_attempt_0.json",
            }
        )

        # 첫 검증에서 고칠 수 있는 오류가 나오면 validator 결과를 repair 요청에 그대로 넘긴다.
        if validation["status"] == "repair_required":
            self._progress_target(job_cd, difficulty_code, "repair LLM started")
            repair_request = RepairPromptBuilder().build(decisions, draft, validation, self._evidence_names(profile))
            self.storage.save_job_artifact(job_cd, difficulty_code, "repair_request_attempt_1.json", repair_request)
            repaired = self.repair_manager.repair(
                repair_request=repair_request,
                json_schema=constraints["structured_output_schema"],
            )
            repair_call = repaired["llm_call_result"]
            draft = repaired["mission_draft"]
            repair_count = 1
            self._progress_target(job_cd, difficulty_code, "repair LLM finished", str(repair_call.get("status") or "unknown"))
            self._collect_usage(repair_call, usage)
            if repair_call["provider"] == "mock":
                usage["mock_repair_count"] += 1
            elif repair_call["status"] == "completed":
                usage["repair_call_count"] += 1
            self.storage.save_job_artifact(job_cd, difficulty_code, "llm_call_result_attempt_1.json", repair_call)
            self.storage.save_job_artifact(job_cd, difficulty_code, "mission_draft_attempt_1.json", draft)
            validation = self.validator.validate(
                job_profile=profile,
                system_decisions=decisions,
                mission_output_draft=draft,
                attempt=1,
            )
            self._progress_target(job_cd, difficulty_code, "validator attempt 1", validation["status"])
            validator_path = self.storage.save_job_artifact(job_cd, difficulty_code, "validator_result_attempt_1.json", validation)
            artifacts.update(
                {
                    "repair_request_attempt_1": "repair_request_attempt_1.json",
                    "llm_call_result_attempt_1": "llm_call_result_attempt_1.json",
                    "mission_draft_attempt_1": "mission_draft_attempt_1.json",
                    "validator_result_attempt_1": "validator_result_attempt_1.json",
                }
            )

        # 최종 저장은 validator가 pass를 준 draft만 대상으로 한다.
        if validation["status"] == "pass":
            final_output = self.assembler.assemble(
                mission_output_draft=draft,
                validator_result=validation,
                job_cd=job_cd,
                difficulty_code=difficulty_code,
                repair_count=repair_count,
            )
            mission_path = self.storage.save_job_artifact(job_cd, difficulty_code, "mission_output.json", final_output)
            artifacts["mission_output"] = "mission_output.json"
            status_path = self.storage.save_run_status(
                job_cd=job_cd,
                job_name=profile["job_identity"].get("job_smcl_nm") or job["job_name"],
                difficulty=difficulty,
                status="saved",
                repair_count=repair_count,
                reliability_score=final_output["reliability"]["score"],
                warning_count=final_output["reliability"]["warning_count"],
                fail_count=final_output["reliability"]["fail_count"],
                artifacts=artifacts,
                error=None,
            )
            result = {
                "job_cd": job_cd,
                "job_name": profile["job_identity"].get("job_smcl_nm") or job["job_name"],
                "difficulty_code": difficulty_code,
                "status": "saved",
                "mission_id": final_output["mission_id"],
                "reliability_score": final_output["reliability"]["score"],
                "warning_count": final_output["reliability"]["warning_count"],
                "repair_count": repair_count,
                "mission_output_path": self.storage.relative_to_run(mission_path),
            }
            self.storage.record_artifact_item(
                job_cd=job_cd,
                difficulty_code=difficulty_code,
                status="saved",
                mission_output_path=self.storage.relative_to_run(mission_path),
                validator_result_path=self.storage.relative_to_run(validator_path),
                run_status_path=self.storage.relative_to_run(status_path) or "",
                flush=False,
            )
            self._progress_target(
                job_cd,
                difficulty_code,
                "saved",
                f"reliability={final_output['reliability']['score']} repair={repair_count}",
            )
            return result

        # pass하지 못한 target도 run_status와 failure_index에 남겨 나중에 원인을 추적할 수 있게 한다.
        status = "discarded" if validation["status"] == "discard" else "repair_failed"
        self._progress_target(job_cd, difficulty_code, status, (validation["errors"] or [{"code": "VALIDATOR_FAILED"}])[0]["code"])
        status_path = self.storage.save_run_status(
            job_cd=job_cd,
            job_name=profile["job_identity"].get("job_smcl_nm") or job["job_name"],
            difficulty=difficulty,
            status=status,
            repair_count=repair_count,
            reliability_score=validation["reliability"]["score"],
            warning_count=validation["reliability"]["warning_count"],
            fail_count=validation["reliability"]["fail_count"],
            artifacts=artifacts,
            error={"errors": validation["errors"], "warnings": validation["warnings"]},
        )
        self.storage.record_artifact_item(
            job_cd=job_cd,
            difficulty_code=difficulty_code,
            status=status,
            mission_output_path=None,
            validator_result_path=self.storage.relative_to_run(validator_path),
            run_status_path=self.storage.relative_to_run(status_path) or "",
            flush=False,
        )
        self.storage.record_failure(
            job_cd=job_cd,
            difficulty_code=difficulty_code,
            status=status,
            reason_code=(validation["errors"] or [{"code": "VALIDATOR_FAILED"}])[0]["code"],
            run_status_path=self.storage.relative_to_run(status_path) or "",
            validator_result_path=self.storage.relative_to_run(validator_path),
            flush=False,
        )
        return {
            "job_cd": job_cd,
            "job_name": profile["job_identity"].get("job_smcl_nm") or job["job_name"],
            "difficulty_code": difficulty_code,
            "status": status,
            "mission_id": None,
            "reliability_score": validation["reliability"]["score"],
            "warning_count": validation["reliability"]["warning_count"],
            "repair_count": repair_count,
            "mission_output_path": None,
        }

    def _save_failed_status(
        self,
        *,
        job_cd: str,
        job_name: str,
        difficulty: dict[str, str],
        status: str,
        reason_code: str,
        error: dict[str, Any],
        results: list[dict[str, Any]] | None,
        artifacts: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """profile/decision/LLM 단계에서 중단된 target의 실패 산출물과 index를 저장한다."""

        status_path = self.storage.save_run_status(
            job_cd=job_cd,
            job_name=job_name,
            difficulty=difficulty,
            status=status,
            repair_count=0,
            reliability_score=None,
            warning_count=0,
            fail_count=1,
            artifacts=artifacts or {},
            error=error,
        )
        self.storage.record_artifact_item(
            job_cd=job_cd,
            difficulty_code=difficulty["code"],
            status=status,
            mission_output_path=None,
            validator_result_path=None,
            run_status_path=self.storage.relative_to_run(status_path) or "",
            flush=False,
        )
        self.storage.record_failure(
            job_cd=job_cd,
            difficulty_code=difficulty["code"],
            status=status,
            reason_code=reason_code,
            run_status_path=self.storage.relative_to_run(status_path) or "",
            flush=False,
        )
        result = {
            "job_cd": job_cd,
            "job_name": job_name,
            "difficulty_code": difficulty["code"],
            "status": status,
            "mission_id": None,
            "reliability_score": None,
            "warning_count": 0,
            "repair_count": 0,
            "mission_output_path": None,
        }
        if results is not None:
            results.append(result)
        return result

    def _run_one_with_usage(
        self,
        profile: dict[str, Any],
        job: dict[str, str],
        difficulty: dict[str, str],
    ) -> dict[str, Any]:
        """병렬 실행 시 target 결과와 해당 target의 usage를 함께 반환한다."""

        usage = self._empty_usage()
        result = self._run_one(profile, job, difficulty, usage)
        return {"result": result, "usage": usage}

    def _build_system_decisions(
        self,
        *,
        profile: dict[str, Any],
        job_cd: str,
        difficulty_code: str,
        decision_config: dict[str, Any],
        usage: dict[str, int],
        artifacts: dict[str, str],
    ) -> dict[str, Any]:
        """기본 selector 경로 또는 --no-llm-selector 규칙 경로로 system_decisions를 만든다."""

        if not self.use_llm_decision_selector:
            self._progress_target(job_cd, difficulty_code, "selector disabled", "이전 규칙 사용")
            return self.decision_builder.build(profile, difficulty_code, decision_config)

        # selector는 미션 본문이 아니라 system_decisions 후보만 고른다.
        # 검증을 통과한 selector 결과만 이후 draft 생성 단계로 넘어간다.
        selector_input = self.selector_input_builder.build(profile, difficulty_code)
        self.storage.save_job_artifact(job_cd, difficulty_code, "decision_selector_input.json", selector_input)
        artifacts["decision_selector_input"] = "decision_selector_input.json"

        self._progress_target(job_cd, difficulty_code, "selector started")
        selector_run = self.decision_selector.select(selector_input)
        call_result = selector_run["llm_call_result"]
        selector_result = selector_run.get("selector_result")
        self._collect_usage(call_result, usage)
        if call_result.get("status") == "completed" and call_result.get("provider") != "local":
            usage["selector_call_count"] += 1
        self.storage.save_job_artifact(job_cd, difficulty_code, "decision_selector_call_result.json", call_result)
        self.storage.save_job_artifact(job_cd, difficulty_code, "decision_selector_result.json", selector_result)
        artifacts.update(
            {
                "decision_selector_call_result": "decision_selector_call_result.json",
                "decision_selector_result": "decision_selector_result.json",
            }
        )

        validation = self.selector_validator.validate(selector_input, selector_result, job_profile=profile)
        self.storage.save_job_artifact(job_cd, difficulty_code, "decision_selector_validation.json", validation)
        artifacts["decision_selector_validation"] = "decision_selector_validation.json"
        if validation["status"] == "passed":
            self._progress_target(job_cd, difficulty_code, "selector passed")
            return self.decision_builder.build_from_selector(profile, difficulty_code, selector_result)

        if self._selector_was_locally_skipped(call_result):
            usage["selector_fallback_count"] += 1
            self._progress_target(job_cd, difficulty_code, "selector skipped", "이전 규칙 사용")
            return self.decision_builder.build(profile, difficulty_code, decision_config)

        usage["selector_failed_count"] += 1
        errors = validation.get("errors", [])
        codes = ", ".join(error.get("code", "UNKNOWN") for error in errors) or "UNKNOWN"
        self._progress_target(job_cd, difficulty_code, "selector failed", codes)
        raise DecisionSelectionError(
            f"Decision selector validation failed: {codes}",
            errors=errors,
        )

    def _progress_target(
        self,
        job_cd: str,
        difficulty_code: str,
        stage: str,
        detail: str | None = None,
    ) -> None:
        """직무/난이도 기준으로 현재 target의 진행 메시지를 출력한다."""

        current = self._progress_positions.get((job_cd, difficulty_code))
        total = self._progress_total_targets or None
        self.progress.target(
            current=current,
            total=total,
            job_cd=job_cd,
            difficulty_code=difficulty_code,
            stage=stage,
            detail=detail,
        )

    def _selector_was_locally_skipped(self, call_result: dict[str, Any]) -> bool:
        """selector 호출 전 skip된 경우만 의사결정 후보를 이전 규칙으로 대체한다.

        이 fallback은 draft/repair mock 생성 허용 여부와 별개이며, 최종 미션 본문은
        allow_mock_fallback 또는 force_mock이 켜져 있지 않으면 실제 LLM이 필요하다.
        """

        if call_result.get("provider") != "local" or call_result.get("status") != "skipped":
            return False
        codes = {
            error.get("code")
            for error in call_result.get("errors", [])
            if isinstance(error, dict)
        }
        return bool(codes & {"DECISION_SELECTOR_MOCK_MODE", "OPENAI_API_KEY_MISSING"})

    def _empty_usage(self) -> dict[str, int]:
        """run/target 단위 LLM usage 누적값의 초기 구조를 만든다."""

        return {
            "selector_call_count": 0,
            "selector_fallback_count": 0,
            "selector_failed_count": 0,
            "draft_call_count": 0,
            "repair_call_count": 0,
            "mock_draft_count": 0,
            "mock_repair_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }

    def _merge_usage(self, total: dict[str, int], delta: dict[str, int]) -> None:
        """target별 usage를 run 전체 usage에 더한다."""

        for key in total:
            total[key] += int(delta.get(key) or 0)

    def _collect_usage(self, call_result: dict[str, Any], usage: dict[str, int]) -> None:
        """LLM call_result 안의 token usage를 누적 dict에 반영한다."""

        call_usage = call_result.get("usage") or {}
        for key in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
            usage[key] += int(call_usage.get(key) or 0)

    def _evidence_names(self, profile: dict[str, Any]) -> list[str]:
        """schema/repair에 넘길 profile evidence 이름을 중복 없이 수집한다."""

        names: list[str] = []
        for group in profile.get("evidence", {}).values():
            for item in group:
                name = item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and name and name not in names:
                    names.append(name)
        return names

    def _summary(
        self,
        results: list[dict[str, Any]],
        usage: dict[str, int],
        *,
        started_at: str,
        finished_at: str,
        duration_seconds: float,
    ) -> dict[str, Any]:
        """target 결과와 LLM usage를 run 단위 pilot_summary 구조로 집계한다."""

        saved = [item for item in results if item["status"] == "saved"]
        failed = [item for item in results if item["status"] != "saved"]
        scores = [item["reliability_score"] for item in saved if item.get("reliability_score") is not None]
        return {
            "schema_version": "pilot_summary.v1",
            "run_id": self.storage.run_id,
            "created_at": iso_now(),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": duration_seconds,
            "concurrency": self.concurrency,
            "total_targets": len(results),
            "saved_count": len(saved),
            "failed_count": len(failed),
            "repair_used_count": sum(1 for item in results if item["repair_count"] > 0),
            "average_reliability_score": round(sum(scores) / len(scores), 2) if scores else None,
            "llm_usage": usage,
            "openai_api_called": usage["selector_call_count"] + usage["draft_call_count"] + usage["repair_call_count"] > 0,
            "results": results,
        }


def main() -> None:
    """CLI에서 pilot runner를 실행하는 진입점."""

    parser = argparse.ArgumentParser(description="미션 생성 v1 pilot을 실행합니다.")
    parser.add_argument("--source-root", default="data/api_raw")
    parser.add_argument("--output-root", default="outputs")
    parser.add_argument("--mock", action="store_true", help="Force local mock generation even when OPENAI_API_KEY is set.")
    parser.add_argument(
        "--allow-mock-fallback",
        action="store_true",
        help="Use local mock generation only when OPENAI_API_KEY is missing. Default is to fail instead.",
    )
    parser.add_argument("--concurrency", type=int, default=2, help="직무/난이도 target을 동시에 실행할 개수입니다.")
    parser.add_argument("--jobs", type=_parse_codes, default=None, help="실행할 직무 코드를 쉼표로 구분해 입력합니다. 예: K000000997,K000001080")
    parser.add_argument("--difficulties", type=_parse_codes, default=None, help="실행할 난이도 코드를 쉼표로 구분해 입력합니다. 예: normal")
    parser.add_argument("--no-llm-selector", action="store_true", help="LLM decision selector를 끄고 이전 규칙 기반 system_decisions를 사용합니다.")
    parser.add_argument("--quiet", action="store_true", help="콘솔 진행 메시지를 출력하지 않습니다.")
    parser.add_argument(
        "--practice-sheet-background",
        dest="use_practice_sheet_background",
        action="store_true",
        help="mission_seed 대신 data/additional_search/{job_cd}.md 직무조사시트를 배경지식으로 사용합니다. 기본값입니다.",
    )
    parser.add_argument(
        "--mission-seed",
        dest="use_practice_sheet_background",
        action="store_false",
        help="직무조사시트 배경지식 대신 이전 mission_seed 흐름을 사용합니다.",
    )
    parser.set_defaults(use_practice_sheet_background=True)
    parser.add_argument("--practice-sheet-root", default="data/additional_search", help="{job_cd}.md 직무조사시트 파일이 있는 폴더 경로입니다.")
    args = parser.parse_args()
    runner = PilotRunner(
        source_root=args.source_root,
        output_root=args.output_root,
        force_mock=args.mock,
        allow_mock_fallback=args.allow_mock_fallback,
        concurrency=args.concurrency,
        target_job_codes=args.jobs,
        target_difficulty_codes=args.difficulties,
        use_llm_decision_selector=not args.no_llm_selector,
        use_practice_sheet_background=args.use_practice_sheet_background,
        practice_sheet_root=args.practice_sheet_root,
        progress_enabled=not args.quiet,
    )
    try:
        summary = runner.run()
    except ValueError as exc:
        parser.error(str(exc))
    print(summary["run_dir"])
    print(f"saved={summary['saved_count']} failed={summary['failed_count']} openai_api_called={summary['openai_api_called']}")


def _parse_codes(value: str) -> list[str]:
    """쉼표로 구분된 CLI 코드 목록을 공백 제거된 리스트로 변환한다."""

    codes = [part.strip() for part in value.split(",") if part.strip()]
    if not codes:
        raise argparse.ArgumentTypeError("must include at least one comma-separated code")
    return codes


if __name__ == "__main__":
    main()
