from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from apps.api.settings import get_settings


TERMINAL_STATUSES = {"completed", "failed", "terminated", "timed_out", "canceled"}


def _request_json(url: str, method: str, headers: dict[str, str], body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {details}") from exc


def select_payload_input_path(generated_path: Path) -> Path:
    if generated_path.exists():
        return generated_path

    example_path = generated_path.with_name("fake-survey-form-data.example.json")
    if example_path.exists():
        return example_path

    return generated_path


def parse_args() -> argparse.Namespace:
    default_data_path = Path(__file__).resolve().parents[3] / "fake-survey-form" / "fake-survey-form-data.json"

    parser = argparse.ArgumentParser(description="Run a single Skyvern task to fill a website survey form.")

    parser.add_argument("--url", default="http://fake-form/?realData=1", help="Target form URL reachable by Skyvern.")

    parser.add_argument(
        "--data",
        type=Path,
        default=select_payload_input_path(default_data_path),
        help="Path to JSON with resolved field_name -> value mappings.",
    )

    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Skyvern max steps for this website form run. Use 30 for small tests; increase later.",
    )

    parser.add_argument("--poll-seconds", type=float, default=3.0, help="Polling interval for run status.")

    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Total timeout waiting for terminal status.",
    )

    parser.add_argument("--browser-session-id", default=None, help="Reuse an existing Skyvern browser session.")

    parser.add_argument(
        "--limit-fields",
        type=int,
        default=10,
        help=(
            "Debug mode: only send the first N scalar fields to Skyvern. "
            "Use 10 first. Set 0 to send all fields."
        ),
    )

    parser.add_argument(
        "--debug-output",
        type=Path,
        default=None,
        help="Optional path to write the exact Skyvern request body for debugging.",
    )

    return parser.parse_args()


