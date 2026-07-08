from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader


_WORD_HINTS = {
    "accommodation",
    "admission",
    "admissions",
    "applicant",
    "applicants",
    "application",
    "award",
    "cip",
    "degree",
    "enrollment",
    "faculty",
    "female",
    "freshman",
    "gender",
    "graduation",
    "headcount",
    "institution",
    "male",
    "number",
    "pell",
    "percent",
    "program",
    "rate",
    "retention",
    "student",
    "students",
    "term",
    "total",
    "transfer",
    "undergraduate",
    "unknown",
    "year",
}


@dataclass(frozen=True)
class PdfDatapointCandidate:
    candidate_key: str
    source: str
    field_name: str
    label_text: str
    normalized_label: str
    input_kind: str
    page_number: int | None
    confidence: float
    label_source: str = ""
    field_rect: list[float] | None = None
    nearby_text: str = ""


@dataclass(frozen=True)
class WidgetMetadata:
    field_name: str
    page_number: int
    rect: list[float]
    nearby_text: str


@dataclass(frozen=True)
class PdfScanResult:
    file_path: str
    file_name: str
    file_sha256: str
    survey_id: str
    fillable: bool
    page_count: int
    candidates: list[PdfDatapointCandidate]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [asdict(candidate) for candidate in self.candidates]
        return payload


def scan_pdf_datapoints(
    file_path: str | Path,
    *,
    survey_id: str = "uploaded_pdf",
    max_text_candidates: int = 200,
) -> PdfScanResult:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    if not path.is_file():
        raise ValueError(f"PDF path is not a file: {path}")

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Unable to read PDF: {path}") from exc
    acroform_candidates = _scan_acroform_fields(reader)
    fillable = bool(acroform_candidates)
    candidates = acroform_candidates
    if not candidates:
        candidates = _scan_text_candidates(reader, max_candidates=max_text_candidates)

    return PdfScanResult(
        file_path=str(path),
        file_name=path.name,
        file_sha256=_sha256_file(path),
        survey_id=survey_id,
        fillable=fillable,
        page_count=len(reader.pages),
        candidates=candidates,
    )


def _scan_acroform_fields(reader: PdfReader) -> list[PdfDatapointCandidate]:
    fields = reader.get_fields() or {}
    widgets_by_name = _scan_widget_metadata(reader)
    candidates: list[PdfDatapointCandidate] = []
    seen_field_names: set[str] = set()

    for field_name, field in fields.items():
        raw_name = _pdf_string(field_name)
        if not raw_name or raw_name in seen_field_names:
            continue
        seen_field_names.add(raw_name)
        widget = widgets_by_name.get(raw_name)
        tooltip = _pdf_string(_field_get(field, "/TU"))
        if widget and not tooltip:
            tooltip = _pdf_string(_field_get(_get_widget_field_object(reader, widget), "/TU"))
        label, label_source, confidence = _select_acroform_label(
            field_name=raw_name,
            tooltip=tooltip,
            nearby_text=widget.nearby_text if widget else "",
        )
        normalized = _normalize_label(label)
        if not normalized:
            continue
        candidates.append(
            PdfDatapointCandidate(
                candidate_key=f"acroform.{_slugify(raw_name or label)}",
                source="acroform",
                field_name=raw_name or _slugify(label),
                label_text=label,
                normalized_label=normalized,
                input_kind=_infer_acroform_input_kind(field),
                page_number=widget.page_number if widget else None,
                confidence=confidence,
                label_source=label_source,
                field_rect=widget.rect if widget else None,
                nearby_text=widget.nearby_text if widget else "",
            )
        )
    return candidates


def _scan_widget_metadata(reader: PdfReader) -> dict[str, WidgetMetadata]:
    widgets: dict[str, WidgetMetadata] = {}
    for page_index, page in enumerate(reader.pages):
        annots = page.get("/Annots") or []
        for annot_ref in annots:
            annot = _safe_get_object(annot_ref)
            if _pdf_name(_field_get(annot, "/Subtype")) != "Widget":
                continue
            field_name = _resolve_inherited_pdf_string(annot, "/T")
            if not field_name:
                continue
            rect = _parse_rect(_field_get(annot, "/Rect"))
            nearby_text = _nearby_text_for_rect(page, rect) if rect else ""
            widgets.setdefault(
                field_name,
                WidgetMetadata(
                    field_name=field_name,
                    page_number=page_index + 1,
                    rect=rect,
                    nearby_text=nearby_text,
                ),
            )
    return widgets


def _get_widget_field_object(reader: PdfReader, widget: WidgetMetadata) -> Any:
    try:
        page = reader.pages[widget.page_number - 1]
    except IndexError:
        return None
    for annot_ref in page.get("/Annots") or []:
        annot = _safe_get_object(annot_ref)
        if _resolve_inherited_pdf_string(annot, "/T") == widget.field_name:
            return annot
    return None


def _scan_text_candidates(reader: PdfReader, *, max_candidates: int) -> list[PdfDatapointCandidate]:
    candidates: list[PdfDatapointCandidate] = []
    seen: set[str] = set()
    for page_index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        for label in _text_label_candidates(text):
            normalized = _normalize_label(label)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(
                PdfDatapointCandidate(
                    candidate_key=f"text.p{page_index + 1}.{_slugify(label)}",
                    source="text",
                    field_name="",
                    label_text=label,
                    normalized_label=normalized,
                    input_kind="unknown",
                    page_number=page_index + 1,
                    confidence=0.45,
                )
            )
            if len(candidates) >= max_candidates:
                return candidates
    return candidates


