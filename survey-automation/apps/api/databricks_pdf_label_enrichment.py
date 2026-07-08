from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient

from apps.api.openai_pdf_label_enrichment import PdfLabelEnrichment
from apps.api.pdf_scanner import PdfDatapointCandidate
from apps.api.settings import Settings


@dataclass(frozen=True)
class DatabricksPdfLabelTrace:
    model: str
    candidate_count: int
    prompt_chars: int


class DatabricksPdfLabelEnricher:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workspace_client: WorkspaceClient | None = None
        self._last_response_summary: str = ""

    @property
    def last_response_summary(self) -> str:
        return self._last_response_summary

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.databricks_host
            and self._settings.pdf_databricks_label_model
            and (
                self._effective_token()
                or (self._settings.databricks_client_id and self._settings.databricks_client_secret)
            )
        )

    def enrich_pdf(
        self,
        *,
        file_path: str | Path,
        candidates: list[PdfDatapointCandidate],
    ) -> dict[str, PdfLabelEnrichment]:
        # Intentionally do not send the PDF file itself to the model; we only use compact metadata
        # (field_name/label_text/etc) to keep payload small and avoid shipping documents upstream.
        del file_path
        if not self.configured or not candidates:
            return {}
        model = self._settings.pdf_databricks_label_model.strip()
        timeout_seconds = max(5, self._settings.pdf_databricks_label_timeout_seconds)
        enriched: dict[str, PdfLabelEnrichment] = {}
        for batch in self._candidate_batches(candidates):
            payload = self._build_request_payload(batch)
            try:
                # ThreadPoolExecutor used so we can enforce a hard wall-clock timeout even if the
                # underlying HTTP call doesn't honor timeouts. Avoid the context-manager form here,
                # because executor shutdown waits for the worker thread and would defeat the timeout.
                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(self._post_serving_json, model=model, payload=payload, timeout_seconds=timeout_seconds)
                try:
                    response = future.result(timeout=timeout_seconds)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
            except FuturesTimeoutError as exc:
                raise RuntimeError(
                    f"Databricks label enrichment timed out after {timeout_seconds}s"
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Databricks label enrichment failed: {exc}") from exc
            self._last_response_summary = self._summarize_raw_response(response)
            parsed = self._parse_response(response)
            if not parsed:
                continue
            enriched.update(parsed)
        return enriched

    def _build_request_payload(self, candidates: list[PdfDatapointCandidate]) -> dict[str, object]:
        compact_candidates = [
            {
                "candidate_key": candidate.candidate_key,
                "field_name": candidate.field_name,
                "label_text": candidate.label_text,
                "page_number": candidate.page_number,
            }
            for candidate in candidates
        ]
        # Larger batches need more output tokens to return structured mappings.
        max_tokens = min(12000, max(2000, len(compact_candidates) * 200))
        return {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You normalize survey PDF field labels for Databricks mapping. "
                        "Use only the provided field_name and label_text to infer meaningful labels. "
                        "Return JSON only, no markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "For each candidate, return meaningful_label, datapoint_path, and value_logic. "
                        "Keep label concise and specific.\n"
                        "JSON schema:\n"
                        '{"mappings":[{"candidate_key":"<id>","meaningful_label":"<text>","datapoint_path":"<dot.path.or.empty>","value_logic":"<text>","input_kind":"<text>","confidence":<0-1 number>}]}'
                        "\nCandidates:\n"
                        f"{json.dumps(compact_candidates, ensure_ascii=True)}"
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }

    def _candidate_batches(self, candidates: list[PdfDatapointCandidate]) -> list[list[PdfDatapointCandidate]]:
        batch_size = max(1, min(300, self._settings.pdf_databricks_label_batch_size))
        max_prompt_chars = max(2000, self._settings.pdf_databricks_label_max_prompt_chars)
        batches: list[list[PdfDatapointCandidate]] = []
        current: list[PdfDatapointCandidate] = []
        for candidate in candidates:
            proposed = [*current, candidate]
            exceeds_count = len(proposed) > batch_size
            exceeds_size = self._estimate_prompt_chars(proposed) > max_prompt_chars
            if current and (exceeds_count or exceeds_size):
                batches.append(current)
                current = [candidate]
            else:
                current = proposed
        if current:
            batches.append(current)
        return batches

    def _estimate_prompt_chars(self, candidates: list[PdfDatapointCandidate]) -> int:
        compact = [
            {
                "candidate_key": candidate.candidate_key,
                "field_name": candidate.field_name,
                "label_text": candidate.label_text,
                "page_number": candidate.page_number,
            }
            for candidate in candidates
        ]
        overhead_chars = 1300
        return overhead_chars + len(json.dumps(compact, ensure_ascii=True))

    def _parse_response(self, raw: Any) -> dict[str, PdfLabelEnrichment]:
        if isinstance(raw, dict):
            err = self._detect_error_payload(raw)
            if err:
                raise RuntimeError(err)
        text = self._extract_message_text(raw)
        if not text:
            return {}
        parsed = self._parse_json_text(text)
        mappings: list[Any] | None = None
        if isinstance(parsed, dict):
            candidate_mappings = parsed.get("mappings")
            if isinstance(candidate_mappings, list):
                mappings = candidate_mappings
        if mappings is None:
            # If the model output was truncated, the JSON may be invalid. Salvage any fully-formed
            # mapping objects so we still get partial value instead of dropping everything.
            mappings = self._salvage_mapping_objects(text)
        if not mappings:
            return {}
        enriched: dict[str, PdfLabelEnrichment] = {}
        for row in mappings:
            if not isinstance(row, dict):
                continue
            candidate_key = str(row.get("candidate_key") or "").strip()
            label_text = str(row.get("meaningful_label") or row.get("label_text") or "").strip()
            if not candidate_key or not label_text:
                continue
            confidence_raw = row.get("confidence")
            confidence = 0.0
            if isinstance(confidence_raw, (int, float)):
                confidence = float(confidence_raw)
            elif isinstance(confidence_raw, str):
                try:
                    confidence = float(confidence_raw.strip())
                except ValueError:
                    confidence = 0.0
            enriched[candidate_key] = PdfLabelEnrichment(
                candidate_key=candidate_key,
                label_text=label_text[:512],
                nearby_text="",
                input_kind=str(row.get("input_kind") or "text").strip() or "text",
                confidence=max(0.0, min(1.0, confidence)),
                section_hint=str(row.get("section_hint") or "").strip(),
                datapoint_description=self._combine_datapoint_context(row),
            )
        return enriched

    def _salvage_mapping_objects(self, text: str) -> list[dict[str, Any]]:
        # Look for standalone JSON objects that contain candidate_key and meaningful_label.
        # This is a best-effort recovery path for truncated outputs.
        results: list[dict[str, Any]] = []
        for match in re.finditer(r"\{[^{}]*\"candidate_key\"[^{}]*\}", text):
            candidate = self._try_json(match.group(0))
            if not isinstance(candidate, dict):
                continue
            if not str(candidate.get("candidate_key") or "").strip():
                continue
            if not str(candidate.get("meaningful_label") or candidate.get("label_text") or "").strip():
                continue
            results.append(candidate)
        return results

    def _combine_datapoint_context(self, row: dict[str, Any]) -> str:
        datapoint_path = str(row.get("datapoint_path") or "").strip()
        value_logic = str(row.get("value_logic") or row.get("datapoint_description") or "").strip()
        if datapoint_path and value_logic:
            return f"path={datapoint_path}; value_logic={value_logic}"
        if datapoint_path:
            return f"path={datapoint_path}"
        return value_logic

    def _extract_message_text(self, raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""
        choices = raw.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                extracted = self._content_to_text(content)
                if extracted:
                    return extracted
        predictions = raw.get("predictions")
        if isinstance(predictions, list):
            for prediction in predictions:
                if not isinstance(prediction, dict):
                    continue
                text = prediction.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return ""

    def _parse_json_text(self, value: str) -> dict[str, Any] | None:
        direct = self._try_json(value)
        if isinstance(direct, dict):
            return direct
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            nested = self._try_json(fenced.group(1))
            if isinstance(nested, dict):
                return nested
        first_obj = re.search(r"(\{(?:.|\n)*\})", value)
        if first_obj:
            nested = self._try_json(first_obj.group(1))
            if isinstance(nested, dict):
                return nested
        return None

    def _try_json(self, value: str) -> Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def _effective_token(self) -> str:
        return (self._settings.databricks_token or self._settings.openai_api_key or "").strip()

    def _workspace_client_for_serving(self) -> WorkspaceClient:
        if self._workspace_client is not None:
            return self._workspace_client
        host = (self._settings.databricks_host or "").strip()
        if not host:
            raise RuntimeError("DATABRICKS_HOST is required for Databricks label enrichment")
        token = self._effective_token()
        auth_type = (self._settings.databricks_auth_type or "").strip() or None
        if token:
            self._workspace_client = WorkspaceClient(
                host=host,
                token=token,
                auth_type=auth_type or "pat",
            )
            return self._workspace_client
        client_id = (self._settings.databricks_client_id or "").strip()
        client_secret = (self._settings.databricks_client_secret or "").strip()
        if client_id and client_secret:
            self._workspace_client = WorkspaceClient(
                host=host,
                client_id=client_id,
                client_secret=client_secret,
                auth_type=auth_type or "oauth-m2m",
            )
            return self._workspace_client
        raise RuntimeError("No Databricks credentials available for label enrichment")

    def _post_serving_json(
        self,
        *,
        model: str,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        client = self._workspace_client_for_serving()
        do_kwargs: dict[str, Any] = {"method": "POST", "path": f"/serving-endpoints/{model}/invocations", "body": payload}
        # Some databricks-sdk versions accept timeouts; others don't. Try to pass through when supported.
        for key in ("timeout", "timeout_seconds", "request_timeout"):
            do_kwargs[key] = timeout_seconds
            try:
                result = client.api_client.do(**do_kwargs)
                break
            except TypeError:
                do_kwargs.pop(key, None)
                result = None
        if result is None:
            result = client.api_client.do(method="POST", path=f"/serving-endpoints/{model}/invocations", body=payload)
        if isinstance(result, dict):
            return result
        raise RuntimeError("Unexpected Databricks serving response format")

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str) and content.strip():
            return content.strip()
        # Claude-style content blocks: [{"type":"text","text":"..."}]
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            joined = "\n".join(parts).strip()
            return joined
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        return ""

    def _detect_error_payload(self, raw: dict[str, Any]) -> str | None:
        # Common Databricks error shapes.
        error_code = raw.get("error_code")
        message = raw.get("message")
        if isinstance(error_code, str) and isinstance(message, str) and message.strip():
            return f"{error_code}: {message.strip()}"
        if isinstance(raw.get("error"), str) and str(raw.get("error")).strip():
            return str(raw.get("error")).strip()
        if isinstance(message, str) and message.strip() and "error" in message.lower():
            return message.strip()
        return None

    def _summarize_raw_response(self, raw: Any) -> str:
        if isinstance(raw, dict):
            keys = sorted(str(k) for k in raw.keys())
            # Include a short snippet of extractable text if present.
            text = self._extract_message_text(raw)
            snippet = (text[:400] + "...") if len(text) > 400 else text
            if snippet:
                return f"keys={keys}; text_snippet={snippet!r}"
            return f"keys={keys}"
        return f"type={type(raw).__name__}"
