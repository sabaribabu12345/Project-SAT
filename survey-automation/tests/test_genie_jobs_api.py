from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api import main as api_main
from apps.api.db.session import get_session
from apps.api.main import app


class _FailingSkyvernService:
    def execute_section_pipeline(self, **_: object) -> dict[str, object]:
        raise RuntimeError("Skyvern request failed after 3 attempts: <urlopen error [Errno -2] Name or service not known>")

    def execute_draft_fill_pipeline(self, **_: object) -> dict[str, object]:
        raise RuntimeError("Skyvern request failed after 3 attempts: <urlopen error [Errno -2] Name or service not known>")


def test_launch_genie_draft_job_requires_known_scan() -> None:
    client = TestClient(app)

    response = client.post(
        "/pdf-scans/pdfscan_missing/genie-draft-mappings/jobs",
        json={
            "min_score": 70,
            "include_already_mapped": False,
            "limit_candidates": 50,
            "provider": "heuristic",
            "overwrite_existing": True,
            "genie_batch_size": 50,
        },
    )

    assert response.status_code == 404
    assert "Unknown scan_id" in response.json()["detail"]


def test_get_genie_draft_job_unknown_id_returns_404() -> None:
    client = TestClient(app)

    response = client.get("/pdf-scans/pdfscan_missing/genie-draft-mappings/jobs/pdfjob_missing")

    assert response.status_code == 404
    assert "Unknown job_id" in response.json()["detail"]


def test_list_genie_call_history_unknown_scan_returns_404() -> None:
    client = TestClient(app)

    response = client.get("/pdf-scans/pdfscan_missing/genie-calls")

    assert response.status_code == 404
    assert "Unknown scan_id" in response.json()["detail"]


def test_execute_draft_fill_pipeline_surfaces_skyvern_errors(monkeypatch) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = lambda: object()

    def _build_service(_session: object) -> _FailingSkyvernService:
        return _FailingSkyvernService()

    try:
        monkeypatch.setattr(api_main, "_build_slice1_service", _build_service)
        response = client.post(
            "/runs/run_test/execute-draft-fill-pipeline",
            json={
                "section_id": "pdf_survey",
                "portal_url": "http://fake-form/?realData=1",
                "scan_id": "scan_test",
            },
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert response.status_code == 400
    assert "Skyvern request failed after 3 attempts" in response.json()["detail"]


def test_execute_section_pipeline_surfaces_skyvern_errors(monkeypatch) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = lambda: object()

    def _build_service(_session: object) -> _FailingSkyvernService:
        return _FailingSkyvernService()

    try:
        monkeypatch.setattr(api_main, "_build_slice1_service", _build_service)
        response = client.post(
            "/runs/run_test/execute-section-pipeline",
            json={
                "section_id": "pdf_survey",
                "portal_url": "http://fake-form/?realData=1",
            },
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert response.status_code == 400
    assert "Skyvern request failed after 3 attempts" in response.json()["detail"]
