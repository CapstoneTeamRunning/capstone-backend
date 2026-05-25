from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModalClientError(RuntimeError):
    pass


class ModalQuestionClient:
    def __init__(
        self,
        base_url: str,
        *,
        endpoint: str = "/generate",
        timeout_seconds: int = 20 * 60,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        normalized_base = str(base_url or "").strip().rstrip("/")
        if not normalized_base:
            raise ValueError("base_url must not be empty")
        self.url = f"{normalized_base}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
        self.timeout_seconds = timeout_seconds
        self.headers = {
            "Content-Type": "application/json",
            **dict(headers or {}),
        }

    def generate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        request = Request(self.url, data=body, headers=self.headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModalClientError(f"Modal HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ModalClientError(f"Modal request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ModalClientError("Modal request timed out") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModalClientError("Modal response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise ModalClientError("Modal response must be a JSON object")
        return data

