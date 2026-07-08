from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib import error, request

from apps.api.pdf_scanner import PdfDatapointCandidate
from apps.api.settings import Settings


@dataclass(frozen=True)
class PdfLabelEnrichment:
    candidate_key: str
    label_text: str  # populated from OpenAI "label" field
    section: str = ""
    datapoint_intent: str = ""
    expected_value_type: str = ""
    context: str = ""
    # Legacy fields retained for the batch (candidates-only) path
    nearby_text: str = ""
    input_kind: str = ""
    confidence: float = 0.0
    section_hint: str = ""
    datapoint_description: str = ""


class PdfLabelEnricher(Protocol):
    def enrich_pdf(
        self,
        *,
        file_path: str | Path,
        candidates: list[PdfDatapointCandidate],
        page_count: int = 0,
    ) -> dict[str, PdfLabelEnrichment]:
        ...


class OpenAIPdfLabelEnricher:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._disabled = False
        self._last_response_summary = ""

    @property
    def last_response_summary(self) -> str:
        return self._last_response_summary

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.pdf_openai_label_enrichment_enabled
            and self._settings.pdf_openai_label_enrichment_api_key
        )

    def enrich_pdf(
        self,
        *,
        file_path: str | Path,
        candidates: list[PdfDatapointCandidate],
        page_count: int = 0,
    ) -> dict[str, PdfLabelEnrichment]:
        """
        Upload the PDF once to the OpenAI Files API, then call the Responses
        API once per page-range batch referencing the file_id. This avoids
        re-sending the full PDF bytes on every batch call.

        Results are merged by field_name back onto candidates.
        The uploaded file is deleted from OpenAI after all batches complete.
        """
        if self._disabled or not self.configured or not candidates:
            return {}

        path = Path(file_path).expanduser().resolve()
        try:
            pdf_bytes = path.read_bytes()
        except OSError as exc:
            self._disabled = True
            raise RuntimeError(f"Unable to read PDF for OpenAI scan: {path}") from exc

        # Upload PDF once — subsequent batch calls reference file_id only
        file_id = self._upload_pdf(path.name, pdf_bytes)

        total_pages = max(page_count, 1)
        pages_per_batch = max(1, self._settings.pdf_openai_pages_per_batch)
        max_fields_per_batch = max(1, self._settings.pdf_openai_max_fields_per_batch)

        # field_name → [candidate_key, ...] index for merging results back
        field_name_to_keys: dict[str, list[str]] = {}
        # page_number → [field_name, ...] for scoping each batch to its pages
        page_to_field_names: dict[int, list[str]] = {}
        for c in candidates:
            if c.field_name:
                field_name_to_keys.setdefault(c.field_name.upper(), []).append(c.candidate_key)
            if c.page_number and c.field_name:
                page_to_field_names.setdefault(c.page_number, []).append(c.field_name)

        # Build list of (page_start, page_end, field_names) for each batch.
        # Batches are split by page window and by maximum field count to keep
        # response size/model compliance stable on dense pages.
        batches: list[tuple[int, int, list[str]]] = []
        page = 1
        while page <= total_pages:
            page_end = min(page + pages_per_batch - 1, total_pages)
            field_names_for_batch: list[str] = []
            for pg in range(page, page_end + 1):
                field_names_for_batch.extend(page_to_field_names.get(pg, []))
            if not field_names_for_batch:
                batches.append((page, page_end, []))
            else:
                for start in range(0, len(field_names_for_batch), max_fields_per_batch):
                    chunk = field_names_for_batch[start : start + max_fields_per_batch]
                    batches.append((page, page_end, chunk))
            page = page_end + 1

        # field_name → page_number lookup for retry pass
        field_name_to_page: dict[str, int] = {
            c.field_name.upper(): c.page_number
            for c in candidates
            if c.field_name and c.page_number
        }

        enriched: dict[str, PdfLabelEnrichment] = {}
        summaries: list[str] = []

        def _run_batches(batch_list: list[tuple[int, int, list[str]]]) -> None:
            workers = max(1, min(len(batch_list), self._settings.pdf_openai_max_concurrent_batches))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        self._enrich_page_range,
                        file_id=file_id,
                        page_start=p_start,
                        page_end=p_end,
                        total_pages=total_pages,
                        field_names=fnames,
                    ): (p_start, p_end)
                    for p_start, p_end, fnames in batch_list
                }
                for future in as_completed(futures):
                    p_start, p_end = futures[future]
                    try:
                        batch_result, summary = future.result()
                    except Exception as exc:  # noqa: BLE001
                        summaries.append(f"pages {p_start}-{p_end} failed: {exc}")
                        continue
                    summaries.append(summary)
                    for field_name_upper, row_enrichment in batch_result.items():
                        for candidate_key in field_name_to_keys.get(field_name_upper, []):
                            enriched[candidate_key] = PdfLabelEnrichment(
                                candidate_key=candidate_key,
                                label_text=row_enrichment.label_text,
                                section=row_enrichment.section,
                                datapoint_intent=row_enrichment.datapoint_intent,
                                expected_value_type=row_enrichment.expected_value_type,
                                context=row_enrichment.context,
                            )

        try:
            # Pass 1: full concurrent run
            _run_batches(batches)

            # Pass 2: retry any field that wasn't returned, in small focused batches
            enriched_field_names = {
                fn_upper
                for fn_upper in field_name_to_keys
                if any(ck in enriched for ck in field_name_to_keys[fn_upper])
            }
            missed_field_names = [
                fn for fn in field_name_to_keys
                if fn not in enriched_field_names
            ]
            if missed_field_names:
                # Group missed fields by page, then build small retry batches (~30 fields each)
                retry_page_groups: dict[int, list[str]] = {}
                for fn_upper in missed_field_names:
                    pg = field_name_to_page.get(fn_upper)
                    if pg:
                        # Recover the original mixed-case field_name from the candidate
                        original_fn = next(
                            (c.field_name for c in candidates if c.field_name and c.field_name.upper() == fn_upper),
                            fn_upper,
                        )
                        retry_page_groups.setdefault(pg, []).append(original_fn)

                # Build retry batches: group consecutive pages, max 30 fields per batch
                retry_batches: list[tuple[int, int, list[str]]] = []
                current_fnames: list[str] = []
                current_page_start = current_page_end = -1
                retry_batch_size = 30
                for pg in sorted(retry_page_groups):
                    fnames_on_page = retry_page_groups[pg]
                    if current_page_start == -1:
                        current_page_start = pg
                    current_page_end = pg
                    current_fnames.extend(fnames_on_page)
                    if len(current_fnames) >= retry_batch_size:
                        retry_batches.append((current_page_start, current_page_end, list(current_fnames)))
                        current_fnames = []
                        current_page_start = current_page_end = -1
                if current_fnames:
                    retry_batches.append((current_page_start, current_page_end, list(current_fnames)))

                if retry_batches:
                    summaries.append(f"retry: {len(missed_field_names)} missed fields in {len(retry_batches)} batches")
                    _run_batches(retry_batches)
        finally:
            self._delete_file(file_id)

        self._last_response_summary = "; ".join(s for s in summaries if s)
        return enriched

    def _upload_pdf(self, filename: str, pdf_bytes: bytes) -> str:
        """Upload PDF to OpenAI Files API. Returns file_id."""
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="purpose"\r\n\r\n'
            f"user_data\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode() + pdf_bytes + f"\r\n--{boundary}--\r\n".encode()

        base = (self._settings.pdf_openai_label_enrichment_api_base or "https://api.openai.com/v1").rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        upload_url = f"{base}/files"

        req = request.Request(
            upload_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._settings.pdf_openai_label_enrichment_api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        timeout = max(60, self._settings.pdf_openai_label_enrichment_timeout_seconds)
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"OpenAI file upload failed HTTP {exc.code}: {body_text}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"OpenAI file upload failed: {exc}") from exc

        file_id = result.get("id") or ""
        if not file_id:
            raise RuntimeError(f"OpenAI file upload returned no id: {result}")
        return str(file_id)

    def _delete_file(self, file_id: str) -> None:
        """Delete an uploaded file from OpenAI Files API. Errors are silently ignored."""
        base = (self._settings.pdf_openai_label_enrichment_api_base or "https://api.openai.com/v1").rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        delete_url = f"{base}/files/{file_id}"
        req = request.Request(
            delete_url,
            headers={"Authorization": f"Bearer {self._settings.pdf_openai_label_enrichment_api_key}"},
            method="DELETE",
        )
        try:
            with request.urlopen(req, timeout=30):
                pass
        except Exception:  # noqa: BLE001
            pass  # best-effort cleanup

    def _enrich_page_range(
        self,
        *,
        file_id: str,
        page_start: int,
        page_end: int,
        total_pages: int,
        field_names: list[str] | None = None,
    ) -> tuple[dict[str, PdfLabelEnrichment], str]:
        """
        Call OpenAI Responses API for one page range, referencing the already-uploaded
        file_id. Returns dict keyed by UPPER-CASE field_name.
        """
        payload = self._build_page_range_payload(
            file_id=file_id,
            page_start=page_start,
            page_end=page_end,
            total_pages=total_pages,
            field_names=field_names or [],
        )
        endpoint = self._responses_endpoint()
        req = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._settings.pdf_openai_label_enrichment_api_key}",
            },
            method="POST",
        )
        timeout = max(1, self._settings.pdf_openai_label_enrichment_timeout_seconds)
        try:
            with request.urlopen(req, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            self._disabled = True
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(
                f"OpenAI PDF scan (pages {page_start}-{page_end}) failed with HTTP {exc.code}: {body}"
            ) from exc
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"OpenAI PDF scan (pages {page_start}-{page_end}) failed: {exc}") from exc

        summary = _summarize_openai_response(raw)
        return self._parse_page_range_response(raw), summary

    def _build_page_range_payload(
        self,
        *,
        file_id: str,
        page_start: int,
        page_end: int,
        total_pages: int,
        field_names: list[str],
    ) -> dict[str, object]:
        return {
            "model": self._settings.pdf_openai_label_enrichment_model,
            "max_output_tokens": 16000,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You are a survey metadata extractor for a higher-education survey PDF. "
                                "You will be given a list of AcroForm field names that appear on specific pages "
                                "of the attached PDF. For each field name, read the corresponding page and return: "
                                "the exact field_name as provided, the survey section heading, a concise human label, "
                                "a one-line datapoint_intent for Databricks Genie mapping, the expected value type, "
                                "and the verbatim question or column/row heading from the page. "
                                "Return exactly one output row per input field_name. Do not invent new field names. "
                                "Return JSON only."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_id": file_id,
                        },
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {
                                    "page_range": {
                                        "start": page_start,
                                        "end": page_end,
                                        "total_pages": total_pages,
                                    },
                                    "field_names": field_names,
                                    "instructions": (
                                        "Return a 'fields' array with exactly one item per field_name above. "
                                        "Each item must have: "
                                        "field_name (copied exactly from input, e.g. 'EN_TOT_UG_N'), "
                                        "section (survey section heading, e.g. 'B1 Enrollment'), "
                                        "label (concise human-readable label), "
                                        "datapoint_intent (one phrase for Genie, e.g. 'total undergraduate enrollment'), "
                                        "expected_value_type (one of: boolean, integer, decimal, text, select), "
                                        "context (verbatim question text or table column/row heading from the PDF page)."
                                    ),
                                },
                                ensure_ascii=True,
                            ),
                        },
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "pdf_label_enrichment",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "fields": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "field_name": {"type": "string"},
                                        "section": {"type": "string"},
                                        "label": {"type": "string"},
                                        "datapoint_intent": {"type": "string"},
                                        "expected_value_type": {"type": "string"},
                                        "context": {"type": "string"},
                                    },
                                    "required": [
                                        "field_name",
                                        "section",
                                        "label",
                                        "datapoint_intent",
                                        "expected_value_type",
                                        "context",
                                    ],
                                },
                            }
                        },
                        "required": ["fields"],
                    },
                }
            },
        }

    def _parse_page_range_response(self, raw: dict[str, object]) -> dict[str, PdfLabelEnrichment]:
        """Parse response into dict keyed by UPPER-CASE field_name."""
        text = _extract_output_text(raw)
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        rows = payload.get("fields") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return {}
        result: dict[str, PdfLabelEnrichment] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            field_name = str(row.get("field_name") or "").strip()
            label = str(row.get("label") or "").strip()
            if not field_name or not label:
                continue
            # Key by upper-case field_name for case-insensitive merge
            result[field_name.upper()] = PdfLabelEnrichment(
                candidate_key="",  # filled in during merge
                label_text=label[:512],
                section=str(row.get("section") or "").strip(),
                datapoint_intent=str(row.get("datapoint_intent") or "").strip(),
                expected_value_type=str(row.get("expected_value_type") or "").strip(),
                context=str(row.get("context") or "").strip(),
            )
        return result

    def enrich_candidates(self, candidates: list[PdfDatapointCandidate]) -> dict[str, PdfLabelEnrichment]:
        """Legacy text-only batch path (no PDF). Kept for compatibility."""
        if self._disabled or not self.configured or not candidates:
            return {}
        batch_size = max(1, min(50, self._settings.pdf_openai_label_enrichment_batch_size))
        enriched: dict[str, PdfLabelEnrichment] = {}
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start : start + batch_size]
            enriched.update(self._enrich_batch(batch))
        return enriched

    def _enrich_batch(self, candidates: list[PdfDatapointCandidate]) -> dict[str, PdfLabelEnrichment]:
        endpoint = self._responses_endpoint()
        payload = {
            "model": self._settings.pdf_openai_label_enrichment_model,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You normalize PDF survey form fields for downstream Databricks Genie mapping. "
                                "Use the field name, current label, and nearby visible text to infer the clearest "
                                "human label and datapoint intent. Keep labels concise and survey-specific. "
                                "Do not invent values. Return JSON only."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "candidate_key": candidate.candidate_key,
                                            "field_name": candidate.field_name,
                                            "label_text": candidate.label_text,
                                            "nearby_text": candidate.nearby_text,
                                            "input_kind": candidate.input_kind,
                                            "page_number": candidate.page_number,
                                        }
                                        for candidate in candidates
                                    ]
                                },
                                ensure_ascii=True,
                            ),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "pdf_label_enrichment",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "candidates": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "candidate_key": {"type": "string"},
                                        "label_text": {"type": "string"},
                                        "nearby_text": {"type": "string"},
                                        "input_kind": {"type": "string"},
                                        "confidence": {"type": "number"},
                                        "section_hint": {"type": "string"},
                                        "datapoint_description": {"type": "string"},
                                    },
                                    "required": [
                                        "candidate_key",
                                        "label_text",
                                        "nearby_text",
                                        "input_kind",
                                        "confidence",
                                        "section_hint",
                                        "datapoint_description",
                                    ],
                                },
                            }
                        },
                        "required": ["candidates"],
                    },
                }
            },
        }
        req = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._settings.pdf_openai_label_enrichment_api_key}",
            },
            method="POST",
        )
        timeout = max(1, self._settings.pdf_openai_label_enrichment_timeout_seconds)
        try:
            with request.urlopen(req, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            self._disabled = True
            return {}
        return self._parse_legacy_response(raw)

    def _parse_legacy_response(self, raw: dict[str, object]) -> dict[str, PdfLabelEnrichment]:
        """Parse legacy enrich_candidates (text-only) responses keyed by candidate_key."""
        text = _extract_output_text(raw)
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        rows = None
        if isinstance(payload, dict):
            rows = payload.get("candidates")
        if not isinstance(rows, list):
            return {}
        enriched: dict[str, PdfLabelEnrichment] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate_key = str(row.get("candidate_key") or "").strip()
            label_text = str(row.get("label_text") or "").strip()
            if not candidate_key or not label_text:
                continue
            try:
                confidence = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            enriched[candidate_key] = PdfLabelEnrichment(
                candidate_key=candidate_key,
                label_text=label_text[:512],
                nearby_text=str(row.get("nearby_text") or "").strip(),
                input_kind=str(row.get("input_kind") or "").strip(),
                confidence=max(0.0, min(1.0, confidence)),
                section_hint=str(row.get("section_hint") or "").strip(),
                datapoint_description=str(row.get("datapoint_description") or "").strip(),
            )
        return enriched

    def _responses_endpoint(self) -> str:
        base = (self._settings.pdf_openai_label_enrichment_api_base or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/responses" if base.endswith("/v1") else f"{base}/v1/responses"


def _extract_output_text(raw: dict[str, object]) -> str:
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


def _summarize_openai_response(raw: dict[str, object]) -> str:
    keys = sorted(str(key) for key in raw.keys())
    text = _extract_output_text(raw)
    if text:
        snippet = (text[:400] + "...") if len(text) > 400 else text
        return f"keys={keys}; text_snippet={snippet!r}"
    status = raw.get("status")
    if isinstance(status, str) and status:
        return f"keys={keys}; status={status}"
    return f"keys={keys}"
