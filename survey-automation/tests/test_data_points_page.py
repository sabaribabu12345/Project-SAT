from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import app


def test_data_points_page_loads() -> None:
    client = TestClient(app)

    response = client.get("/data-points")

    assert response.status_code == 200
    assert "Master Data Points" in response.text
    assert "PDF fill value" in response.text
    assert "Web fill value" in response.text
    assert "/data-points/fill-preview" in response.text
    assert "Both ready" in response.text
    assert "dpSearch" in response.text
    assert "/pdf-ops" in response.text
    assert "/website-ops" in response.text
