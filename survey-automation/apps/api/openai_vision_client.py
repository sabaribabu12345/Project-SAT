from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from apps.api.settings import Settings


@dataclass(frozen=True)
class ExtractedPageQuestion:
    display_id: str
    question: str
    answer_type: str
    confidence: float
    source_text: str


@dataclass(frozen=True)
class VisionPageResult:
    questions: list[ExtractedPageQuestion]
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]
    duration_seconds: float
    error: str = ""


@dataclass(frozen=True)
class ExtractedWebpageQuestion:
    question: str
    question_type: str
    possible_answers: list[str]
    confidence: float


@dataclass(frozen=True)
class WebpageVisionResult:
    questions: list[ExtractedWebpageQuestion]
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]
    duration_seconds: float
    error: str = ""


class OpenAIVisionClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return bool(self._settings.pdf_vision_enabled and self._settings.pdf_vision_api_key)

    def extract_page_questions(
        self,
        *,
        image_path: str,
        page_number: int,
    ) -> VisionPageResult:
        if not self.configured:
            raise RuntimeError(
                "Vision provider requires PDF_VISION_ENABLED=true and PDF_VISION_API_KEY"
            )

        image_bytes = Path(image_path).read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = self._build_payload(image_b64=image_b64, page_number=page_number)

        endpoint = self._responses_endpoint()
        req = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._settings.pdf_vision_api_key}",
            },
            method="POST",
        )
        timeout = max(1, self._settings.pdf_vision_timeout_seconds)
        started = time.monotonic()
        try:
            with request.urlopen(req, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            duration = time.monotonic() - started
            return VisionPageResult(
                questions=[],
                request_payload=payload,
                response_payload={},
                duration_seconds=duration,
                error=f"HTTP {exc.code}: {body}",
            )
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            duration = time.monotonic() - started
            return VisionPageResult(
                questions=[],
                request_payload=payload,
                response_payload={},
                duration_seconds=duration,
                error=str(exc),
            )

        duration = time.monotonic() - started
        refusal = _extract_refusal(raw)
        if refusal:
            return VisionPageResult(
                questions=[],
                request_payload=payload,
                response_payload=raw,
                duration_seconds=duration,
                error=f"Model refused: {refusal}",
            )
        questions = self._parse_response(raw)
        return VisionPageResult(
            questions=questions,
            request_payload=payload,
            response_payload=raw,
            duration_seconds=duration,
        )

    def extract_webpage_questions(
        self,
        *,
        image_bytes: bytes,
        page_url: str,
    ) -> WebpageVisionResult:
        if not self.configured:
            raise RuntimeError(
                "Vision provider requires PDF_VISION_ENABLED=true and PDF_VISION_API_KEY"
            )

        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = self._build_webpage_payload(image_b64=image_b64, page_url=page_url)

        endpoint = self._responses_endpoint()
        req = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._settings.pdf_vision_api_key}",
            },
            method="POST",
        )
        timeout = max(1, self._settings.pdf_vision_timeout_seconds)
        started = time.monotonic()
        try:
            with request.urlopen(req, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            duration = time.monotonic() - started
            return WebpageVisionResult(
                questions=[],
                request_payload=payload,
                response_payload={},
                duration_seconds=duration,
                error=f"HTTP {exc.code}: {body}",
            )
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            duration = time.monotonic() - started
            return WebpageVisionResult(
                questions=[],
                request_payload=payload,
                response_payload={},
                duration_seconds=duration,
                error=str(exc),
            )

        duration = time.monotonic() - started
        refusal = _extract_refusal(raw)
        if refusal:
            return WebpageVisionResult(
                questions=[],
                request_payload=payload,
                response_payload=raw,
                duration_seconds=duration,
                error=f"Model refused: {refusal}",
            )
        questions = self._parse_webpage_response(raw)
        return WebpageVisionResult(
            questions=questions,
            request_payload=payload,
            response_payload=raw,
            duration_seconds=duration,
        )

    def _build_webpage_payload(self, *, image_b64: str, page_url: str) -> dict[str, Any]:
        return {
            "model": self._settings.pdf_vision_model,
            "max_output_tokens": 3000,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You are looking at a screenshot of an online survey web page. "
                                "Identify every question the page is asking the user to answer — "
                                "every textbox, textarea, dropdown, radio group, checkbox group, "
                                "date picker, or number field. "
                                "For each one, return the visible question or field label text "
                                "exactly as shown, its type (one of: textbox, textarea, dropdown, "
                                "radio, checkbox, date, number), and — for dropdown/radio/checkbox — "
                                "the list of visible answer options. "
                                "Do not invent fields that are not visible on this page. "
                                "Return JSON only."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"This is the page currently open at: {page_url}",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{image_b64}",
                        },
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "webpage_questions",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "questions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "question": {"type": "string"},
                                        "question_type": {"type": "string"},
                                        "possible_answers": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "confidence": {"type": "number"},
                                    },
                                    "required": [
                                        "question",
                                        "question_type",
                                        "possible_answers",
                                        "confidence",
                                    ],
                                },
                            }
                        },
                        "required": ["questions"],
                    },
                }
            },
        }

    def _parse_webpage_response(self, raw: dict[str, Any]) -> list[ExtractedWebpageQuestion]:
        text = _extract_output_text(raw)
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        rows = payload.get("questions") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        results: list[ExtractedWebpageQuestion] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            question = str(row.get("question") or "").strip()
            if not question:
                continue
            try:
                confidence = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            possible_answers = [
                str(item).strip()
                for item in (row.get("possible_answers") or [])
                if str(item).strip()
            ]
            results.append(
                ExtractedWebpageQuestion(
                    question=question,
                    question_type=str(row.get("question_type") or "textbox").strip() or "textbox",
                    possible_answers=possible_answers,
                    confidence=max(0.0, min(1.0, confidence)),
                )
            )
        return results

    def _build_payload(self, *, image_b64: str, page_number: int) -> dict[str, Any]:
        return {
            "model": self._settings.pdf_vision_model,
            "max_output_tokens": 4000,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You are reading one page of a Common Data Set (CDS) survey PDF. "
                                "Identify every fillable question on this page — every blank, box, "
                                "or table cell that expects a value. "
                                "Rewrite each into a single, clear, self-contained natural-language "
                                "question a human analyst could answer without seeing the PDF "
                                "(e.g. 'How many full-time first-year undergraduate men are enrolled?'). "
                                "Do not invent fields that are not visibly present on this page. "
                                "For each question also return the exact verbatim label or heading text "
                                "you read on the page that the question is based on (source_text) — this "
                                "is used to locate the field on the page image, so it must be copied "
                                "exactly as it appears, not paraphrased. "
                                "Return JSON only."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"This is page {page_number} of the PDF.",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{image_b64}",
                        },
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "pdf_page_questions",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "questions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "question_id": {"type": "string"},
                                        "question": {"type": "string"},
                                        "answer_type": {"type": "string"},
                                        "confidence": {"type": "number"},
                                        "source_text": {"type": "string"},
                                    },
                                    "required": [
                                        "question_id",
                                        "question",
                                        "answer_type",
                                        "confidence",
                                        "source_text",
                                    ],
                                },
                            }
                        },
                        "required": ["questions"],
                    },
                }
            },
        }

    def _parse_response(self, raw: dict[str, Any]) -> list[ExtractedPageQuestion]:
        text = _extract_output_text(raw)
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        rows = payload.get("questions") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        results: list[ExtractedPageQuestion] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            question = str(row.get("question") or "").strip()
            if not question:
                continue
            try:
                confidence = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            results.append(
                ExtractedPageQuestion(
                    display_id=str(row.get("question_id") or "").strip(),
                    question=question,
                    answer_type=str(row.get("answer_type") or "text").strip() or "text",
                    confidence=max(0.0, min(1.0, confidence)),
                    source_text=str(row.get("source_text") or "").strip(),
                )
            )
        return results

    def _responses_endpoint(self) -> str:
        base = (self._settings.pdf_vision_api_base or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/responses" if base.endswith("/v1") else f"{base}/v1/responses"


def _extract_refusal(raw: dict[str, Any]) -> str:
    output = raw.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "refusal":
                refusal = part.get("refusal")
                if isinstance(refusal, str) and refusal.strip():
                    return refusal.strip()
    return ""


def _extract_output_text(raw: dict[str, Any]) -> str:
    direct = raw.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = raw.get("output")
    if not isinstance(output, list):
        return ""
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                value = part.get("text")
                if isinstance(value, str):
                    chunks.append(value)
    return "\n".join(chunks).strip()
