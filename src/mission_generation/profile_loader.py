# KNOW 원천 XML 파일을 미션 생성용 job_profile JSON으로 정규화한다.

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .config import SOURCE_ROOT
from .utils import normalize_text, parse_score, source_ref, split_exec_jobs, sorted_by_score, write_json_atomic


class ProfileLoadError(RuntimeError):
    """KNOW XML에서 필수 profile 값을 만들 수 없을 때 발생하는 오류."""

    def __init__(self, job_cd: str, errors: list[dict[str, str]]) -> None:
        self.job_cd = job_cd
        self.errors = errors
        super().__init__(f"failed to build job profile for {job_cd}: {errors}")


class JobProfileLoader:
    """직무별 KNOW XML 원천 파일을 job_profile JSON 구조로 정규화한다."""

    required_files = ("dtlGb_2.xml", "dtlGb_5.xml", "dtlGb_7.xml")
    optional_files = ("dtlGb_3.xml",)

    def __init__(
        self,
        source_root: str | Path = SOURCE_ROOT,
        output_root: str | Path = "outputs/profiles/v1",
    ) -> None:
        self.source_root = Path(source_root)
        self.output_root = Path(output_root)

    def build(self, job_cd: str, save: bool = True) -> dict[str, Any]:
        """직무 코드 하나에 대한 profile을 만들고, 필요하면 outputs에 저장한다."""

        job_dir = self.source_root / job_cd
        warnings: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        source_files = self._source_files(job_cd, job_dir)

        for file_name in self.required_files:
            if not (job_dir / file_name).exists():
                errors.append(
                    {
                        "code": "REQUIRED_FILE_MISSING",
                        "severity": "fail",
                        "message": f"{file_name} is required for {job_cd}.",
                    }
                )
        if errors:
            profile = self._empty_profile(job_cd, source_files, warnings, errors)
            raise ProfileLoadError(job_cd, profile["loader_errors"])

        roots: dict[str, ET.Element] = {}
        for file_name in self.required_files + self.optional_files:
            path = job_dir / file_name
            if not path.exists():
                if file_name in self.optional_files:
                    warnings.append(
                        {
                            "code": "OPTIONAL_FILE_MISSING",
                            "severity": "warning",
                            "message": f"{file_name} is missing; technical_context.techn_know is null.",
                        }
                    )
                continue
            try:
                roots[file_name] = ET.parse(path).getroot()
            except ET.ParseError as exc:
                severity = "fail" if file_name in self.required_files else "warning"
                item = {
                    "code": "XML_PARSE_FAILED",
                    "severity": severity,
                    "message": f"{file_name} parse failed: {exc}",
                }
                if severity == "fail":
                    errors.append(item)
                else:
                    warnings.append(item)
        if errors:
            profile = self._empty_profile(job_cd, source_files, warnings, errors)
            raise ProfileLoadError(job_cd, profile["loader_errors"])

        jobs_do = roots["dtlGb_2.xml"]
        profile = {
            "schema_version": "job_mission_profile.v1",
            "source_root": self.source_root.as_posix(),
            "job_identity": self._job_identity(job_cd, jobs_do),
            "work": self._work(job_cd, jobs_do),
            "technical_context": self._technical_context(job_cd, roots.get("dtlGb_3.xml"), warnings),
            "evidence": {
                "abilities": self._evidence_items(
                    job_cd,
                    roots["dtlGb_5.xml"],
                    "jobAbil",
                    "jobAblStatus",
                    "jobAblNm",
                    "jobAblCont",
                    "dtlGb_5.xml",
                    "ablKnwEnv.jobAbil",
                    5,
                    warnings,
                ),
                "knowledge": self._evidence_items(
                    job_cd,
                    roots["dtlGb_5.xml"],
                    "Knwldg",
                    "knwldgStatus",
                    "knwldgNm",
                    "knwldgCont",
                    "dtlGb_5.xml",
                    "ablKnwEnv.Knwldg",
                    5,
                    warnings,
                ),
                "work_environment": self._evidence_items(
                    job_cd,
                    roots["dtlGb_5.xml"],
                    "jobsEnv",
                    "jobEnvStatus",
                    "jobEnvNm",
                    "jobEnvCont",
                    "dtlGb_5.xml",
                    "ablKnwEnv.jobsEnv",
                    5,
                    warnings,
                ),
                "work_activities": self._evidence_items(
                    job_cd,
                    roots["dtlGb_7.xml"],
                    "jobActvImprtnc",
                    "jobActvImprtncStatus",
                    "jobActvImprtncNm",
                    "jobActvImprtncCont",
                    "dtlGb_7.xml",
                    "jobActv.jobActvImprtnc",
                    10,
                    warnings,
                ),
            },
            "source_files": source_files,
            "loader_warnings": warnings,
            "loader_errors": errors,
        }

        self._validate_profile(profile)
        if save:
            self.save(profile)
        return profile

    def save(self, profile: dict[str, Any]) -> Path:
        """생성된 job_profile을 표준 profile 산출물 경로에 저장한다."""

        job_cd = profile["job_identity"]["job_cd"]
        path = self.output_root / f"{job_cd}.json"
        write_json_atomic(path, profile)
        return path

    def _source_files(self, job_cd: str, job_dir: Path) -> list[dict[str, Any]]:
        """profile 산출물에 남길 원천 XML 파일 로드 여부 목록을 만든다."""

        files: list[dict[str, Any]] = []
        for file_name in self.required_files:
            files.append({"file": f"{job_cd}/{file_name}", "required": True, "loaded": (job_dir / file_name).exists()})
        for file_name in self.optional_files:
            files.append({"file": f"{job_cd}/{file_name}", "required": False, "loaded": (job_dir / file_name).exists()})
        return files

    def _empty_profile(
        self,
        job_cd: str,
        source_files: list[dict[str, Any]],
        warnings: list[dict[str, str]],
        errors: list[dict[str, str]],
    ) -> dict[str, Any]:
        """필수 XML 누락처럼 profile을 만들 수 없을 때도 오류 저장용 최소 구조를 만든다."""

        return {
            "schema_version": "job_mission_profile.v1",
            "source_root": self.source_root.as_posix(),
            "job_identity": {"job_cd": job_cd, "job_lrcl_nm": "", "job_mdcl_nm": "", "job_smcl_nm": "", "source_ref": {}},
            "work": {"job_summary": {"text": "", "source_ref": {}}, "exec_jobs": []},
            "technical_context": {"techn_know": None},
            "evidence": {"abilities": [], "knowledge": [], "work_environment": [], "work_activities": []},
            "source_files": source_files,
            "loader_warnings": warnings,
            "loader_errors": errors,
        }

    def _job_identity(self, job_cd: str, root: ET.Element) -> dict[str, Any]:
        """dtlGb_2.xml에서 직무 코드와 분류명을 추출한다."""

        return {
            "job_cd": normalize_text(root.findtext("jobCd")) or job_cd,
            "job_lrcl_nm": normalize_text(root.findtext("jobLrclNm")),
            "job_mdcl_nm": normalize_text(root.findtext("jobMdclNm")),
            "job_smcl_nm": normalize_text(root.findtext("jobSmclNm")),
            "source_ref": source_ref(job_cd, "dtlGb_2.xml", "jobsDo"),
        }

    def _work(self, job_cd: str, root: ET.Element) -> dict[str, Any]:
        """직무 요약과 수행직무 목록을 source_ref와 함께 만든다."""

        exec_jobs = []
        for idx, text in enumerate(split_exec_jobs(root.findtext("execJob")), start=1):
            exec_jobs.append(
                {
                    "exec_job_id": f"exec_{idx:03d}",
                    "text": text,
                    "source_ref": source_ref(job_cd, "dtlGb_2.xml", "jobsDo.execJob", idx),
                }
            )
        return {
            "job_summary": {
                "text": normalize_text(root.findtext("jobSum")),
                "source_ref": source_ref(job_cd, "dtlGb_2.xml", "jobsDo.jobSum"),
            },
            "exec_jobs": exec_jobs,
        }

    def _technical_context(
        self,
        job_cd: str,
        root: ET.Element | None,
        warnings: list[dict[str, str]],
    ) -> dict[str, Any]:
        """선택 XML인 dtlGb_3.xml에서 기술/지식 맥락을 읽는다."""

        if root is None:
            return {"techn_know": None}
        techn_know = normalize_text(root.findtext("technKnow"), preserve_newlines=True)
        if not techn_know:
            warnings.append(
                {
                    "code": "TECHN_KNOW_MISSING",
                    "severity": "warning",
                    "message": "dtlGb_3.xml exists but way.technKnow is empty.",
                }
            )
            return {"techn_know": None}
        return {
            "techn_know": {
                "text": techn_know,
                "source_ref": source_ref(job_cd, "dtlGb_3.xml", "way.technKnow"),
            }
        }

    def _evidence_items(
        self,
        job_cd: str,
        root: ET.Element,
        item_tag: str,
        score_tag: str,
        name_tag: str,
        desc_tag: str,
        file_name: str,
        field: str,
        limit: int,
        warnings: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """능력/지식/환경/활동 XML 반복 항목을 점수순 evidence 목록으로 변환한다."""

        items: list[dict[str, Any]] = []
        for index, element in enumerate(root.findall(item_tag), start=1):
            score = parse_score(element.findtext(score_tag))
            if score is None:
                warnings.append(
                    {
                        "code": "SCORE_PARSE_FAILED",
                        "severity": "warning",
                        "message": f"{field}[{index}] score could not be parsed.",
                    }
                )
                continue
            items.append(
                {
                    "name": normalize_text(element.findtext(name_tag)),
                    "score": int(score) if score.is_integer() else score,
                    "description": normalize_text(element.findtext(desc_tag)),
                    "rank": 0,
                    "source_ref": source_ref(job_cd, file_name, field, index),
                }
            )
        selected = sorted_by_score(items, limit)
        if len(selected) < limit:
            warnings.append(
                {
                    "code": "EVIDENCE_COUNT_BELOW_TARGET",
                    "severity": "warning",
                    "message": f"{field} has {len(selected)} items below target {limit}.",
                }
            )
        return selected

    def _validate_profile(self, profile: dict[str, Any]) -> None:
        """미션 생성에 필요한 identity, summary, exec_jobs, evidence가 있는지 확인한다."""

        errors = profile["loader_errors"]
        identity = profile["job_identity"]
        if not identity.get("job_cd") or not identity.get("job_smcl_nm"):
            errors.append({"code": "JOB_IDENTITY_MISSING", "severity": "fail", "message": "job identity is incomplete."})
        if not profile["work"]["job_summary"].get("text"):
            errors.append({"code": "JOB_SUMMARY_MISSING", "severity": "fail", "message": "job summary is empty."})
        if not profile["work"].get("exec_jobs"):
            errors.append({"code": "EXEC_JOBS_EMPTY", "severity": "fail", "message": "exec jobs are empty."})
        evidence = profile["evidence"]
        if any(not evidence.get(key) for key in ("abilities", "knowledge", "work_environment", "work_activities")):
            errors.append({"code": "EVIDENCE_EMPTY", "severity": "fail", "message": "one or more evidence groups are empty."})
        if errors:
            raise ProfileLoadError(identity.get("job_cd") or "", errors)
