# 파이프라인 기본 상수, 직무/난이도 기본값, OpenAI runtime 설정을 정의한다.

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


PIPELINE_VERSION = "v1"
SOURCE_ROOT = "data/api_raw"
OUTPUT_ROOT = "outputs"

PILOT_JOBS: list[dict[str, str]] = [
    {"job_cd": "K000000997", "job_name": "상품기획자"},
    {"job_cd": "K000001080", "job_name": "데이터분석가(빅데이터분석가)"},
    {"job_cd": "K000001179", "job_name": "투자분석가"},
    {"job_cd": "K000007519", "job_name": "보험상품개발자"},
]

DIFFICULTIES: list[dict[str, str]] = [
    {"code": "easy", "label": "쉬움"},
    {"code": "normal", "label": "보통"},
    {"code": "hard", "label": "어려움"},
]

# 기본 selector 성공 경로에서는 사용하지 않는다.
# --no-llm-selector 또는 selector가 API 호출 전에 local skip될 때 이전 규칙 경로에서만 참조한다.
PILOT_JOB_CONFIGS: dict[str, dict[str, Any]] = {
    "K000000997": {
        "preferred_exec_job_id": "exec_004",
        "preferred_exec_job_keywords": ["판매수준", "소비자", "평가", "정보", "수집", "분석"],
        "preferred_primary_task_type": "research_and_analysis",
        "materials": {
            "easy": ["memo"],
            "normal": ["chart", "memo"],
            "hard": ["email", "chart", "table"],
        },
    },
    "K000001080": {
        "preferred_exec_job_id": "exec_003",
        "preferred_exec_job_keywords": ["데이터", "처리", "분석", "플랫폼"],
        "preferred_primary_task_type": "research_and_analysis",
        "materials": {
            "easy": ["table"],
            "normal": ["chart", "table"],
            "hard": ["chart", "table", "log"],
        },
    },
    "K000001179": {
        "preferred_exec_job_id": "exec_001",
        "preferred_exec_job_keywords": ["경제상황", "산업", "기업", "정보", "수집", "분석"],
        "preferred_primary_task_type": "research_and_analysis",
        "materials": {
            "easy": ["chart"],
            "normal": ["chart", "table"],
            "hard": ["email", "chart", "table"],
        },
    },
    "K000007519": {
        "preferred_exec_job_id": "exec_002",
        "preferred_exec_job_keywords": ["분석결과", "사회환경", "경제사정", "보험상품", "개발"],
        "preferred_primary_task_type": "planning_and_proposal",
        "materials": {
            "easy": ["table"],
            "normal": ["table", "chart"],
            "hard": ["email", "table", "chart"],
        },
    },
}

TASK_TYPES = {
    "research_and_analysis",
    "planning_and_proposal",
    "decision_making",
    "coordination_and_negotiation",
    "diagnosis_and_improvement",
    "operation_and_scheduling",
    "communication_and_reporting",
}

MATERIAL_TYPES = {"chart", "table", "memo", "email", "schedule", "checklist", "log", "card"}
EXCLUDED_MATERIAL_TYPES = {"image", "screenshot"}


@dataclass(frozen=True)
class RuntimeConfig:
    """OpenAI Responses API 호출과 pilot 실행에 공통으로 쓰는 runtime 설정."""

    provider: str = "openai"
    api: str = "responses"
    model: str = field(default_factory=lambda: os.environ.get("OPENAI_GENERATION_MODEL", "gpt-5.4-nano"))
    reasoning_effort: str = "medium"
    draft_temperature: float = 0.8
    repair_temperature: float = 0.4
    max_output_tokens_draft: int = 12000
    max_output_tokens_repair: int = 8000
    timeout_seconds: int = 120
    max_api_retries: int = 1
    api_key_env: str = "OPENAI_API_KEY"

    def temperature_application(self) -> dict[str, Any]:
        """현재 모델에서 temperature를 실제 API body에 적용할지 기록한다."""

        return {
            "request_parameter": "omitted",
            "temperature_applied": False,
            "temperature_omitted_reason": "unsupported_by_model",
        }

    def as_manifest(self) -> dict[str, Any]:
        """run_manifest.json에 남길 안전한 runtime 요약을 반환한다."""

        return {
            "provider": self.provider,
            "api": self.api,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "draft_temperature": self.draft_temperature,
            "repair_temperature": self.repair_temperature,
            "temperature_application": self.temperature_application(),
        }

    def as_full_config(self) -> dict[str, Any]:
        """pilot_config.json에 저장할 전체 runtime 설정을 반환한다."""

        return {
            "schema_version": "llm_runtime_config.v1",
            "provider": self.provider,
            "api": self.api,
            "model": self.model,
            "reasoning": {"effort": self.reasoning_effort},
            "temperature": {
                "draft_generation": self.draft_temperature,
                "repair_generation": self.repair_temperature,
            },
            "temperature_application": self.temperature_application(),
            "output_mode": "structured_outputs",
            "structured_output": {
                "enabled": True,
                "strict": True,
                "schema_name": "mission_output_v1_draft",
            },
            "tools": {
                "enabled": False,
                "allow_web_search": False,
                "allow_file_search": False,
                "allow_code_interpreter": False,
            },
            "limits": {
                "max_output_tokens": self.max_output_tokens_draft,
                "timeout_ms": self.timeout_seconds * 1000,
            },
            "retry": {
                "max_api_retries": self.max_api_retries,
                "retry_on": ["timeout", "rate_limit", "server_error"],
            },
            "security": {
                "api_key_env": self.api_key_env,
                "key_flow": "secure_encrypted_key_setup",
                "log_prompts": False,
                "log_raw_responses": False,
                "redact_secrets": True,
            },
        }


def default_pilot_config() -> dict[str, Any]:
    """옵션 없이 실행할 때 사용할 기본 직무/난이도/pipeline 설정을 만든다."""

    return {
        "schema_version": "pilot_generation_config.v1",
        "source_root": SOURCE_ROOT,
        "use_kmeans": False,
        "use_llm_decision_selector": True,
        "use_practice_sheet_background": True,
        "jobs": PILOT_JOBS,
        "difficulties": DIFFICULTIES,
        "max_repair_attempts": 1,
    }
