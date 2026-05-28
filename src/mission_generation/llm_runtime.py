# OpenAI Responses API structured output 호출과 응답 파싱을 담당한다.

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import RuntimeConfig


class OpenAIResponsesRuntime:
    """OpenAI Responses API를 호출하고 공통 응답 메타데이터를 표준화한다."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()
        self._load_local_env_key()

    def api_key_available(self) -> bool:
        """환경 변수 또는 .env.local에서 사용할 API key가 준비됐는지 확인한다."""

        return bool(os.environ.get(self.config.api_key_env))

    def _load_local_env_key(self) -> None:
        """환경 변수에 key가 없을 때 .env.local에서 가져온다."""

        if os.environ.get(self.config.api_key_env):
            return
        env_path = Path.cwd() / ".env.local"
        if not env_path.exists():
            return
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() == self.config.api_key_env and value.strip():
                    os.environ[self.config.api_key_env] = value.strip().strip('"').strip("'")
                    return
        except OSError:
            return

    def call_structured(
        self,
        *,
        call_type: str,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
        schema_name: str = "mission_output_v1_draft",
    ) -> dict[str, Any]:
        """strict JSON schema를 포함해 LLM을 호출하고 파싱된 JSON 결과만 반환한다."""

        if not self.api_key_available():
            return self._missing_key_result(call_type, temperature)

        request_body = {
            "model": self.config.model,
            "reasoning": {"effort": self.config.reasoning_effort},
            "max_output_tokens": max_output_tokens,
            "tools": [],
            "tool_choice": "none",
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                }
            },
        }

        last_error: dict[str, str] | None = None
        retry_errors: list[dict[str, str]] = []
        attempt_count = 0
        for attempt in range(self.config.max_api_retries + 1):
            attempt_count = attempt + 1
            try:
                response = self._post(request_body)
                output_text = self._extract_output_text(response)
                output_json = self._parse_json(output_text)
                usage = self._usage(response)
                return {
                    "schema_version": "llm_call_result.v1",
                    "provider": self.config.provider,
                    "api": self.config.api,
                    "model": self.config.model,
                    "call_type": call_type,
                    "reasoning_effort": self.config.reasoning_effort,
                    **self._temperature_metadata(temperature),
                    **self._retry_metadata(attempt_count, retry_errors),
                    "status": response.get("status", "completed"),
                    "output_json": output_json,
                    "usage": usage,
                    "errors": [],
                }
            except urllib.error.HTTPError as exc:
                last_error = self._map_http_error(exc)
                if not self._should_retry(last_error["code"], attempt):
                    break
                retry_errors.append(last_error)
                time.sleep(1)
            except TimeoutError:
                last_error = {"code": "OPENAI_TIMEOUT", "message": "OpenAI request timed out."}
                if not self._should_retry(last_error["code"], attempt):
                    break
                retry_errors.append(last_error)
                time.sleep(1)
            except (urllib.error.URLError, OSError) as exc:
                last_error = {"code": "OPENAI_SERVER_ERROR", "message": str(exc)}
                if not self._should_retry(last_error["code"], attempt):
                    break
                retry_errors.append(last_error)
                time.sleep(1)
            except ValueError as exc:
                last_error = {"code": "OUTPUT_PARSE_FAILED", "message": str(exc)}
                if not self._should_retry(last_error["code"], attempt):
                    break
                retry_errors.append(last_error)
                time.sleep(1)

        return {
            "schema_version": "llm_call_result.v1",
            "provider": self.config.provider,
            "api": self.config.api,
            "model": self.config.model,
            "call_type": call_type,
            "reasoning_effort": self.config.reasoning_effort,
            **self._temperature_metadata(temperature),
            **self._retry_metadata(attempt_count, retry_errors),
            "status": "failed",
            "output_json": None,
            "usage": self._empty_usage(),
            "errors": [last_error or {"code": "OPENAI_SERVER_ERROR", "message": "OpenAI request failed."}],
        }

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """Responses API에 HTTP POST를 보내고 JSON 응답을 반환한다."""

        api_key = os.environ[self.config.api_key_env]
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _missing_key_result(self, call_type: str, temperature: float) -> dict[str, Any]:
        """API key가 없을 때도 저장 가능한 표준 call_result를 반환한다."""

        return {
            "schema_version": "llm_call_result.v1",
            "provider": self.config.provider,
            "api": self.config.api,
            "model": self.config.model,
            "call_type": call_type,
            "reasoning_effort": self.config.reasoning_effort,
            **self._temperature_metadata(temperature),
            **self._retry_metadata(1, []),
            "status": "skipped",
            "output_json": None,
            "usage": self._empty_usage(),
            "errors": [{"code": "OPENAI_API_KEY_MISSING", "message": "OPENAI_API_KEY is not set."}],
        }

    def _temperature_metadata(self, configured_temperature: float) -> dict[str, Any]:
        return {
            "configured_temperature": configured_temperature,
            "temperature_applied": False,
            "temperature_omitted_reason": self.config.temperature_application()["temperature_omitted_reason"],
        }

    def _retry_metadata(self, attempt_count: int, retry_errors: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "attempt_count": attempt_count,
            "retry_count": max(0, attempt_count - 1),
            "retry_errors": retry_errors,
        }

    def _extract_output_text(self, response: dict[str, Any]) -> str:
        """Responses API 응답 형태 차이를 흡수해 출력 텍스트만 꺼낸다."""

        if isinstance(response.get("output_text"), str):
            return response["output_text"]
        chunks: list[str] = []
        for output in response.get("output", []) or []:
            for content in output.get("content", []) or []:
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        if chunks:
            return "".join(chunks)
        raise ValueError("response did not contain output text")

    def _parse_json(self, text: str) -> Any:
        """모델 출력에서 JSON 객체를 파싱하고, 앞뒤 잡음이 있으면 한 번 보정한다."""

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise ValueError("could not parse JSON output")

    def _usage(self, response: dict[str, Any]) -> dict[str, int]:
        """Responses API usage 필드를 프로젝트 표준 token usage 구조로 바꾼다."""

        usage = response.get("usage") or {}
        output_details = usage.get("output_tokens_details") or {}
        return {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }

    def _empty_usage(self) -> dict[str, int]:
        """LLM 호출이 없거나 실패했을 때 사용할 빈 usage 구조를 만든다."""

        return {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0}

    def _map_http_error(self, exc: urllib.error.HTTPError) -> dict[str, str]:
        """HTTP status code를 프로젝트 표준 오류 코드로 바꾼다."""

        if exc.code in {401, 403}:
            code = "OPENAI_AUTH_FAILED"
        elif exc.code == 429:
            code = "OPENAI_RATE_LIMITED"
        elif 500 <= exc.code < 600:
            code = "OPENAI_SERVER_ERROR"
        else:
            code = "OPENAI_BAD_REQUEST"
        return {"code": code, "message": self._safe_http_error_message(exc)}

    def _safe_http_error_message(self, exc: urllib.error.HTTPError) -> str:
        """API 오류 응답에서 key가 노출되지 않는 안전한 메시지만 추출한다."""

        fallback = f"OpenAI HTTP error {exc.code}."
        try:
            body = exc.read().decode("utf-8", errors="replace")
            error = json.loads(body).get("error", {})
        except Exception:
            return fallback
        if not isinstance(error, dict):
            return fallback
        message = error.get("message")
        if not isinstance(message, str) or not message:
            return fallback
        details: list[str] = []
        for key in ("type", "param", "code"):
            value = error.get(key)
            if isinstance(value, str) and value:
                details.append(f"{key}={value}")
        suffix = f" ({', '.join(details)})" if details else ""
        return f"{message}{suffix}"

    def _should_retry(self, code: str, attempt: int) -> bool:
        """일시적 오류와 출력 파싱 실패에 대해서만 설정된 횟수 안에서 재시도한다."""

        return attempt < self.config.max_api_retries and code in {
            "OPENAI_RATE_LIMITED",
            "OPENAI_TIMEOUT",
            "OPENAI_SERVER_ERROR",
            "OUTPUT_PARSE_FAILED",
        }
