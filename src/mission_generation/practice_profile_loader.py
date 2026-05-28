# 기본 경로가 아닌 옵션용 코드다.
# practice profile은 이전 방식인 --mission-seed 흐름에서만 읽고, 기본 경로는 Markdown 직무조사시트를 사용한다.

from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import project_path, read_json


class PracticeProfileLoadError(RuntimeError):
    """practice profile 파일 구조가 이전 seed 생성 방식에 맞지 않을 때 사용한다."""

    pass


class PracticeProfileLoader:
    """resources/practice_profiles/v1/{job_cd}.json 이전 방식 profile을 읽고 검증한다."""

    def __init__(self, root: str | Path = "resources/practice_profiles/v1") -> None:
        self.root = project_path(root)

    def load(self, job_cd: str) -> dict[str, Any] | None:
        """직무별 practice profile이 있으면 반환하고, 없으면 None을 반환한다."""

        path = self.root / f"{job_cd}.json"
        if not path.exists():
            return None
        data = read_json(path)
        self._validate(job_cd, data)
        return data

    def _validate(self, job_cd: str, data: Any) -> None:
        """legacy practice profile이 seed 생성에 필요한 최소 필드를 갖는지 확인한다."""

        if not isinstance(data, dict):
            raise PracticeProfileLoadError(f"practice profile for {job_cd} must be an object")
        if data.get("schema_version") != "job_practice_profile.v1":
            raise PracticeProfileLoadError(f"invalid practice profile schema for {job_cd}")
        identity = data.get("job_identity")
        if not isinstance(identity, dict) or identity.get("job_cd") != job_cd:
            raise PracticeProfileLoadError(f"practice profile job_cd mismatch for {job_cd}")
        required_lists = [
            "practice_tasks",
            "decision_situations",
            "practice_materials",
            "collaboration_contexts",
            "response_flow",
            "deliverable_formats",
        ]
        for field in required_lists:
            if not isinstance(data.get(field), list):
                raise PracticeProfileLoadError(f"practice profile {field} must be a list for {job_cd}")
