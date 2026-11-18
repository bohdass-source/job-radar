"""Client for your llm-gateway.

Assumes the OpenAI-compatible contract (POST {base}/v1/chat/completions). If the
gateway speaks something else, only `_call` needs to change.
`complete_json` is the piece the rest of the code relies on: it asks for JSON,
validates it against a pydantic model and, when parsing or validation fails,
sends the error back to the model and tries again.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import settings

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    pass


class LLMInvalidOutput(RuntimeError):
    def __init__(self, message: str, attempts: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def _extract_json(text: str) -> Any:
    text = _FENCE.sub("", text.strip())
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None,
                 transport: httpx.BaseTransport | None = None, timeout_s: float = 120.0) -> None:
        s = settings()
        self.base_url = (base_url if base_url is not None else s.llm_url).rstrip("/")
        self.model = model or s.llm_model
        key = api_key if api_key is not None else s.llm_api_key
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._http = httpx.Client(timeout=timeout_s, headers=headers, transport=transport)
        self.calls = 0

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    def _call(self, messages: list[dict[str, str]], temperature: float = 0.0) -> str:
        if not self.available:
            raise LLMUnavailable("LLM_GATEWAY_URL is not set")
        body = {"model": self.model, "messages": messages, "temperature": temperature,
                "response_format": {"type": "json_object"}}
        for attempt in range(3):
            self.calls += 1
            try:
                resp = self._http.post(f"{self.base_url}/v1/chat/completions", json=body)
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise LLMUnavailable(str(exc)) from exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        raise LLMUnavailable("gateway kept failing")

    def complete_text(self, system: str, user: str, temperature: float = 0.2) -> str:
        if not self.available:
            raise LLMUnavailable("LLM_GATEWAY_URL is not set")
        body = {"model": self.model, "temperature": temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        self.calls += 1
        resp = self._http.post(f"{self.base_url}/v1/chat/completions", json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def complete_json(self, system: str, user: str, model_cls: type[T], max_attempts: int = 3) -> T:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        raw_answers: list[str] = []
        for _ in range(max_attempts):
            content = self._call(messages)
            raw_answers.append(content)
            try:
                return model_cls.model_validate(_extract_json(content))
            except (json.JSONDecodeError, ValidationError) as exc:
                error = str(exc)
                if len(error) > 1500:
                    error = error[:1500] + "..."
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": "Your answer is not valid for the schema. Error:\n"
                                                f"{error}\nReturn ONLY the corrected JSON object."},
                ]
        raise LLMInvalidOutput(f"no valid JSON after {max_attempts} attempts", raw_answers)
