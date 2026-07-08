from __future__ import annotations

from pathlib import Path
from typing import Any

from apps.api import main as api_main
from infra.scripts import pull_real_fake_form_data
from infra.scripts import run_website_form_fill


def test_full_workflow_passes_current_browser_session_and_max_steps(monkeypatch) -> None:
    commands: dict[str, list[str]] = {}

    def fake_run_command_step(
        *,
        job_id: str,
        name: str,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str],
    ) -> dict[str, Any]:
        del job_id, cwd, env
        commands[name] = cmd
        return {"parsed_output": {"step": name}}

    monkeypatch.setattr(api_main, "_job_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_main, "_run_command_step", fake_run_command_step)

    payload = api_main.FullWorkflowLaunchRequest(
        portal_url="https://survey.example/form",
        timeout_seconds=1200,
        survey_year=2025,
        validate=True,
        browser_session_id="sess_current_browser",
        skyvern_max_steps=140,
    )

    api_main._execute_full_workflow_job("job_fast_web", payload)

    pull_cmd = commands["pull_real_data"]
    run_cmd = commands["run_full_fill"]
    assert "--browser-session-id" not in pull_cmd
    assert run_cmd[1].endswith("run_website_form_fill.py")
    assert run_cmd[run_cmd.index("--browser-session-id") + 1] == "sess_current_browser"
    assert run_cmd[run_cmd.index("--max-steps") + 1] == "140"
    assert run_cmd[run_cmd.index("--url") + 1] == "https://survey.example/form"


def test_skyvern_run_body_is_direct_fill_prompt_for_current_browser_session() -> None:
    body = run_website_form_fill.build_run_body(
        url="https://survey.example/form",
        payload_data={"enrolled_total": 1234, "applied_total": 4567},
        max_steps=90,
        browser_session_id="sess_fast_web",
    )

    prompt = str(body["prompt"])
    assert body["url"] == "https://survey.example/form"
    assert body["max_steps"] == 90
    assert body["browser_session_id"] == "sess_fast_web"
    assert "already-open browser session" in prompt
    assert "visible labels" in prompt
    assert "field names" in prompt
    assert "option text" in prompt
    assert "Do not click final Submit" in prompt
    assert "readback_count" in body["data_extraction_schema"]["fill_summary"]["description"]


def test_skyvern_run_body_includes_login_instructions_when_credentials_provided() -> None:
    body = run_website_form_fill.build_run_body(
        url="https://survey.example/login",
        payload_data={"field_a": "1"},
        max_steps=60,
        login_username="survey.user@example.edu",
        login_password="secret-pass",
    )

    prompt = str(body["prompt"])
    assert "requires login" in prompt
    assert "survey.user@example.edu" in prompt
    assert "secret-pass" in prompt


def test_full_workflow_passes_login_credentials_via_env_not_command(monkeypatch) -> None:
    commands: dict[str, list[str]] = {}
    env_snapshots: dict[str, dict[str, str]] = {}

    def fake_run_command_step(
        *,
        job_id: str,
        name: str,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str],
    ) -> dict[str, Any]:
        del job_id, cwd
        commands[name] = cmd
        env_snapshots[name] = dict(env)
        return {"parsed_output": {"step": name}}

    monkeypatch.setattr(api_main, "_job_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_main, "_run_command_step", fake_run_command_step)

    payload = api_main.FullWorkflowLaunchRequest(
        portal_url="https://survey.example/form",
        timeout_seconds=1200,
        needs_login=True,
        username="portal.user",
        password="portal-pass",
    )

    api_main._execute_full_workflow_job("job_login_web", payload)

    run_cmd = commands["run_full_fill"]
    run_env = env_snapshots["run_full_fill"]
    assert "portal-pass" not in " ".join(run_cmd)
    assert "portal.user" not in " ".join(run_cmd)
    assert run_env["WEBSITE_LOGIN_USERNAME"] == "portal.user"
    assert run_env["WEBSITE_LOGIN_PASSWORD"] == "portal-pass"


def test_workflow_request_for_storage_excludes_login_credentials() -> None:
    payload = api_main.FullWorkflowLaunchRequest(
        needs_login=True,
        username="portal.user",
        password="portal-pass",
    )

    stored = api_main._workflow_request_for_storage(payload)

    assert stored["needs_login"] is True
    assert "username" not in stored
    assert "password" not in stored


def test_launch_job_rejects_missing_login_credentials() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(api_main.app)
    response = client.post(
        "/website-ops/full-workflow/jobs",
        json={
            "portal_url": "https://survey.example/form",
            "needs_login": True,
            "username": "",
            "password": "",
        },
    )

    assert response.status_code == 400


def test_full_workflow_auto_generates_browser_session_when_use_current_browser(monkeypatch) -> None:
    commands: dict[str, list[str]] = {}

    def fake_run_command_step(
        *,
        job_id: str,
        name: str,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str],
    ) -> dict[str, Any]:
        del job_id, cwd, env
        commands[name] = cmd
        return {"parsed_output": {"step": name}}

    monkeypatch.setattr(api_main, "_job_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_main, "_run_command_step", fake_run_command_step)

    payload = api_main.FullWorkflowLaunchRequest(
        portal_url="https://survey.example/form",
        use_current_browser=True,
    )

    api_main._execute_full_workflow_job("job_sess_auto", payload)

    run_cmd = commands["run_full_fill"]
    session_idx = run_cmd.index("--browser-session-id")
    assert run_cmd[session_idx + 1].startswith("sess_")


def test_browser_check_reports_not_configured_when_cdp_disabled(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_TYPE", "chromium-headful")
    monkeypatch.delenv("BROWSER_REMOTE_DEBUGGING_URL", raising=False)

    from fastapi.testclient import TestClient

    client = TestClient(api_main.app)
    response = client.get("/website-ops/browser-check")

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert "CDP mode is not active" in body["message"]


def test_website_fill_uses_example_payload_when_generated_data_is_missing(tmp_path) -> None:
    generated_path = tmp_path / "fake-survey-form-data.json"
    example_path = tmp_path / "fake-survey-form-data.example.json"
    example_path.write_text("{}")

    assert run_website_form_fill.select_payload_input_path(generated_path) == example_path


def test_full_workflow_uses_example_payload_as_pull_input_when_generated_data_is_missing(tmp_path) -> None:
    generated_path = tmp_path / "fake-survey-form-data.json"
    example_path = tmp_path / "fake-survey-form-data.example.json"
    example_path.write_text("{}")

    assert api_main._fake_form_input_data_path(generated_path) == example_path


def test_real_data_pull_uses_example_payload_when_generated_data_is_missing(tmp_path) -> None:
    generated_path = tmp_path / "fake-survey-form-data.json"
    example_path = tmp_path / "fake-survey-form-data.example.json"
    example_path.write_text("{}")

    assert pull_real_fake_form_data.select_baseline_input_path(generated_path) == example_path
