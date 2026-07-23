
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz


@dataclass(frozen=True)
class PdfPageContext:
    scan_id: str
    page_number: int
    image_path: str
    context_json_path: str
    page_summary: str
    section_title: str
    table_titles: list[str]
    extracted_context: dict[str, Any]


def render_pdf_page_to_png(
    *,
    pdf_path: str,
    page_number: int,
    output_dir: str,
    zoom: float = 2.0,
) -> str:
    """
    Render a PDF page to PNG.

    page_number is 1-based.
    """
    source_path = Path(pdf_path)
    if not source_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    page_index = max(0, int(page_number) - 1)
    image_path = output_path / f"{source_path.stem}_page_{page_number}.png"

    if image_path.exists():
        return str(image_path)

    with fitz.open(source_path) as document:
        if page_index >= len(document):
            raise ValueError(f"Page {page_number} does not exist in PDF with {len(document)} pages")

        page = document[page_index]
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        pixmap.save(str(image_path))

    return str(image_path)


def page_context_paths(
    *,
    scan_id: str,
    page_number: int,
    base_dir: str,
) -> tuple[Path, Path]:
    root = Path(base_dir) / "pdf_page_contexts" / scan_id
    image_dir = root / "images"
    json_dir = root / "json"
    return image_dir, json_dir / f"page_{page_number}_context.json"


def load_page_context(
    *,
    scan_id: str,
    page_number: int,
    base_dir: str,
) -> PdfPageContext | None:
    _image_dir, context_path = page_context_paths(
        scan_id=scan_id,
        page_number=page_number,
        base_dir=base_dir,
    )
    if not context_path.exists():
        return None

    payload = json.loads(context_path.read_text(encoding="utf-8"))
    return PdfPageContext(
        scan_id=payload["scan_id"],
        page_number=int(payload["page_number"]),
        image_path=payload.get("image_path", ""),
        context_json_path=str(context_path),
        page_summary=payload.get("page_summary", ""),
        section_title=payload.get("section_title", ""),
        table_titles=list(payload.get("table_titles", [])),
        extracted_context=dict(payload.get("extracted_context", {})),
    )


def save_page_context(
    *,
    scan_id: str,
    page_number: int,
    image_path: str,
    base_dir: str,
    page_summary: str,
    section_title: str = "",
    table_titles: list[str] | None = None,
    extracted_context: dict[str, Any] | None = None,
) -> PdfPageContext:
    _image_dir, context_path = page_context_paths(
        scan_id=scan_id,
        page_number=page_number,
        base_dir=base_dir,
    )
    context_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "scan_id": scan_id,
        "page_number": page_number,
        "image_path": image_path,
        "page_summary": page_summary,
        "section_title": section_title,
        "table_titles": table_titles or [],
        "extracted_context": extracted_context or {},
    }

    context_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return PdfPageContext(
        scan_id=scan_id,
        page_number=page_number,
        image_path=image_path,
        context_json_path=str(context_path),
        page_summary=page_summary,
        section_title=section_title,
        table_titles=table_titles or [],
        extracted_context=extracted_context or {},
    )


def build_basic_page_context_from_candidates(
    *,
    scan_id: str,
    page_number: int,
    image_path: str,
    candidates: list[Any],
    base_dir: str,
) -> PdfPageContext:
    """
    Safe first version.

    This does not call external AI. It creates a reusable page-level context
    from the candidates already extracted from the PDF.
    """
    labels: list[str] = []
    field_names: list[str] = []
    nearby_samples: list[str] = []

    for candidate in candidates:
        label = (getattr(candidate, "label_text", "") or "").strip()
        field_name = (getattr(candidate, "field_name", "") or "").strip()
        nearby_text = (getattr(candidate, "nearby_text", "") or "").strip()

        if label and label not in labels:
            labels.append(label)
        if field_name and field_name not in field_names:
            field_names.append(field_name)
        if nearby_text and nearby_text not in nearby_samples:
            nearby_samples.append(nearby_text)

    page_summary = (
        f"PDF page {page_number} contains {len(candidates)} fillable fields. "
        f"Visible labels include: {', '.join(labels[:30])}. "
        f"PDF field names include: {', '.join(field_names[:30])}."
    )

    extracted_context = {
        "field_count": len(candidates),
        "labels": labels[:100],
        "field_names": field_names[:100],
        "nearby_text_samples": nearby_samples[:30],
        "note": (
            "This is a cached page-level context. It should be reused for all fields on this page. "
            "A future vision/OCR extractor can replace or enrich this JSON using the page screenshot."
        ),
    }

    return save_page_context(
        scan_id=scan_id,
        page_number=page_number,
        image_path=image_path,
        base_dir=base_dir,
        page_summary=page_summary,
        section_title="",
        table_titles=[],
        extracted_context=extracted_context,
    )