def _is_scalar_fill_value(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _flatten_payload(payload_data: dict[str, Any]) -> dict[str, Any]:
    """
    Keep the current simple field_name -> value shape.

    If the payload ever contains nested objects, this avoids sending large nested
    structures directly to Skyvern during the small debug test.
    """
    flattened: dict[str, Any] = {}

    for key, value in payload_data.items():
        if _is_scalar_fill_value(value):
            flattened[str(key)] = "" if value is None else value

    return flattened


def _limit_payload(payload_data: dict[str, Any], limit_fields: int) -> dict[str, Any]:
    flattened = _flatten_payload(payload_data)

    if limit_fields <= 0:
        return flattened

    # Prefer common first-section / institution-profile fields when present.
    preferred_keys = [
        "institution_name",
        "display_name",
        "mailing_address",
        "city",
        "state",
        "zip",
        "zipcode",
        "phone",
        "email",
        "website",
        "admissions_email",
        "admissions_phone",
    ]

    selected: dict[str, Any] = {}

    for key in preferred_keys:
        if key in flattened and len(selected) < limit_fields:
            selected[key] = flattened[key]

    for key, value in flattened.items():
        if len(selected) >= limit_fields:
            break
        if key not in selected:
            selected[key] = value

    return selected


def build_run_body(
    *,
    url: str,
    payload_data: dict,
    max_steps: int,
    browser_session_id: str | None = None,
    login_username: str | None = None,
    login_password: str | None = None,
) -> dict:
    login_block = ""
    if login_username and login_password:
        login_block = (
            "The survey website requires login before filling. If you see a sign-in page, log in first "
            "using these credentials, then navigate to the survey form URL and continue filling. "
            "Use the credentials only for this run.\n"
            f"Login username: {login_username}\n"
            f"Login password: {login_password}\n\n"
        )

    run_prompt = (
        login_block
        + "Use the already-open browser session when a browser_session_id is supplied. "
        "Open or focus the survey form URL.\n\n"
        "IMPORTANT DEBUG MODE INSTRUCTIONS:\n"
        "You are filling a small test subset of fields, not the entire survey.\n"
        "Do not plan the whole form.\n"
        "Do not inspect future sections unless none of the provided fields are visible.\n"
        "Start with the first visible section of the form.\n"
        "Fill only fields that match the provided field_name -> value mapping.\n"
        "Prefer exact matches using input field names, id, data-testid, aria-label, placeholder, visible labels, "
        "and nearby section headings.\n"
        "For each mapped value, set the matching input, select, textarea, radio group, or checkbox group.\n"
        "For checkbox arrays, ensure exactly the requested option text values are selected.\n"
        "After filling the visible matching fields, read back only the fields you changed.\n"
        "Then stop. Do not continue exploring the entire form.\n"
        "Report a concise JSON summary with filled_count, skipped_count, readback_count, submit_attempted, and notes.\n"
        "Do not click final Submit, Finalize, Certify, or Send. Draft save actions are allowed only if clearly safe.\n"
        f"\n\nField mapping JSON:\n{json.dumps(payload_data, ensure_ascii=True)}"
    )

    run_body: dict = {
        "prompt": run_prompt,
        "url": url,
        "engine": "skyvern-2.0",
        "title": "Debug website survey fill from resolved values",
        "data_extraction_schema": {
            "fill_summary": {
                "type": "string",
                "description": (
                    "JSON string with keys filled_count, skipped_count, readback_count, "
                    "submit_attempted, and notes"
                ),
            }
        },
        "max_steps": max_steps,
        "run_with": "agent",
    }

    clean_session_id = (browser_session_id or "").strip()
    if clean_session_id:
        run_body["browser_session_id"] = clean_session_id

    return run_body


def main() -> None:
    settings = get_settings()
    args = parse_args()

    if not settings.skyvern_api_key:
        raise RuntimeError("SKYVERN_API_KEY is required")

    raw_payload_data = json.loads(args.data.read_text())
    payload_data = _limit_payload(raw_payload_data, args.limit_fields)

    if not payload_data:
        raise RuntimeError(f"No scalar field values found in payload file: {args.data}")

    print(
        json.dumps(
            {
                "event": "selected_payload_fields",
                "source": str(args.data),
                "limit_fields": args.limit_fields,
                "selected_count": len(payload_data),
                "selected_keys": list(payload_data.keys()),
            },
            indent=2,
        )
    )

    login_username = (os.getenv("WEBSITE_LOGIN_USERNAME") or "").strip() or None
    login_password = os.getenv("WEBSITE_LOGIN_PASSWORD") or None

    run_body = build_run_body(
        url=args.url,
        payload_data=payload_data,
        max_steps=args.max_steps,
        browser_session_id=args.browser_session_id,
        login_username=login_username,
        login_password=login_password,
    )

    if args.debug_output:
        args.debug_output.parent.mkdir(parents=True, exist_ok=True)
        args.debug_output.write_text(json.dumps(run_body, indent=2))
        print(json.dumps({"event": "debug_request_written", "path": str(args.debug_output)}, indent=2))

    headers = {
        "x-api-key": settings.skyvern_api_key,
        "Content-Type": "application/json",
    }

    create_response = _request_json(
        url=f"{settings.skyvern_base_url.rstrip('/')}/v1/run/tasks",
        method="POST",
        headers=headers,
        body=run_body,
    )

    run_id = str(create_response.get("run_id") or "").strip()
    if not run_id:
        raise RuntimeError(f"Missing run_id in create response: {create_response}")

    print(json.dumps({"event": "skyvern_run_created", "run_id": run_id}, indent=2))

    deadline = time.monotonic() + args.timeout_seconds
    latest = create_response

    while time.monotonic() < deadline:
        status = str(latest.get("status") or "").lower()
        if status in TERMINAL_STATUSES:
            break

        time.sleep(args.poll_seconds)

        latest = _request_json(
            url=f"{settings.skyvern_base_url.rstrip('/')}/v1/runs/{run_id}",
            method="GET",
            headers={"x-api-key": settings.skyvern_api_key},
        )

        print(
            json.dumps(
                {
                    "event": "skyvern_run_poll",
                    "run_id": run_id,
                    "status": latest.get("status"),
                    "step_count": latest.get("step_count"),
                }
            )
        )

    status = str(latest.get("status") or "").lower()

    if status not in TERMINAL_STATUSES:
        raise TimeoutError(f"Skyvern run did not reach terminal status in {args.timeout_seconds}s: run_id={run_id}")

    result = {
        "run_id": run_id,
        "status": latest.get("status"),
        "failure_reason": latest.get("failure_reason"),
        "app_url": latest.get("app_url"),
        "recording_url": latest.get("recording_url"),
        "step_count": latest.get("step_count"),
        "output": latest.get("output"),
    }

    print(json.dumps(result, indent=2))

    if status != "completed":
        raise RuntimeError(f"Skyvern run finished with non-completed status: {status}")


if __name__ == "__main__":
    main()