def _text_label_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for raw_line in text.splitlines():
        line = _clean_label(raw_line)
        if not _looks_like_datapoint_label(line):
            continue
        candidates.append(line)
    return candidates


def _looks_like_datapoint_label(line: str) -> bool:
    if len(line) < 4 or len(line) > 220:
        return False
    if re.fullmatch(r"[\W_]+", line):
        return False
    lower = line.lower()
    words = set(re.findall(r"[a-z][a-z-]+", lower))
    has_hint_word = bool(words & _WORD_HINTS)
    has_prompt_punctuation = line.endswith((":", "?")) or "____" in line
    has_value_slot = bool(re.search(r"\b(number|total|percent|rate|count)\b", lower))
    if line.endswith(".") and not has_value_slot:
        return False
    return has_prompt_punctuation or has_hint_word


def _select_acroform_label(field_name: str, tooltip: str, nearby_text: str) -> tuple[str, str, float]:
    tooltip = _clean_label(tooltip)
    if tooltip and not _looks_internal_field_label(field_name, tooltip):
        return tooltip, "tooltip", 0.95

    nearby_text = _clean_label(nearby_text)
    if _normalize_label(nearby_text) and not _looks_internal_field_label(field_name, nearby_text):
        return nearby_text, "nearby_text", 0.85

    label = tooltip or _humanize_field_name(field_name)
    return label, "field_name", 0.65


def _looks_internal_field_label(field_name: str, label_text: str) -> bool:
    field_slug = _normalize_label(_humanize_field_name(field_name))
    label_slug = _normalize_label(label_text)
    if field_slug and field_slug == label_slug:
        return True
    return False


def _nearby_text_for_rect(page: Any, rect: list[float]) -> str:
    chunks: list[tuple[float, str]] = []
    try:
        page.extract_text(visitor_text=_text_visitor_for_rect(rect, chunks))
    except TypeError:
        return ""
    except Exception:
        return ""
    if not chunks:
        return ""
    chunks.sort(key=lambda item: item[0])
    return _clean_label(chunks[0][1])


def _text_visitor_for_rect(rect: list[float], chunks: list[tuple[float, str]]):
    left, bottom, right, top = rect
    mid_y = (bottom + top) / 2

    def visitor(text: str, _cm: Any, tm: Any, _font_dict: Any, _font_size: Any) -> None:
        cleaned = _clean_label(text)
        if not cleaned or len(cleaned) > 180:
            return
        if not _normalize_label(cleaned):
            return
        try:
            x = float(tm[4])
            y = float(tm[5])
        except (TypeError, ValueError, IndexError):
            return

        same_line_left = x <= left + 8 and abs(y - mid_y) <= 14
        above_field = bottom <= y <= top + 42 and left - 40 <= x <= right + 40
        if not same_line_left and not above_field:
            return

        distance = abs(y - mid_y) + max(0.0, x - left) / 10
        if same_line_left:
            distance -= 20
        chunks.append((distance, cleaned))

    return visitor


def _infer_acroform_input_kind(field: Any) -> str:
    field_type = _pdf_name(_field_get(field, "/FT"))
    if field_type == "Tx":
        multiline = _field_flag_enabled(field, 12)
        return "textarea" if multiline else "text"
    if field_type == "Ch":
        return "select"
    if field_type == "Btn":
        if _field_flag_enabled(field, 16):
            return "button"
        if _field_flag_enabled(field, 15):
            return "radio"
        return "checkbox"
    return "unknown"


def _field_flag_enabled(field: Any, bit_position: int) -> bool:
    try:
        flags = int(_field_get(field, "/Ff") or 0)
    except (TypeError, ValueError):
        return False
    return bool(flags & (1 << bit_position))


def _field_get(field: Any, key: str) -> Any:
    if hasattr(field, "get"):
        return field.get(key)
    return None


def _safe_get_object(value: Any) -> Any:
    if hasattr(value, "get_object"):
        return value.get_object()
    return value


def _resolve_inherited_pdf_string(field: Any, key: str) -> str:
    current = field
    visited = 0
    while current is not None and visited < 12:
        value = _field_get(current, key)
        if value is not None:
            return _pdf_string(value)
        current = _safe_get_object(_field_get(current, "/Parent"))
        visited += 1
    return ""


def _parse_rect(value: Any) -> list[float]:
    if not value:
        return []
    try:
        rect = [float(item) for item in value]
    except (TypeError, ValueError):
        return []
    if len(rect) != 4:
        return []
    left, bottom, right, top = rect
    return [min(left, right), min(bottom, top), max(left, right), max(bottom, top)]


def _pdf_name(value: Any) -> str:
    text = _pdf_string(value)
    return text[1:] if text.startswith("/") else text


def _pdf_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _humanize_field_name(field_name: str) -> str:
    text = re.sub(r"[_\-.]+", " ", field_name)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return _clean_label(text).title() if text else ""


def _clean_label(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = re.sub(r"\s*[_\.]{3,}\s*$", "", cleaned).strip()
    return cleaned


def _normalize_label(value: str) -> str:
    value = _clean_label(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", _normalize_label(value))
    slug = slug.strip("_")
    return slug[:96] or "field"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
