from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


sys.path.insert(0, str(_repo_root()))

from apps.api.pdf_scanner import scan_pdf_datapoints  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan a survey PDF for datapoint fields.")
    parser.add_argument("pdf_path", help="Path to the survey PDF")
    parser.add_argument("--survey-id", default="uploaded_pdf", help="Survey identifier to attach to the scan")
    parser.add_argument("--max-text-candidates", type=int, default=200, help="Max fallback text candidates")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON")
    args = parser.parse_args()

    result = scan_pdf_datapoints(
        args.pdf_path,
        survey_id=args.survey_id,
        max_text_candidates=args.max_text_candidates,
    )
    indent = None if args.compact else 2
    print(json.dumps(result.to_dict(), indent=indent))


if __name__ == "__main__":
    main()
