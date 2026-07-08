from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import app


def test_pdf_ops_page_is_pdf_only() -> None:
    client = TestClient(app)

    response = client.get("/pdf-ops")

    assert response.status_code == 200
    assert "PDF Survey Operations" in response.text
    assert "PDF Flow" in response.text
    assert "/website-ops" in response.text
    assert "Step 1" in response.text
    assert "Step 2" in response.text
    assert "Step 3" in response.text
    assert "Step 4" in response.text
    assert "tip-icon" in response.text
    assert "/pdf-scans" in response.text
    assert "/resolve-via-genie" in response.text
    assert "/resolve-direct" in response.text
    assert "/resolved-values" in response.text
    assert "Export Filled PDF" in response.text
    assert "/export-filled-pdf" in response.text
    assert "Download filled PDF" in response.text
    assert "Shared fill catalog" in response.text
    assert "Analyst SQL Mapping" in response.text
    assert "Ready for review" in response.text
    assert "Rerun approved SQL" in response.text
    assert "/analyst-sql/preview" in response.text
    assert "/auto-map" in response.text
    assert "/rerun-approved" in response.text
    assert "Debug - Raw API responses" in response.text
    assert "errorLog" in response.text
    assert "Website form URL" not in response.text
    assert "Fill from Resolved Values" not in response.text
    assert "/execute-draft-fill-pipeline" not in response.text
    assert "Skyvern" not in response.text
    assert "Create Master" not in response.text
    assert "Bootstrap Masters" not in response.text
    assert "Auto-Map Scan" not in response.text
    assert "Publish Catalog" not in response.text
    assert "/mapping-suggestions" not in response.text


def test_website_ops_page_is_website_only() -> None:
    client = TestClient(app)

    response = client.get("/website-ops")

    assert response.status_code == 200
    assert "Website Survey Operations" in response.text
    assert "Web Fill" in response.text
    assert "/pdf-ops" in response.text
    assert "Website scan and form fill" in response.text
    assert "Website form URL" in response.text
    assert "Use current browser session" in response.text
    assert "CDP browser connection" in response.text or "Connect browser (CDP)" in response.text
    assert "Check browser connection" in response.text
    assert "Website requires login" in response.text
    assert "browserSessionId" in response.text
    assert "useCurrentBrowser" in response.text
    assert "needsLogin" in response.text
    assert "skyvernMaxSteps" in response.text
    assert "No passwords are stored by this page." in response.text
    assert "Review web fill values" in response.text
    assert "wpPreviewRows" in response.text
    assert "Full catalog" in response.text
    assert "Run Website Automation" in response.text
    assert "/website-ops/full-workflow/jobs" in response.text
    assert "PDF file path" not in response.text
    assert "Export Filled PDF" not in response.text
    assert "/export-filled-pdf" not in response.text

    assert "<script><script>" not in response.text
    assert 'id="wsBanner" class="banner"' in response.text
    assert "loadBrowserConfig();" in response.text
    assert "reloadWebPreview();" in response.text


def test_legacy_ops_page_aliases_website_flow() -> None:
    client = TestClient(app)

    response = client.get("/ops")

    assert response.status_code == 200
    assert "Website Survey Operations" in response.text
