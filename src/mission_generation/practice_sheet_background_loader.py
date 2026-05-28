# 기본 생성 경로에서 직무조사시트 Markdown을 LLM 배경지식 JSON으로 감싼다.

from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import project_path, to_posix_relative


class PracticeSheetBackgroundLoader:
    """직무조사시트 Markdown을 LLM 입력 패키지에 넣기 좋은 JSON 형태로 감싼다."""

    def __init__(self, root: str | Path = "data/additional_search") -> None:
        self.root = project_path(root)

    def load(self, job_cd: str) -> dict[str, Any] | None:
        """data/additional_search/{job_cd}.md가 있으면 background JSON으로 감싸 반환한다."""

        path = self.root / f"{job_cd}.md"
        if not path.exists():
            return None
        # 원문을 요약하지 않고 보존한다. 프롬프트 지침에서 background_only로만 쓰도록 제한한다.
        return {
            "schema_version": "job_practice_sheet_background.v1",
            "job_cd": job_cd,
            "source_path": to_posix_relative(path, project_path()),
            "content_markdown": path.read_text(encoding="utf-8"),
            "usage": "background_only",
        }
