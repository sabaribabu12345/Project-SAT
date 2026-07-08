from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import app


def test_fill_preview_merges_pdf_and_web_values(tmp_path, monkeypatch) -> None:
    example = tmp_path / "fake-survey-form-data.example.json"
    example.write_text(json.dumps({"applied_men": "100", "institution_name": "Example U"}))

    import apps.api.main as api_main

    monkeypatch.setattr(api_main, "_fake_form_data_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(api_main, "_fake_form_input_data_path", lambda _: example)

    client = TestClient(app)
    response = client.get("/data-points/fill-preview")

    assert response.status_code == 200
    body = response.json()
    assert body["web_ready_count"] >= 2
    keys = {row["field_key"] for row in body["rows"]}
    assert "applied_men" in keys
    assert "institution_name" in keys
    applied = next(row for row in body["rows"] if row["field_key"] == "applied_men")
    assert applied["web_value"] == "100"
    assert applied["web_ready"] is True


def test_data_points_page_shows_dual_flow_catalog() -> None:
    client = TestClient(app)
    response = client.get("/data-points")

    assert response.status_code == 200
    assert "Master Data Points" in response.text
    assert "PDF fill value" in response.text
    assert "Web fill value" in response.text
    assert "/data-points/fill-preview" in response.text
    assert "Both ready" in response.text
    assert "/pdf-ops" in response.text
    assert "/website-ops" in response.text
