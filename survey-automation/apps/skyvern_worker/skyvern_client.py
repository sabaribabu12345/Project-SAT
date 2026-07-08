from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class SkyvernWorkflow:
    workflow_id: str
    raw_response: dict[str, object]


class SkyvernClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def create_validate_workflow(
        self,
        user_prompt: str,
        extracted_information_schema: dict[str, dict[str, str]],
        max_steps: int = 25,
        browser_session_id: str | None = None,
    ) -> SkyvernWorkflow:
        return self.create_workflow(
            user_prompt=user_prompt,
            extracted_information_schema=extracted_information_schema,
            max_steps=max_steps,
            browser_session_id=browser_session_id,
        )

    def create_scan_workflow(
        self,
        user_prompt: str,
        extracted_information_schema: dict[str, dict[str, str]],
        max_steps: int = 35,
        browser_session_id: str | None = None,
    ) -> SkyvernWorkflow:
        return self.create_workflow(
            user_prompt=user_prompt,
            extracted_information_schema=extracted_information_schema,
            max_steps=max_steps,
            browser_session_id=browser_session_id,
        )

    def create_fill_workflow(
        self,
        user_prompt: str,
        extracted_information_schema: dict[str, dict[str, str]],
        max_steps: int = 35,
        browser_session_id: str | None = None,
    ) -> SkyvernWorkflow:
        return self.create_workflow(
            user_prompt=user_prompt,
            extracted_information_schema=extracted_information_schema,
            max_steps=max_steps,
            browser_session_id=browser_session_id,
        )

    def create_workflow(
        self,
        user_prompt: str,
        extracted_information_schema: dict[str, dict[str, str]],
        max_steps: int,
        browser_session_id: str | None = None,
    ) -> SkyvernWorkflow:
        request_body: dict[str, object] = {
            "user_prompt": user_prompt,
            "run_with": "agent",
            "extracted_information_schema": extracted_information_schema,
            "ai_fallback": True,
            "max_steps_override": max_steps,
        }
        if browser_session_id:
            request_body["browser_session_id"] = browser_session_id

        payload = {"task_version": "v2", "request": request_body}
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/v1/workflows/create-from-prompt",
            data=body,
            method="POST",
            headers={
                "x-api-key": self._api_key,
                "Content-Type": "application/json",
            },
        )
        response_data = self._request_with_retry(req)
        workflow_id = str(response_data.get("workflow_id", "")).strip()
        if not workflow_id:
            raise RuntimeError("Skyvern response missing workflow_id")
        return SkyvernWorkflow(workflow_id=workflow_id, raw_response=response_data)

    def _request_with_retry(
        self,
        req: urllib.request.Request,
        max_attempts: int = 3,
        backoff_base: float = 2.0,
    ) -> dict[str, object]:
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    return json.loads(resp.read().decode("utf-8"))  # type: ignore[no-any-return]
            except urllib.error.HTTPError as exc:
                # 429 and 5xx are retryable; 4xx client errors are not
                if exc.code == 429 or exc.code >= 500:
                    last_exc = exc
                    if attempt < max_attempts - 1:
                        time.sleep(backoff_base ** attempt)
                    continue
                details = exc.read().decode("utf-8")
                raise RuntimeError(f"Skyvern request failed: status={exc.code} body={details}") from exc
            except urllib.error.URLError as exc:
                last_exc = exc
                if attempt < max_attempts - 1:
                    time.sleep(backoff_base ** attempt)
                continue
        assert last_exc is not None
        if isinstance(last_exc, urllib.error.HTTPError):
            details = last_exc.read().decode("utf-8")
            raise RuntimeError(f"Skyvern request failed after {max_attempts} attempts: status={last_exc.code} body={details}") from last_exc
        raise RuntimeError(f"Skyvern request failed after {max_attempts} attempts: {last_exc}") from last_exc
