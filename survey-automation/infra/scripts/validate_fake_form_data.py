from __future__ import annotations

import argparse
import json
from pathlib import Path


GENDERS = ("men", "women", "other")
TIME_STATUSES = ("ft", "pt")


def number(data: dict[str, object], key: str) -> int:
    raw = data.get(key)
    if raw is None or raw == "":
        return 0
    return int(raw)


def validate_payload(path: Path) -> tuple[bool, list[str]]:
    data = json.loads(path.read_text())
    errors: list[str] = []

    for metric in ("applied", "admitted", "enrolled"):
        total_key = f"{metric}_total"
        component_total = sum(number(data, f"{metric}_{gender}") for gender in GENDERS)
        if component_total != number(data, total_key):
            errors.append(f"{total_key} mismatch: expected {component_total}, found {number(data, total_key)}")

    ug_total = sum(number(data, f"{time_status}_total_ug_{gender}") for time_status in TIME_STATUSES for gender in GENDERS)
    if ug_total != number(data, "total_undergraduates"):
        errors.append(
            f"total_undergraduates mismatch: expected {ug_total}, found {number(data, 'total_undergraduates')}"
        )

    grad_total = sum(
        number(data, f"{time_status}_total_grad_{gender}") for time_status in TIME_STATUSES for gender in GENDERS
    )
    if grad_total != number(data, "total_graduates"):
        errors.append(f"total_graduates mismatch: expected {grad_total}, found {number(data, 'total_graduates')}")

    enrolled_from_grid = sum(
        number(data, f"{time_status}_ftf_{gender}") for time_status in TIME_STATUSES for gender in GENDERS
    )
    if enrolled_from_grid != number(data, "enrolled_total"):
        errors.append(f"enrolled_total mismatch: expected {enrolled_from_grid}, found {number(data, 'enrolled_total')}")

    if number(data, "total_undergraduates") + number(data, "total_graduates") != number(data, "grand_total_enrollment"):
        errors.append(
            "grand_total_enrollment mismatch: expected "
            f"{number(data, 'total_undergraduates') + number(data, 'total_graduates')}, "
            f"found {number(data, 'grand_total_enrollment')}"
        )

    return len(errors) == 0, errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate fake-survey-form-data.json consistency checks.")
    parser.add_argument("input", type=Path, help="Path to fake-survey-form-data.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ok, errors = validate_payload(args.input)
    if ok:
        print(json.dumps({"ok": True, "input": str(args.input)}, indent=2))
        return

    print(json.dumps({"ok": False, "input": str(args.input), "errors": errors}, indent=2))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
