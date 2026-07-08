from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from apps.api import main as api_main
from apps.api.db.models import Base, SurveyPdfDataPointCandidate, SurveyPdfScan
from apps.api.db.session import get_session
from apps.api.main import app


class _FakeSqlReader:
    configured = True
    calls: list[str] = []

    def __init__(self, _settings):
        pass

    def query_rows(self, sql: str, *, row_limit: int = 1000):
        del row_limit
        self.__class__.calls.append(sql)
        return ["metric", "men", "women", "total"], [["Applied", "12", "20", "32"], ["Admitted", "5", "8", "13"]]


class _FakeServingMapper:
    configured = True

    def __init__(self, _settings):
        pass

    def propose_mappings(self, *, query, candidates, columns, rows, max_drafts=50):
        del query, max_drafts
        candidate = next(item for item in candidates if item["field_name"] == "AP_RECD_1ST_N")
        return [
            {
                "candidate_id": candidate["candidate_id"],
                "field_name": candidate["field_name"],
                "source_row_index": 0,
                "source_column": "total",
                "confidence": 94,
                "reason": "The row metric Applied and column total match total first-year applications.",
            }
        ]


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'control_plane.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed_scan(session_factory) -> None:
    session = session_factory()
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_sql",
                survey_id="cds_2025",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=2,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_app_total",
                scan_id="scan_sql",
                survey_id="cds_2025",
                candidate_key="AP_RECD_1ST_N",
                source="acroform",
                field_name="AP_RECD_1ST_N",
                label_text="Total first-time first-year applications received",
                normalized_label="total first time first year applications received",
                input_kind="number",
                confidence=1.0,
                label_source="openai_enriched",
                datapoint_intent="total applications received for first-time first-year students",
                status="DISCOVERED",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_admit_total",
                scan_id="scan_sql",
                survey_id="cds_2025",
                candidate_key="AP_ADMT_1ST_N",
                source="acroform",
                field_name="AP_ADMT_1ST_N",
                label_text="Total first-time first-year admitted applicants",
                normalized_label="total first time first year admitted applicants",
                input_kind="number",
                confidence=1.0,
                label_source="openai_enriched",
                datapoint_intent="total admitted first-time first-year students",
                status="DISCOVERED",
            )
        )
        session.commit()
    finally:
        session.close()


def test_analyst_sql_mapping_preview_approve_and_rerun(tmp_path, monkeypatch) -> None:
    session_factory = _session_factory(tmp_path)
    _seed_scan(session_factory)

    def _override_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _override_session
    monkeypatch.setattr(api_main, "DatabricksSqlValueReader", _FakeSqlReader, raising=False)
    monkeypatch.setattr(api_main, "DatabricksServingSqlMapper", _FakeServingMapper, raising=False)
    _FakeSqlReader.calls = []

    try:
        client = TestClient(app)
        preview = client.post(
            "/pdf-scans/scan_sql/analyst-sql/preview",
            json={
                "name": "Section C admissions",
                "sql_text": "SELECT metric, men, women, total FROM section_c",
                "survey_year": 2025,
                "row_limit": 10,
            },
        )
        assert preview.status_code == 200
        preview_body = preview.json()
        assert preview_body["scan_id"] == "scan_sql"
        assert preview_body["columns"] == ["metric", "men", "women", "total"]
        assert preview_body["sample_rows"][0] == ["Applied", "12", "20", "32"]
        query_id = preview_body["query_id"]

        auto_map = client.post(f"/analyst-sql/{query_id}/auto-map", json={"max_drafts": 10})
        assert auto_map.status_code == 200
        drafts = auto_map.json()["drafts"]
        assert len(drafts) == 1
        assert drafts[0]["field_name"] == "AP_RECD_1ST_N"
        assert drafts[0]["value_preview"] == "32"
        assert drafts[0]["confidence"] == 94

        approve = client.post(f"/analyst-sql-mapping-drafts/{drafts[0]['draft_id']}/approve", json={})
        assert approve.status_code == 200
        assert approve.json()["field_name"] == "AP_RECD_1ST_N"
        assert approve.json()["value"] == "32"

        rerun = client.post(f"/analyst-sql/{query_id}/rerun-approved", json={"survey_year": 2025})
        assert rerun.status_code == 200
        assert rerun.json()["refreshed"] == 1
        assert _FakeSqlReader.calls.count("SELECT metric, men, women, total FROM section_c") == 2
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    verify = session_factory()
    try:
        row = verify.get(SurveyPdfDataPointCandidate, "cand_app_total")
        assert row is not None
        assert row.genie_value == "32"
        assert row.genie_confidence == 94
        assert row.status == "GENIE_RESOLVED"
        assert "Analyst SQL" in row.genie_reason
    finally:
        verify.close()
