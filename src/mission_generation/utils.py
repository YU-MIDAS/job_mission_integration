# 경로 검증, JSON 저장, 텍스트 정규화, 비밀값 스캔 등 공통 유틸을 제공한다.

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WORKSPACE_ROOT = Path.cwd().resolve()
SECRET_PATTERNS = [
    re.compile(r"OPENAI_API_KEY", re.IGNORECASE),
    re.compile(r"Authorization\s*:\s*Bearer", re.IGNORECASE),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
]


def project_path(*parts: str | os.PathLike[str]) -> Path:
    """workspace 내부 경로만 허용하는 절대 Path를 만든다."""

    path = (WORKSPACE_ROOT.joinpath(*parts)).resolve()
    ensure_inside_workspace(path)
    return path


def ensure_inside_workspace(path: Path) -> None:
    """실수로 workspace 바깥 파일을 읽거나 쓰지 않도록 경로를 검증한다."""

    resolved = path.resolve()
    if resolved != WORKSPACE_ROOT and WORKSPACE_ROOT not in resolved.parents:
        raise ValueError(f"path escapes workspace: {resolved}")


def to_posix_relative(path: Path, base: Path) -> str:
    """산출물 manifest에 쓰기 좋은 POSIX 상대 경로 문자열로 변환한다."""

    return path.resolve().relative_to(base.resolve()).as_posix()


def normalize_text(value: str | None, preserve_newlines: bool = False) -> str:
    """XML/Markdown에서 읽은 텍스트의 HTML entity와 공백을 정리한다."""

    if value is None:
        return ""
    text = html.unescape(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if preserve_newlines:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
        return "\n".join(line for line in lines if line)
    return re.sub(r"\s+", " ", text).strip()


def split_exec_jobs(value: str | None) -> list[str]:
    """KNOW execJob 원문을 수행직무 문장 목록으로 분리한다."""

    text = normalize_text(value, preserve_newlines=True)
    items: list[str] = []
    for line in text.split("\n"):
        cleaned = re.sub(r"^\s*[-•*·\u2022]+\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    if not items and text:
        items = [text]
    return items


def source_ref(job_cd: str, file_name: str, field: str, index: int | None = None) -> dict[str, Any]:
    """profile/evidence 값이 어느 XML 파일과 필드에서 왔는지 표시한다."""

    ref: dict[str, Any] = {"file": f"{job_cd}/{file_name}", "field": field}
    if index is not None:
        ref["index"] = index
    return ref


def parse_score(value: str | None) -> float | None:
    """KNOW 점수 문자열을 숫자로 바꾸고 실패하면 None을 반환한다."""

    if value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def sorted_by_score(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """점수 내림차순으로 evidence 후보를 자르고 rank를 붙인다."""

    indexed = list(enumerate(items))
    indexed.sort(key=lambda pair: (-float(pair[1]["score"]), pair[0]))
    selected = [item for _, item in indexed[:limit]]
    for rank, item in enumerate(selected, start=1):
        item["rank"] = rank
    return selected


def write_json_atomic(path: Path, data: Any) -> None:
    """JSON 직렬화 가능성을 확인한 뒤 UTF-8 파일로 저장한다."""

    ensure_inside_workspace(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    json.loads(serialized)
    path.write_text(serialized + "\n", encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    """workspace 내부 JSON 파일을 읽어 Python 객체로 반환한다."""

    ensure_inside_workspace(path)
    return json.loads(path.read_text(encoding="utf-8"))


def seoul_now() -> datetime:
    """Asia/Seoul 기준 현재 시각을 반환한다."""

    try:
        tz = ZoneInfo("Asia/Seoul")
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=9), name="Asia/Seoul")
    return datetime.now(tz)


def iso_now() -> str:
    """run manifest에 쓰는 초 단위 ISO timestamp를 만든다."""

    return seoul_now().isoformat(timespec="seconds")


def scan_text_for_secrets(text: str) -> list[str]:
    """텍스트 안에서 API key/Authorization 패턴을 찾아 패턴명을 반환한다."""

    return [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(text)]


def scan_path_for_secrets(path: Path) -> list[dict[str, str]]:
    """파일 또는 폴더 전체에서 비밀값으로 보이는 문자열을 검사한다."""

    ensure_inside_workspace(path)
    findings: list[dict[str, str]] = []
    targets = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    for target in targets:
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in scan_text_for_secrets(text):
            findings.append({"file": target.relative_to(WORKSPACE_ROOT).as_posix(), "pattern": pattern})
    return findings
