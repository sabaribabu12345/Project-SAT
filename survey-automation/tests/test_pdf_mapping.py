from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import apps.api.service as service_module
import apps.api.openai_pdf_label_enrichment as openai_pdf_label_enrichment_module
from apps.api.databricks_pdf_label_enrichment import DatabricksPdfLabelEnricher
from apps.api.databricks_genie_client import DatabricksGenieClient, GenieMappingChoice, GenieResolution
from apps.api.cds_query_registry import CdsQueryRegistry
from apps.api.db.models import (
    Base,
    MasterDataPoint,
    MasterDataPointAlias,
    PdfMappingDraft,
    SurveyFieldCatalog,
    SurveyPdfDataPointCandidate,
    SurveyPdfScan,
)
from apps.api.openai_pdf_label_enrichment import OpenAIPdfLabelEnricher, PdfLabelEnrichment
from apps.api.pdf_scanner import PdfDatapointCandidate, PdfScanResult
from apps.api.service import PdfDatapointService, PdfLabelEnrichmentFailedError
from apps.api.service import _text_match_score
from apps.api.settings import Settings


def _service_with_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    return PdfDatapointService(session), session


def _file_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'control_plane.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class _FakeEmbeddingScorer:
    def score_pair(self, left: str, right: str) -> int | None:
        if "ship to another university" in left.lower() and "redirection status" in right.lower():
            return 93
        return 5


class _DisabledEmbeddingScorer:
    """Always unavailable — for tests asserting lexical/alias-only matching
    behavior, which must not depend on ambient .env embedding credentials or
    a live network call."""

    def score_pair(self, left: str, right: str) -> int | None:
        return None


class _FakeGenieClient:
    configured = True

    def __init__(self, choice: GenieMappingChoice | None) -> None:
        self._choice = choice
        self.calls = 0
        self.batch_sizes: list[int] = []

    def choose_master_data_point(
        self,
        *,
        candidate_field_name: str,
        candidate_label_text: str,
        candidate_nearby_text: str,
        options: list[dict[str, object]],
    ) -> GenieMappingChoice | None:
        del candidate_field_name, candidate_label_text, candidate_nearby_text, options
        return self._choice

    def choose_many_master_data_points(
        self,
        *,
        candidates: list[dict[str, object]],
    ) -> dict[str, GenieMappingChoice]:
        self.calls += 1
        self.batch_sizes.append(len(candidates))
        result: dict[str, GenieMappingChoice] = {}
        for candidate in candidates:
            candidate_id = str(candidate["candidate_id"])
            if self._choice:
                result[candidate_id] = self._choice
        return result


class _FakePdfLabelEnricher:
    def enrich_pdf(
        self,
        *,
        file_path: str,
        candidates: list[PdfDatapointCandidate],
        page_count: int = 0,
    ) -> dict[str, PdfLabelEnrichment]:
        del file_path, page_count
        return {
            candidate.candidate_key: PdfLabelEnrichment(
                candidate_key=candidate.candidate_key,
                label_text="CDS responses posted on institution website",
                section="A0 Respondent Information",
                datapoint_intent="institution publishes CDS response URL/status",
                expected_value_type="boolean",
                context="Are your responses to the CDS posted on your institution website?",
            )
            for candidate in candidates
        }


class _CapturingPdfLabelEnricher:
    configured = True

    def __init__(self) -> None:
        self.last_candidates_count = 0

    def enrich_pdf(
        self,
        *,
        file_path: str,
        candidates: list[PdfDatapointCandidate],
        page_count: int = 0,
    ) -> dict[str, PdfLabelEnrichment]:
        del file_path, page_count
        self.last_candidates_count = len(candidates)
        return {}


class _FailingPdfLabelEnricher:
    def enrich_pdf(
        self,
        *,
        file_path: str,
        candidates: list[PdfDatapointCandidate],
        page_count: int = 0,
    ) -> dict[str, PdfLabelEnrichment]:
        del file_path, candidates, page_count
        raise RuntimeError("provider down")


class _FakeOpenAIResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeOpenAIResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _FakeServingResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def as_dict(self) -> dict[str, object]:
        return self._payload


class _FakeServingEndpoints:
    def __init__(self, response_payload: dict[str, object]) -> None:
        self._response_payload = response_payload
        self.calls: list[dict[str, object]] = []

    def query(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return _FakeServingResponse(self._response_payload)


class _FakeWorkspaceClient:
    def __init__(self, response_payload: dict[str, object]) -> None:
        class _FakeApiClient:
            def __init__(self, payload: dict[str, object]) -> None:
                self._payload = payload
                self.calls: list[dict[str, object]] = []

            def do(self, *, method, path, body):  # type: ignore[no-untyped-def]
                self.calls.append({"method": method, "path": path, "body": body})
                return self._payload

        self.api_client = _FakeApiClient(response_payload)


class _ResolvingFakeGenieClient:
    configured = True

    def __init__(self, _settings: Settings) -> None:
        pass

    def resolve_many_candidates(
        self,
        *,
        candidates: list[dict[str, object]],
        survey_year: int,
    ) -> dict[str, GenieResolution]:
        del survey_year
        candidate_id = str(candidates[0]["candidate_id"])
        return {
            candidate_id: GenieResolution(
                candidate_id=candidate_id,
                sql_template="SELECT '42' AS value WHERE survey_year = __SURVEY_YEAR__",
                table="catalog.schema.table",
                column="value",
                year_column="survey_year",
                value="42",
                confidence=88,
                reason="test resolution",
            )
        }


class _RefreshingFakeSqlReader:
    configured = True

    def __init__(self, _settings: Settings) -> None:
        self.calls: list[str] = []

    def query_rows(self, sql: str, *, row_limit: int = 1000):  # type: ignore[no-untyped-def]
        del row_limit
        self.calls.append(sql)
        return ["value"], [["43"]]


class _RegistryFakeSqlReader:
    configured = True
    calls: list[str] = []

    def __init__(self, _settings: Settings) -> None:
        pass

    def query_rows(self, sql: str, *, row_limit: int = 1000):  # type: ignore[no-untyped-def]
        del row_limit
        self.__class__.calls.append(sql)
        if "RETENTION_FRSH_N" in sql:
            return ["value"], [["6267"]]
        return ["value"], [["5522"]]


def _add_scan_with_candidate(
    session,
    *,
    scan_id: str = "scan_commit",
    candidate_id: str = "cand_commit",
    genie_sql_template: str = "",
) -> None:
    session.add(
        SurveyPdfScan(
            scan_id=scan_id,
            survey_id="survey",
            file_name="survey.pdf",
            file_path="/tmp/survey.pdf",
            file_sha256="abc",
            fillable=True,
            page_count=1,
            candidate_count=1,
            raw_result_json="{}",
        )
    )
    session.add(
        SurveyPdfDataPointCandidate(
            candidate_id=candidate_id,
            scan_id=scan_id,
            survey_id="survey",
            candidate_key="acroform.total",
            source="acroform",
            field_name="TOTAL",
            label_text="Total applicants",
            normalized_label="total applicants",
            input_kind="text",
            confidence=0.95,
            label_source="openai_enriched",
            field_rect_json="[]",
            nearby_text="Section: B1 Enrollment\nTotal applicants",
            datapoint_intent="total applicants",
            genie_sql_template=genie_sql_template,
            genie_table="catalog.schema.table" if genie_sql_template else "",
            genie_column="value" if genie_sql_template else "",
            genie_year_column="survey_year" if genie_sql_template else "",
            genie_value="42" if genie_sql_template else "",
            genie_confidence=80 if genie_sql_template else 0,
            status="GENIE_RESOLVED" if genie_sql_template else "DISCOVERED",
        )
    )
    session.commit()


def _sample_pdf_candidate(candidate_key: str = "acroform.cds_response") -> PdfDatapointCandidate:
    return PdfDatapointCandidate(
        candidate_key=candidate_key,
        source="acroform",
        field_name="CDS_RESPONSE",
        label_text="Cds Response",
        normalized_label="cds response",
        input_kind="text",
        page_number=2,
        confidence=0.65,
        label_source="field_name",
        nearby_text="Are your responses to the CDS posted for",
    )


def test_text_match_score_does_not_match_one_word_alias_inside_longer_words() -> None:
    assert _text_match_score("outstate area code", "state") < 70
    assert _text_match_score("e mail address", "address") < 70
    assert _text_match_score("main institution website", "institution website") == 88


def test_genie_workspace_client_uses_configured_http_timeout(monkeypatch) -> None:
    class _FakeWorkspaceClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

            class _FakeBaseClient:
                _http_timeout_seconds = None
                _retry_timeout_seconds = None

            class _FakeApiClient:
                _api_client = _FakeBaseClient()

            self.api_client = _FakeApiClient()

    monkeypatch.setattr(service_module, "WorkspaceClient", _FakeWorkspaceClient, raising=False)
    monkeypatch.setattr("apps.api.databricks_genie_client.WorkspaceClient", _FakeWorkspaceClient)

    client = DatabricksGenieClient(
        Settings(
            databricks_host="https://example.cloud.databricks.com",
            databricks_token="token",
            databricks_genie_space_id="space",
            databricks_genie_request_timeout_seconds=17,
        )
    )

    workspace_client = client._workspace_client_for_genie()

    base_client = workspace_client.api_client._api_client
    assert base_client._http_timeout_seconds == 17
    assert base_client._retry_timeout_seconds == 17


def test_genie_query_result_parser_handles_databricks_typed_value_rows(monkeypatch) -> None:
    client = DatabricksGenieClient(Settings())

    monkeypatch.setattr(
        client,
        "_get_json",
        lambda path: {
            "statement_response": {
                "result": {
                    "data_typed_array": [
                        {"values": [{"str": "FT_N"}, {}]},
                        {"values": [{"str": "TOTAL"}, {"str": "123"}]},
                    ]
                }
            }
        },
    )

    columns, rows = client._fetch_query_result("space", "conversation", "message")

    assert columns == []  # no schema in this mock response
    assert rows == [["FT_N", None], ["TOTAL", "123"]]


def test_genie_resolution_prompt_is_explicit_about_narrow_sql_output() -> None:
    client = DatabricksGenieClient(Settings())
    prompt = client._build_resolution_prompt(
        survey_year=2025,
        candidates=[
            {
                "candidate_id": "cand_1",
                "field_name": "EN_FRSH_FT_MEN_N",
                "section": "B1 Enrollment",
                "label_text": "Full-time first-time men",
                "datapoint_intent": "Count full-time first-time men enrollment",
                "context": "Section: B1 Enrollment | Need count of full-time first-time men",
            },
            {
                "candidate_id": "cand_2",
                "field_name": "EN_FRSH_FT_WMN_N",
                "section": "B1 Enrollment",
                "label_text": "Full-time first-time women",
                "datapoint_intent": "Count full-time first-time women enrollment",
                "context": "Section: B1 Enrollment | Need count of full-time first-time women",
            },
        ],
    )

    assert "Survey year: 2025" in prompt
    assert "Return one SQL query that produces a narrow result set with exactly two columns: field_name and value." in prompt
    assert "Use one row per requested field." in prompt
    assert "field_name='EN_FRSH_FT_MEN_N'" in prompt
    assert "field_name='EN_FRSH_FT_WMN_N'" in prompt


def test_pdf_service_uses_openai_label_provider_for_scan_even_when_databricks_configured() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        service = PdfDatapointService(
            session,
            settings=Settings(
                pdf_label_enrichment_provider="databricks",
                databricks_host="https://adb-123.4.azuredatabricks.net",
                databricks_token="token",
                pdf_databricks_label_model="databricks-claude-sonnet-4-6",
            ),
        )
        assert isinstance(service._pdf_label_enricher, OpenAIPdfLabelEnricher)
    finally:
        session.close()


def test_scan_pdf_always_sets_openai_label_source_for_enriched_fields(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    service = PdfDatapointService(
        session,
        settings=Settings(pdf_label_enrichment_provider="databricks"),
        pdf_label_enricher=_FakePdfLabelEnricher(),
    )
    try:
        monkeypatch.setattr(
            service_module,
            "scan_pdf_datapoints",
            lambda *_args, **_kwargs: PdfScanResult(
                file_path="/tmp/survey.pdf",
                file_name="survey.pdf",
                file_sha256="abc",
                survey_id="survey",
                fillable=True,
                page_count=1,
                candidates=[_sample_pdf_candidate()],
            ),
        )
        _scan, candidates = service.scan_pdf(file_path="/tmp/survey.pdf", survey_id="survey")
        assert candidates[0].label_source == "openai_enriched"
    finally:
        session.close()


def test_databricks_pdf_label_enricher_uses_compact_payload_and_parses_mappings(monkeypatch) -> None:
    settings = Settings(
        databricks_host="https://adb-123.4.azuredatabricks.net",
        databricks_token="token",
        pdf_databricks_label_model="databricks-claude-sonnet-4-6",
        pdf_databricks_label_batch_size=50,
        pdf_databricks_label_max_prompt_chars=18000,
    )
    enricher = DatabricksPdfLabelEnricher(settings)
    fake_workspace = _FakeWorkspaceClient(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "mappings": [
                                    {
                                        "candidate_key": "acroform.cds_response",
                                        "meaningful_label": "CDS responses posted on institution website",
                                        "datapoint_description": "Whether CDS responses are posted online.",
                                        "input_kind": "checkbox",
                                        "confidence": 0.93,
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }
    )
    monkeypatch.setattr(enricher, "_workspace_client_for_serving", lambda: fake_workspace)

    result = enricher.enrich_pdf(file_path="/tmp/survey.pdf", candidates=[_sample_pdf_candidate()])

    assert result["acroform.cds_response"].label_text == "CDS responses posted on institution website"
    assert result["acroform.cds_response"].confidence == 0.93
    assert len(fake_workspace.api_client.calls) == 1
    call = fake_workspace.api_client.calls[0]
    assert call["method"] == "POST"
    assert "/serving-endpoints/databricks-claude-sonnet-4-6/invocations" in str(call["path"])
    user_prompt = call["body"]["messages"][1]["content"]  # type: ignore[index]
    assert "nearby_text" not in user_prompt
    assert "field_name" in user_prompt
    assert "label_text" in user_prompt


def test_databricks_pdf_label_enricher_batches_candidates(monkeypatch) -> None:
    settings = Settings(
        databricks_host="https://adb-123.4.azuredatabricks.net",
        databricks_token="token",
        pdf_databricks_label_model="databricks-claude-sonnet-4-6",
        pdf_databricks_label_batch_size=2,
        pdf_databricks_label_max_prompt_chars=18000,
    )
    enricher = DatabricksPdfLabelEnricher(settings)
    fake_workspace = _FakeWorkspaceClient({"choices": [{"message": {"content": '{"mappings":[]}'}}]})
    monkeypatch.setattr(enricher, "_workspace_client_for_serving", lambda: fake_workspace)
    candidates = [
        _sample_pdf_candidate("cand_1"),
        _sample_pdf_candidate("cand_2"),
        _sample_pdf_candidate("cand_3"),
    ]

    enricher.enrich_pdf(file_path="/tmp/survey.pdf", candidates=candidates)

    assert len(fake_workspace.api_client.calls) == 2


def _make_fake_urlopen(batch_response_payload: dict[str, object]):  # type: ignore[no-untyped-def]
    """
    Returns a fake urlopen that handles the three call types:
      - POST /files  → returns {"id": "file-test123"}
      - POST /responses → returns batch_response_payload
      - DELETE /files/... → returns empty (best-effort cleanup)
    """
    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        del timeout
        url = req.full_url if hasattr(req, "full_url") else str(req)
        method = getattr(req, "method", "GET")
        if method == "DELETE":
            return _FakeOpenAIResponse({})
        if "/files" in url and method == "POST":
            return _FakeOpenAIResponse({"id": "file-test123", "object": "file"})
        # POST /responses — batch call
        return _FakeOpenAIResponse(batch_response_payload)
    return fake_urlopen


def test_openai_pdf_label_enricher_batches_by_page_range(tmp_path, monkeypatch) -> None:
    pdf_path = tmp_path / "survey.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nwhole pdf bytes")
    # candidate has field_name="CDS_RESPONSE"; OpenAI returns that field_name
    candidate = _sample_pdf_candidate()
    captured_batch_payloads: list[dict[str, object]] = []

    batch_response = {
        "output_text": json.dumps(
            {
                "fields": [
                    {
                        "field_name": "CDS_RESPONSE",
                        "section": "A0 Respondent Information",
                        "label": "CDS responses posted on institution website",
                        "datapoint_intent": "institution publishes CDS response URL/status",
                        "expected_value_type": "boolean",
                        "context": "Are your responses to the CDS posted on your institution website?",
                    }
                ]
            }
        )
    }

    original_fake = _make_fake_urlopen(batch_response)

    def capturing_fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        url = req.full_url if hasattr(req, "full_url") else str(req)
        method = getattr(req, "method", "GET")
        if "/responses" in url and method == "POST" and req.data:
            captured_batch_payloads.append(json.loads(req.data.decode("utf-8")))
        return original_fake(req, timeout)

    monkeypatch.setattr(openai_pdf_label_enrichment_module.request, "urlopen", capturing_fake_urlopen)
    enricher = OpenAIPdfLabelEnricher(
        Settings(
            pdf_openai_label_enrichment_enabled=True,
            pdf_openai_label_enrichment_api_key="sk-test",
            pdf_openai_label_enrichment_model="gpt-4o-mini",
            pdf_openai_pages_per_batch=10,
        )
    )

    # page_count=2 → 1 batch covering pages 1-2
    enrichments = enricher.enrich_pdf(file_path=pdf_path, candidates=[candidate], page_count=2)

    assert len(captured_batch_payloads) == 1
    # Batch payload uses file_id reference, not inline base64
    user_content = captured_batch_payloads[0]["input"][1]["content"]  # type: ignore[index]
    assert any(
        isinstance(part, dict) and part.get("type") == "input_file" and "file_id" in part
        for part in user_content  # type: ignore[union-attr]
    )
    # Payload carries page_range, not known_pdf_fields
    text_part = next(
        p for p in user_content  # type: ignore[union-attr]
        if isinstance(p, dict) and p.get("type") == "input_text"
    )
    instructions_obj = json.loads(str(text_part["text"]))
    assert "page_range" in instructions_obj
    assert "known_pdf_fields" not in instructions_obj
    assert instructions_obj["page_range"]["start"] == 1
    assert instructions_obj["page_range"]["end"] == 2

    # Result merged back by field_name → candidate_key
    assert "acroform.cds_response" in enrichments
    assert enrichments["acroform.cds_response"].label_text == "CDS responses posted on institution website"
    assert enrichments["acroform.cds_response"].section == "A0 Respondent Information"
    assert enrichments["acroform.cds_response"].datapoint_intent == "institution publishes CDS response URL/status"
    assert enrichments["acroform.cds_response"].expected_value_type == "boolean"


def test_openai_pdf_label_enricher_splits_into_multiple_batches(tmp_path, monkeypatch) -> None:
    pdf_path = tmp_path / "survey.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nwhole pdf bytes")
    candidate = _sample_pdf_candidate()
    batch_call_count = 0

    def counting_fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        nonlocal batch_call_count
        del timeout
        url = req.full_url if hasattr(req, "full_url") else str(req)
        method = getattr(req, "method", "GET")
        if method == "DELETE":
            return _FakeOpenAIResponse({})
        if "/files" in url and method == "POST":
            return _FakeOpenAIResponse({"id": "file-abc", "object": "file"})
        # /responses batch call
        batch_call_count += 1
        return _FakeOpenAIResponse({"output_text": json.dumps({"fields": []})})

    monkeypatch.setattr(openai_pdf_label_enrichment_module.request, "urlopen", counting_fake_urlopen)
    enricher = OpenAIPdfLabelEnricher(
        Settings(
            pdf_openai_label_enrichment_enabled=True,
            pdf_openai_label_enrichment_api_key="sk-test",
            pdf_openai_pages_per_batch=10,
        )
    )

    # 25 pages with batch_size=10 → 3 initial batches (1-10, 11-20, 21-25)
    # + 1 retry batch for the candidate that wasn't returned in pass 1 (empty response)
    enricher.enrich_pdf(file_path=pdf_path, candidates=[candidate], page_count=25)
    assert batch_call_count == 4  # 3 initial + 1 retry


def test_openai_pdf_label_enricher_splits_large_field_list_by_field_cap(tmp_path, monkeypatch) -> None:
    pdf_path = tmp_path / "survey.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nwhole pdf bytes")
    batch_call_count = 0

    def counting_fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        nonlocal batch_call_count
        del timeout
        url = req.full_url if hasattr(req, "full_url") else str(req)
        method = getattr(req, "method", "GET")
        if method == "DELETE":
            return _FakeOpenAIResponse({})
        if "/files" in url and method == "POST":
            return _FakeOpenAIResponse({"id": "file-abc", "object": "file"})
        batch_call_count += 1
        return _FakeOpenAIResponse({"output_text": json.dumps({"fields": []})})

    monkeypatch.setattr(openai_pdf_label_enrichment_module.request, "urlopen", counting_fake_urlopen)
    enricher = OpenAIPdfLabelEnricher(
        Settings(
            pdf_openai_label_enrichment_enabled=True,
            pdf_openai_label_enrichment_api_key="sk-test",
            pdf_openai_pages_per_batch=10,
            pdf_openai_max_fields_per_batch=2,
        )
    )
    candidates = [
        PdfDatapointCandidate(
            candidate_key=f"cand_{idx}",
            source="acroform",
            field_name=f"FIELD_{idx}",
            label_text=f"Field {idx}",
            normalized_label=f"field {idx}",
            input_kind="text",
            page_number=1,
            confidence=0.65,
            label_source="field_name",
        )
        for idx in range(5)
    ]

    enricher.enrich_pdf(file_path=pdf_path, candidates=candidates, page_count=1)
    assert batch_call_count == 4  # 3 initial chunks + 1 retry chunk


def test_scan_pdf_applies_openai_label_enrichment_before_persisting_candidates(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    service = PdfDatapointService(
        session,
        settings=Settings(pdf_label_enrichment_provider="openai"),
        pdf_label_enricher=_FakePdfLabelEnricher(),
    )
    try:
        monkeypatch.setattr(
            service_module,
            "scan_pdf_datapoints",
            lambda *_args, **_kwargs: PdfScanResult(
                file_path="/tmp/survey.pdf",
                file_name="survey.pdf",
                file_sha256="abc",
                survey_id="survey",
                fillable=True,
                page_count=1,
                candidates=[
                    PdfDatapointCandidate(
                        candidate_key="acroform.cds_response",
                        source="acroform",
                        field_name="CDS_RESPONSE",
                        label_text="Cds Response",
                        normalized_label="cds response",
                        input_kind="text",
                        page_number=1,
                        confidence=0.65,
                        label_source="field_name",
                        nearby_text="Are your responses to the CDS posted for",
                    )
                ],
            ),
        )

        _scan, candidates = service.scan_pdf(file_path="/tmp/survey.pdf", survey_id="survey")

        assert len(candidates) == 1
        assert candidates[0].label_text == "CDS responses posted on institution website"
        assert candidates[0].normalized_label == "cds responses posted on institution website"
        assert candidates[0].input_kind == "checkbox"  # expected_value_type "boolean" → "checkbox"
        assert candidates[0].label_source == "openai_enriched"
        assert candidates[0].datapoint_intent == "institution publishes CDS response URL/status"
        assert "Section:" in candidates[0].nearby_text
    finally:
        session.close()


def test_scan_pdf_does_not_fail_when_label_enrichment_provider_errors(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    service = PdfDatapointService(session, pdf_label_enricher=_FailingPdfLabelEnricher())
    try:
        monkeypatch.setattr(
            service_module,
            "scan_pdf_datapoints",
            lambda *_args, **_kwargs: PdfScanResult(
                file_path="/tmp/survey.pdf",
                file_name="survey.pdf",
                file_sha256="abc",
                survey_id="survey",
                fillable=True,
                page_count=1,
                candidates=[_sample_pdf_candidate()],
            ),
        )
        _scan, candidates = service.scan_pdf(
            file_path="/tmp/survey.pdf",
            survey_id="survey",
            require_label_enrichment=True,
            allow_enrichment_fallback=True,
        )
        assert len(candidates) == 1
        assert candidates[0].label_text == "Cds Response"
    finally:
        session.close()


def test_scan_pdf_raises_when_label_enrichment_provider_errors_and_fallback_disabled(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    service = PdfDatapointService(session, pdf_label_enricher=_FailingPdfLabelEnricher())
    try:
        monkeypatch.setattr(
            service_module,
            "scan_pdf_datapoints",
            lambda *_args, **_kwargs: PdfScanResult(
                file_path="/tmp/survey.pdf",
                file_name="survey.pdf",
                file_sha256="abc",
                survey_id="survey",
                fillable=True,
                page_count=1,
                candidates=[_sample_pdf_candidate()],
            ),
        )
        try:
            service.scan_pdf(
                file_path="/tmp/survey.pdf",
                survey_id="survey",
                require_label_enrichment=True,
                allow_enrichment_fallback=False,
            )
            assert False, "Expected PdfLabelEnrichmentFailedError"
        except PdfLabelEnrichmentFailedError:
            pass
    finally:
        session.close()


def test_scan_pdf_honors_label_enrichment_candidate_limit(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    capturing = _CapturingPdfLabelEnricher()
    service = PdfDatapointService(session, pdf_label_enricher=capturing)
    try:
        monkeypatch.setattr(
            service_module,
            "scan_pdf_datapoints",
            lambda *_args, **_kwargs: PdfScanResult(
                file_path="/tmp/survey.pdf",
                file_name="survey.pdf",
                file_sha256="abc",
                survey_id="survey",
                fillable=True,
                page_count=1,
                candidates=[
                    PdfDatapointCandidate(
                        candidate_key=f"cand_{idx}",
                        source="acroform",
                        field_name=f"FIELD_{idx}",
                        label_text=f"Field {idx}",
                        normalized_label=f"field {idx}",
                        input_kind="text",
                        page_number=1,
                        confidence=0.65,
                        label_source="field_name",
                    )
                    for idx in range(6)
                ],
            ),
        )
        service.scan_pdf(
            file_path="/tmp/survey.pdf",
            survey_id="survey",
            require_label_enrichment=True,
            allow_enrichment_fallback=True,
            label_enrichment_candidate_limit=3,
        )
        assert capturing.last_candidates_count == 3
    finally:
        session.close()


def test_suggests_master_datapoint_from_alias_overlap() -> None:
    service, session = _service_with_session()
    service = PdfDatapointService(session, mapping_similarity_scorer=_DisabledEmbeddingScorer())
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_test",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_tuition",
                scan_id="scan_test",
                survey_id="survey",
                candidate_key="acroform.tuit_vary_prog_p",
                source="acroform",
                field_name="TUIT_VARY_PROG_P",
                label_text="If yes, what percentage of full-time undergraduates pay more than the tuition and fees reported in G1?",
                normalized_label="if yes what percentage of full time undergraduates pay more than the tuition and fees reported in g1",
                input_kind="text",
                confidence=0.85,
                label_source="nearby_text",
                field_rect_json="[]",
                nearby_text="If yes, what percentage of full-time undergraduates pay more than the tuition and fees reported in G1?",
            )
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.tuition.varies_by_program_percent",
                canonical_name="Percent of full-time undergraduates paying more than G1 tuition and fees",
                semantic_key="tuition.varies_by_program_percent",
                databricks_view="production.silver.erss",
                databricks_value_column="value",
                databricks_year_column="survey_year",
            )
        )
        session.add(
            MasterDataPointAlias(
                alias_id="alias_1",
                data_point_id="dp.tuition.varies_by_program_percent",
                alias_text="what percentage of full-time undergraduates pay more than the tuition and fees reported in G1",
                normalized_alias="what percentage of full time undergraduates pay more than the tuition and fees reported in g1",
                source="test",
            )
        )
        session.commit()

        suggestions = service.suggest_candidate_mappings(scan_id="scan_test", limit_per_candidate=1)

        assert suggestions[0].candidate_id == "cand_tuition"
        assert suggestions[0].suggestions[0].data_point_id == "dp.tuition.varies_by_program_percent"
        assert suggestions[0].suggestions[0].score >= 80
        assert "alias" in suggestions[0].suggestions[0].reason
    finally:
        session.close()


def test_resolves_mapped_pdf_scan_from_literal_master_binding() -> None:
    service, session = _service_with_session()
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_resolve",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_website",
                scan_id="scan_resolve",
                survey_id="survey",
                candidate_key="acroform.url_destination_url",
                source="acroform",
                field_name="URL_DESTINATION_URL",
                label_text="Main Institution Website",
                normalized_label="main institution website",
                input_kind="text",
                confidence=0.95,
                label_source="tooltip",
                field_rect_json="[]",
                nearby_text="Main Institution Website",
                master_data_point_id="dp.institution.website",
                status="MAPPED",
            )
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.institution.website",
                canonical_name="Main Institution Website",
                semantic_key="institution.website",
                databricks_view="",
                databricks_value_column="literal:https://www.csulb.edu",
                databricks_year_column="",
            )
        )
        session.commit()

        payload = service.resolve_mapped_pdf_scan(
            scan_id="scan_resolve",
            survey_year=2026,
            settings=Settings(databricks_resolver_mode="fake"),
        )

        assert payload.values["cand_website"]["value"] == "https://www.csulb.edu"
        assert payload.values["cand_website"]["master_data_point_id"] == "dp.institution.website"
        assert payload.missing_candidates == []
    finally:
        session.close()


def test_publishes_mapped_pdf_candidates_to_field_catalog() -> None:
    service, session = _service_with_session()
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_publish",
                survey_id="usnews_pdf",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_website",
                scan_id="scan_publish",
                survey_id="usnews_pdf",
                candidate_key="acroform.url_destination_url",
                source="acroform",
                field_name="URL_DESTINATION_URL",
                label_text="Main Institution Website",
                normalized_label="main institution website",
                input_kind="text",
                confidence=0.95,
                label_source="tooltip",
                field_rect_json="[]",
                nearby_text="Main Institution Website",
                master_data_point_id="dp.institution.website",
                status="MAPPED",
            )
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.institution.website",
                canonical_name="Main Institution Website",
                semantic_key="institution.website",
                databricks_view="production.reference.institution_profile",
                databricks_value_column="website",
                databricks_year_column="survey_year",
                transform_json='{"strip": true}',
            )
        )
        session.commit()

        rows = service.publish_pdf_scan_to_field_catalog(scan_id="scan_publish", section_id="pdf_usnews")

        assert len(rows) == 1
        assert rows[0].field_id == "pdf.usnews_pdf.acroform.url_destination_url"
        assert rows[0].section_id == "pdf_usnews"
        assert rows[0].label_text == "Main Institution Website"
        assert rows[0].databricks_view == "production.reference.institution_profile"
        assert rows[0].databricks_value_column == "website"
        assert rows[0].databricks_year_column == "survey_year"
        assert rows[0].transform_json == '{"strip": true}'
        assert session.get(SurveyFieldCatalog, "pdf.usnews_pdf.acroform.url_destination_url") is not None
    finally:
        session.close()


def test_bootstrap_master_data_points_from_catalog_creates_masters_and_aliases() -> None:
    service, session = _service_with_session()
    try:
        session.add(
            SurveyFieldCatalog(
                field_id="institution.main_website",
                section_id="pdf_usnews",
                label_text="Main Institution Website",
                input_kind="text",
                required_flag=False,
                databricks_view="production.reference.institution_profile",
                databricks_value_column="website",
                databricks_year_column="survey_year",
                transform_json='{"strip": true}',
                status="ACTIVE",
            )
        )
        session.commit()

        payload = service.bootstrap_master_data_points_from_catalog(section_id="pdf_usnews")
        created_master = session.get(MasterDataPoint, "dp.catalog.institution.main.website")
        aliases = service.list_master_aliases("dp.catalog.institution.main.website")

        assert payload.created_count == 1
        assert payload.reused_count == 0
        assert payload.alias_created_count >= 1
        assert created_master is not None
        assert created_master.databricks_view == "production.reference.institution_profile"
        assert created_master.databricks_value_column == "website"
        assert created_master.databricks_year_column == "survey_year"
        assert any(alias.normalized_alias == "main institution website" for alias in aliases)
    finally:
        session.close()


def test_bootstrap_master_data_points_from_fake_form_data_creates_broader_aliases(tmp_path) -> None:
    service, session = _service_with_session()
    try:
        fake_form_path = tmp_path / "fake-survey-form-data.json"
        fake_form_path.write_text(
            json.dumps(
                {
                    "institution_name": "California State University--Long Beach",
                    "homepage": "https://www.csulb.edu",
                    "total_undergraduates": "36703",
                    "__meta": {"ignored": True},
                }
            ),
            encoding="utf-8",
        )

        payload = service.bootstrap_master_data_points_from_fake_form_data(file_path=str(fake_form_path))
        website_aliases = service.list_master_aliases("dp.fake_form.homepage")
        enrollment_aliases = service.list_master_aliases("dp.fake_form.total.undergraduates")
        enrollment_master = session.get(MasterDataPoint, "dp.fake_form.total.undergraduates")

        assert payload.created_count == 3
        assert "dp.fake_form.institution.name" in payload.data_point_ids
        assert any(alias.alias_text == "Main Institution Website" for alias in website_aliases)
        assert any(alias.alias_text == "Total undergraduate enrollment" for alias in enrollment_aliases)
        assert enrollment_master is not None
        assert enrollment_master.databricks_view == "production.silver.erss"
        assert '"resolver_name": "fall_enrollment_counts"' in enrollment_master.transform_json
        assert '"resolver_field": "total_undergraduates"' in enrollment_master.transform_json
    finally:
        session.close()


def test_auto_map_pdf_scan_candidates_maps_high_confidence_and_skips_unmatched() -> None:
    service, session = _service_with_session()
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_auto",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=2,
                raw_result_json="{}",
            )
        )
        session.add_all(
            [
                SurveyPdfDataPointCandidate(
                    candidate_id="cand_site",
                    scan_id="scan_auto",
                    survey_id="survey",
                    candidate_key="acroform.url_destination_url",
                    source="acroform",
                    field_name="URL_DESTINATION_URL",
                    label_text="Main Institution Website",
                    normalized_label="main institution website",
                    input_kind="text",
                    confidence=0.95,
                    label_source="tooltip",
                    field_rect_json="[]",
                    nearby_text="Main Institution Website",
                ),
                SurveyPdfDataPointCandidate(
                    candidate_id="cand_unmatched",
                    scan_id="scan_auto",
                    survey_id="survey",
                    candidate_key="acroform.unknown_field",
                    source="acroform",
                    field_name="UNKNOWN_FIELD",
                    label_text="Completely unrelated signal",
                    normalized_label="completely unrelated signal",
                    input_kind="text",
                    confidence=0.5,
                    label_source="tooltip",
                    field_rect_json="[]",
                    nearby_text="Completely unrelated signal",
                ),
            ]
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.institution.website",
                canonical_name="Main Institution Website",
                semantic_key="institution.website",
                databricks_view="production.reference.institution_profile",
                databricks_value_column="website",
                databricks_year_column="survey_year",
            )
        )
        session.commit()

        payload = service.auto_map_pdf_scan_candidates(scan_id="scan_auto", min_score=70, min_margin=8)
        mapped_candidate = session.get(SurveyPdfDataPointCandidate, "cand_site")
        unmatched_candidate = session.get(SurveyPdfDataPointCandidate, "cand_unmatched")

        assert payload.mapped_count == 1
        assert payload.skipped_no_suggestion == 1
        assert payload.scan_id == "scan_auto"
        assert mapped_candidate is not None
        assert mapped_candidate.master_data_point_id == "dp.institution.website"
        assert mapped_candidate.status == "MAPPED"
        assert unmatched_candidate is not None
        assert unmatched_candidate.master_data_point_id == ""
    finally:
        session.close()


def test_suggest_candidate_mappings_uses_embedding_scorer_when_token_overlap_is_weak() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    service = PdfDatapointService(session, mapping_similarity_scorer=_FakeEmbeddingScorer())
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_embed",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_embed",
                scan_id="scan_embed",
                survey_id="survey",
                candidate_key="acroform.adm_redir",
                source="acroform",
                field_name="ADMISSION_REDIRECT_STATUS",
                label_text="Was this applicant shipped to another university?",
                normalized_label="was this applicant shipped to another university",
                input_kind="text",
                confidence=0.9,
                label_source="nearby_text",
                field_rect_json="[]",
                nearby_text="ship to another university",
            )
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.admissions.redirection_status",
                canonical_name="Redirection status",
                semantic_key="admissions.redirection_status",
                description="Whether applicant was redirected to another campus",
                databricks_view="production.silver.ersa",
                databricks_value_column="admission_status",
                databricks_year_column="years",
            )
        )
        session.commit()

        suggestions = service.suggest_candidate_mappings(scan_id="scan_embed", limit_per_candidate=1)

        assert suggestions[0].suggestions
        assert suggestions[0].suggestions[0].data_point_id == "dp.admissions.redirection_status"
        assert suggestions[0].suggestions[0].score == 93
        assert "embedding" in suggestions[0].suggestions[0].reason
    finally:
        session.close()


def test_generate_pdf_mapping_drafts_creates_persisted_drafts_with_binding_hints() -> None:
    service, session = _service_with_session()
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_draft",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_applied",
                scan_id="scan_draft",
                survey_id="survey",
                candidate_key="acroform.ap_recd_1st_n",
                source="acroform",
                field_name="AP_RECD_1ST_N",
                label_text="Total first-time first-year applicants",
                normalized_label="total first time first year applicants",
                input_kind="text",
                confidence=0.95,
                label_source="tooltip",
                field_rect_json="[]",
                nearby_text="first-time first-year applicants",
            )
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.fake_form.applied.total",
                canonical_name="Total applicants",
                semantic_key="fake_form.applied_total",
                databricks_view="",
                databricks_value_column="",
                databricks_year_column="",
                transform_json="{}",
            )
        )
        session.add(
            MasterDataPointAlias(
                alias_id="alias_applied_total",
                data_point_id="dp.fake_form.applied.total",
                alias_text="Total first-time first-year applicants",
                normalized_alias="total first time first year applicants",
                source="test",
            )
        )
        session.commit()

        payload = service.generate_pdf_mapping_drafts(scan_id="scan_draft", min_score=70)
        persisted = session.query(PdfMappingDraft).filter(PdfMappingDraft.scan_id == "scan_draft").all()

        assert payload.drafted_count == 1
        assert payload.skipped_count == 0
        assert len(payload.drafts) == 1
        assert payload.drafts[0].candidate_id == "cand_applied"
        assert payload.drafts[0].master_data_point_id == "dp.fake_form.applied.total"
        assert payload.drafts[0].databricks_view == "production.silver.ersa"
        assert '"resolver_name": "fall_admissions_counts"' in payload.drafts[0].transform_json
        assert '"resolver_field": "applied_total"' in payload.drafts[0].transform_json
        assert len(persisted) == 1
        assert persisted[0].candidate_id == "cand_applied"
    finally:
        session.close()


def test_generate_pdf_mapping_drafts_overwrite_existing_replaces_old_rows() -> None:
    service, session = _service_with_session()
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_overwrite",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_site",
                scan_id="scan_overwrite",
                survey_id="survey",
                candidate_key="acroform.url_destination_url",
                source="acroform",
                field_name="URL_DESTINATION_URL",
                label_text="Main Institution Website",
                normalized_label="main institution website",
                input_kind="text",
                confidence=0.95,
                label_source="tooltip",
                field_rect_json="[]",
                nearby_text="Main Institution Website",
            )
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.institution.website",
                canonical_name="Main Institution Website",
                semantic_key="institution.website",
                databricks_view="production.reference.institution_profile",
                databricks_value_column="website",
                databricks_year_column="survey_year",
                transform_json="{}",
            )
        )
        session.commit()

        first = service.generate_pdf_mapping_drafts(scan_id="scan_overwrite", min_score=70, overwrite_existing=True)
        second = service.generate_pdf_mapping_drafts(scan_id="scan_overwrite", min_score=70, overwrite_existing=True)
        persisted = session.query(PdfMappingDraft).filter(PdfMappingDraft.scan_id == "scan_overwrite").all()

        assert first.drafted_count == 1
        assert second.drafted_count == 1
        assert len(persisted) == 1
    finally:
        session.close()


def test_list_and_approve_pdf_mapping_draft_maps_candidate_and_applies_binding() -> None:
    service, session = _service_with_session()
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_approve",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_approve",
                scan_id="scan_approve",
                survey_id="survey",
                candidate_key="acroform.ap_recd_1st_n",
                source="acroform",
                field_name="AP_RECD_1ST_N",
                label_text="Total first-time first-year applicants",
                normalized_label="total first time first year applicants",
                input_kind="text",
                confidence=0.95,
                label_source="tooltip",
                field_rect_json="[]",
                nearby_text="first-time first-year applicants",
            )
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.fake_form.applied.total",
                canonical_name="Total applicants",
                semantic_key="fake_form.applied_total",
                databricks_view="",
                databricks_value_column="",
                databricks_year_column="",
                transform_json="{}",
            )
        )
        session.add(
            MasterDataPointAlias(
                alias_id="alias_approve",
                data_point_id="dp.fake_form.applied.total",
                alias_text="Total first-time first-year applicants",
                normalized_alias="total first time first year applicants",
                source="test",
            )
        )
        session.commit()

        generated = service.generate_pdf_mapping_drafts(scan_id="scan_approve", min_score=70)
        listed = service.list_pdf_mapping_drafts(scan_id="scan_approve", status="PENDING_REVIEW")
        approved = service.approve_pdf_mapping_draft(
            draft_id=generated.drafts[0].draft_id,
            apply_binding=True,
            overwrite_master_binding=False,
        )
        candidate = session.get(SurveyPdfDataPointCandidate, "cand_approve")
        master = session.get(MasterDataPoint, "dp.fake_form.applied.total")
        draft_rows = service.list_pdf_mapping_drafts(scan_id="scan_approve")

        assert len(listed) == 1
        assert listed[0].candidate_id == "cand_approve"
        assert approved.candidate_id == "cand_approve"
        assert approved.binding_applied is True
        assert candidate is not None
        assert candidate.master_data_point_id == "dp.fake_form.applied.total"
        assert candidate.status == "MAPPED"
        assert master is not None
        assert master.databricks_view == "production.silver.ersa"
        assert '"resolver_name": "fall_admissions_counts"' in master.transform_json
        assert draft_rows[0].status == "APPROVED"
    finally:
        session.close()


def test_generate_pdf_mapping_drafts_genie_provider_uses_genie_choice() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    service = PdfDatapointService(
        session,
        genie_mapping_client=_FakeGenieClient(
            GenieMappingChoice(
                master_data_point_id="dp.fake_form.applied.total",
                confidence=91,
                reason="matched admissions applied total",
                field_key="applied_total",
            )
        ),
    )
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_genie",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_genie",
                scan_id="scan_genie",
                survey_id="survey",
                candidate_key="acroform.ap_recd_1st_n",
                source="acroform",
                field_name="AP_RECD_1ST_N",
                label_text="Total first-time first-year applicants",
                normalized_label="total first time first year applicants",
                input_kind="text",
                confidence=0.95,
                label_source="tooltip",
                field_rect_json="[]",
                nearby_text="first-time first-year applicants",
            )
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.fake_form.applied.total",
                canonical_name="Total applicants",
                semantic_key="fake_form.applied_total",
                databricks_view="",
                databricks_value_column="",
                databricks_year_column="",
                transform_json="{}",
            )
        )
        session.add(
            MasterDataPointAlias(
                alias_id="alias_genie",
                data_point_id="dp.fake_form.applied.total",
                alias_text="Total first-time first-year applicants",
                normalized_alias="total first time first year applicants",
                source="test",
            )
        )
        session.commit()

        payload = service.generate_pdf_mapping_drafts(
            scan_id="scan_genie",
            provider="genie_api",
            min_score=70,
        )

        assert payload.drafted_count == 1
        assert payload.drafts[0].provider == "genie_api"
        assert payload.drafts[0].score == 91
        assert payload.drafts[0].master_data_point_id == "dp.fake_form.applied.total"
        assert payload.drafts[0].databricks_view == "production.silver.ersa"
        assert '"resolver_name": "fall_admissions_counts"' in payload.drafts[0].transform_json
    finally:
        session.close()


def test_generate_pdf_mapping_drafts_genie_provider_batches_by_50() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    fake_genie = _FakeGenieClient(
        GenieMappingChoice(
            master_data_point_id="dp.fake_form.applied.total",
            confidence=90,
            reason="batch",
            field_key="applied_total",
        )
    )
    service = PdfDatapointService(
        session,
        settings=Settings(databricks_genie_batch_size=50),
        genie_mapping_client=fake_genie,
    )
    try:
        total = 120
        session.add(
            SurveyPdfScan(
                scan_id="scan_genie_batch",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=10,
                candidate_count=total,
                raw_result_json="{}",
            )
        )
        for index in range(total):
            session.add(
                SurveyPdfDataPointCandidate(
                    candidate_id=f"cand_batch_{index}",
                    scan_id="scan_genie_batch",
                    survey_id="survey",
                    candidate_key=f"acroform.ap_recd_1st_n_{index}",
                    source="acroform",
                    field_name="AP_RECD_1ST_N",
                    label_text="Total first-time first-year applicants",
                    normalized_label="total first time first year applicants",
                    input_kind="text",
                    confidence=0.95,
                    label_source="tooltip",
                    field_rect_json="[]",
                    nearby_text="first-time first-year applicants",
                )
            )
        session.add(
            MasterDataPoint(
                data_point_id="dp.fake_form.applied.total",
                canonical_name="Total applicants",
                semantic_key="fake_form.applied_total",
                databricks_view="",
                databricks_value_column="",
                databricks_year_column="",
                transform_json="{}",
            )
        )
        session.add(
            MasterDataPointAlias(
                alias_id="alias_batch",
                data_point_id="dp.fake_form.applied.total",
                alias_text="Total first-time first-year applicants",
                normalized_alias="total first time first year applicants",
                source="test",
            )
        )
        session.commit()

        payload = service.generate_pdf_mapping_drafts(
            scan_id="scan_genie_batch",
            provider="genie_api",
            min_score=70,
            limit_candidates=total,
        )

        assert payload.drafted_count == total
        assert fake_genie.calls == 3
        assert sum(fake_genie.batch_sizes) == total
    finally:
        session.close()


def test_generate_pdf_mapping_drafts_genie_provider_splits_batches_by_prompt_size() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    fake_genie = _FakeGenieClient(
        GenieMappingChoice(
            master_data_point_id="dp.fake_form.applied.total",
            confidence=90,
            reason="size split",
            field_key="applied_total",
        )
    )
    service = PdfDatapointService(
        session,
        settings=Settings(
            databricks_genie_batch_size=100,
            databricks_genie_max_prompt_chars=2500,
            databricks_genie_options_per_candidate=2,
        ),
        genie_mapping_client=fake_genie,
    )
    try:
        total = 4
        long_context = " ".join(["context"] * 260)
        session.add(
            SurveyPdfScan(
                scan_id="scan_genie_size_batch",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=10,
                candidate_count=total,
                raw_result_json="{}",
            )
        )
        for index in range(total):
            session.add(
                SurveyPdfDataPointCandidate(
                    candidate_id=f"cand_size_batch_{index}",
                    scan_id="scan_genie_size_batch",
                    survey_id="survey",
                    candidate_key=f"acroform.ap_recd_1st_n_{index}",
                    source="acroform",
                    field_name="AP_RECD_1ST_N",
                    label_text=f"Total first-time first-year applicants {long_context}",
                    normalized_label=f"total first time first year applicants {long_context}",
                    input_kind="text",
                    confidence=0.95,
                    label_source="tooltip",
                    field_rect_json="[]",
                    nearby_text=f"first-time first-year applicants {long_context}",
                )
            )
        session.add(
            MasterDataPoint(
                data_point_id="dp.fake_form.applied.total",
                canonical_name="Total applicants",
                semantic_key="fake_form.applied_total",
                databricks_view="",
                databricks_value_column="",
                databricks_year_column="",
                transform_json="{}",
            )
        )
        session.add(
            MasterDataPointAlias(
                alias_id="alias_size_batch",
                data_point_id="dp.fake_form.applied.total",
                alias_text="Total first-time first-year applicants",
                normalized_alias="total first time first year applicants",
                source="test",
            )
        )
        session.commit()

        payload = service.generate_pdf_mapping_drafts(
            scan_id="scan_genie_size_batch",
            provider="genie_api",
            min_score=70,
            limit_candidates=total,
        )

        assert payload.drafted_count == total
        assert fake_genie.calls > 1
        assert sum(fake_genie.batch_sizes) == total
        assert max(fake_genie.batch_sizes) < total
    finally:
        session.close()


def test_generate_pdf_mapping_drafts_genie_provider_fails_when_genie_returns_no_choices() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    service = PdfDatapointService(
        session,
        genie_mapping_client=_FakeGenieClient(None),
    )
    try:
        session.add(
            SurveyPdfScan(
                scan_id="scan_genie_empty",
                survey_id="survey",
                file_name="survey.pdf",
                file_path="/tmp/survey.pdf",
                file_sha256="abc",
                fillable=True,
                page_count=1,
                candidate_count=1,
                raw_result_json="{}",
            )
        )
        session.add(
            SurveyPdfDataPointCandidate(
                candidate_id="cand_empty",
                scan_id="scan_genie_empty",
                survey_id="survey",
                candidate_key="acroform.ap_recd_1st_n",
                source="acroform",
                field_name="AP_RECD_1ST_N",
                label_text="Total first-time first-year applicants",
                normalized_label="total first time first year applicants",
                input_kind="text",
                confidence=0.95,
                label_source="tooltip",
                field_rect_json="[]",
                nearby_text="first-time first-year applicants",
            )
        )
        session.add(
            MasterDataPoint(
                data_point_id="dp.fake_form.applied.total",
                canonical_name="Total applicants",
                semantic_key="fake_form.applied_total",
                databricks_view="",
                databricks_value_column="",
                databricks_year_column="",
                transform_json="{}",
            )
        )
        session.add(
            MasterDataPointAlias(
                alias_id="alias_empty",
                data_point_id="dp.fake_form.applied.total",
                alias_text="Total first-time first-year applicants",
                normalized_alias="total first time first year applicants",
                source="test",
            )
        )
        session.commit()

        try:
            service.generate_pdf_mapping_drafts(
                scan_id="scan_genie_empty",
                provider="genie_api",
                min_score=70,
            )
        except RuntimeError as exc:
            assert "Genie API returned no parseable mapping choices" in str(exc)
        else:
            raise AssertionError("Expected empty Genie choices to fail")
    finally:
        session.close()


def test_resolve_pdf_scan_via_genie_persists_updates_after_session_close(tmp_path, monkeypatch) -> None:
    session_factory = _file_session_factory(tmp_path)
    seed_session = session_factory()
    try:
        _add_scan_with_candidate(seed_session)
    finally:
        seed_session.close()

    monkeypatch.setattr(
        "apps.api.databricks_genie_client.DatabricksGenieClient",
        _ResolvingFakeGenieClient,
    )

    service_session = session_factory()
    try:
        service = PdfDatapointService(service_session, settings=Settings())
        result = service.resolve_pdf_scan_via_genie(
            scan_id="scan_commit",
            survey_year=2024,
            min_confidence=60,
        )
        assert result["resolved"] == 1
    finally:
        service_session.close()

    verify_session = session_factory()
    try:
        row = verify_session.get(SurveyPdfDataPointCandidate, "cand_commit")
        assert row is not None
        assert row.status == "GENIE_RESOLVED"
        assert row.genie_value == "42"
        assert row.genie_sql_template == "SELECT '42' AS value WHERE survey_year = __SURVEY_YEAR__"
        assert row.genie_confidence == 88
    finally:
        verify_session.close()


def test_resolve_pdf_scan_direct_persists_updates_after_session_close(tmp_path, monkeypatch) -> None:
    session_factory = _file_session_factory(tmp_path)
    seed_session = session_factory()
    try:
        _add_scan_with_candidate(
            seed_session,
            scan_id="scan_direct_commit",
            candidate_id="cand_direct_commit",
            genie_sql_template="SELECT '42' AS value WHERE survey_year = __SURVEY_YEAR__",
        )
    finally:
        seed_session.close()

    monkeypatch.setattr(
        "apps.api.databricks_resolver.DatabricksSqlValueReader",
        _RefreshingFakeSqlReader,
    )

    service_session = session_factory()
    try:
        service = PdfDatapointService(service_session, settings=Settings(databricks_sql_warehouse_id="warehouse"))
        result = service.resolve_pdf_scan_direct(scan_id="scan_direct_commit", survey_year=2024)
        assert result["refreshed"] == 1
    finally:
        service_session.close()

    verify_session = session_factory()
    try:
        row = verify_session.get(SurveyPdfDataPointCandidate, "cand_direct_commit")
        assert row is not None
        assert row.genie_value == "43"
        assert row.direct_sql_failures == 0
    finally:
        verify_session.close()


def test_resolve_pdf_scan_uses_cds_registry_without_genie_for_known_field(tmp_path, monkeypatch) -> None:
    session_factory = _file_session_factory(tmp_path)
    seed_session = session_factory()
    try:
        _add_scan_with_candidate(
            seed_session,
            scan_id="scan_registry",
            candidate_id="cand_retention",
        )
        row = seed_session.get(SurveyPdfDataPointCandidate, "cand_retention")
        assert row is not None
        row.field_name = "RETENTION_FRSH_N"
        row.label_text = "First-year retention cohort"
        row.normalized_label = "first year retention cohort"
        row.label_source = "field_name"
        row.nearby_text = "Section: B22 Retention\nFirst-year retention cohort"
        row.genie_sql_template = ""
        row.genie_value = ""
        row.status = "DISCOVERED"
        seed_session.commit()
    finally:
        seed_session.close()

    _RegistryFakeSqlReader.calls = []
    monkeypatch.setattr(
        "apps.api.databricks_resolver.DatabricksSqlValueReader",
        _RegistryFakeSqlReader,
    )

    service_session = session_factory()
    try:
        service = PdfDatapointService(
            service_session,
            settings=Settings(databricks_sql_warehouse_id="warehouse"),
        )
        result = service.resolve_pdf_scan_via_genie(
            scan_id="scan_registry",
            survey_year=2025,
            min_confidence=60,
        )
        assert result["resolved"] == 1
    finally:
        service_session.close()

    verify_session = session_factory()
    try:
        row = verify_session.get(SurveyPdfDataPointCandidate, "cand_retention")
        assert row is not None
        assert row.status == "GENIE_RESOLVED"
        assert row.genie_value == "6267"
        assert row.genie_confidence == 100
        assert "cds registry" in row.genie_reason.lower()
        assert "RETENTION_FRSH_N" in row.genie_sql_template
        assert "production.silver.erss" in row.genie_sql_template
        assert "IRAMASTER.ERSS" not in row.genie_sql_template
        assert _RegistryFakeSqlReader.calls
        assert "YEARS = 2025" in _RegistryFakeSqlReader.calls[0]
        assert "TERM = '4'" in _RegistryFakeSqlReader.calls[0]
    finally:
        verify_session.close()


def test_resolve_pdf_scan_falls_back_to_genie_for_unmapped_registry_field(tmp_path, monkeypatch) -> None:
    session_factory = _file_session_factory(tmp_path)
    seed_session = session_factory()
    try:
        _add_scan_with_candidate(
            seed_session,
            scan_id="scan_registry_fallback",
            candidate_id="cand_unknown",
        )
        row = seed_session.get(SurveyPdfDataPointCandidate, "cand_unknown")
        assert row is not None
        row.field_name = "UNKNOWN_FIELD"
        row.label_source = "openai_enriched"
        row.genie_sql_template = ""
        row.genie_value = ""
        seed_session.commit()
    finally:
        seed_session.close()

    monkeypatch.setattr(
        "apps.api.databricks_genie_client.DatabricksGenieClient",
        _ResolvingFakeGenieClient,
    )

    service_session = session_factory()
    try:
        service = PdfDatapointService(service_session, settings=Settings())
        result = service.resolve_pdf_scan_via_genie(scan_id="scan_registry_fallback", survey_year=2025)
        assert result["resolved"] == 1
    finally:
        service_session.close()

    verify_session = session_factory()
    try:
        row = verify_session.get(SurveyPdfDataPointCandidate, "cand_unknown")
        assert row is not None
        assert row.genie_value == "42"
        assert row.genie_reason == "test resolution"
    finally:
        verify_session.close()


def test_registry_resolved_sql_template_refreshes_with_direct_sql(tmp_path, monkeypatch) -> None:
    session_factory = _file_session_factory(tmp_path)
    seed_session = session_factory()
    try:
        _add_scan_with_candidate(
            seed_session,
            scan_id="scan_registry_direct",
            candidate_id="cand_retention_direct",
        )
        row = seed_session.get(SurveyPdfDataPointCandidate, "cand_retention_direct")
        assert row is not None
        row.field_name = "RETENTION_FRSH_N"
        row.label_source = "openai_enriched"
        row.genie_sql_template = ""
        row.genie_value = ""
        seed_session.commit()
    finally:
        seed_session.close()

    _RegistryFakeSqlReader.calls = []
    monkeypatch.setattr(
        "apps.api.databricks_resolver.DatabricksSqlValueReader",
        _RegistryFakeSqlReader,
    )

    service_session = session_factory()
    try:
        service = PdfDatapointService(
            service_session,
            settings=Settings(databricks_sql_warehouse_id="warehouse"),
        )
        service.resolve_pdf_scan_via_genie(scan_id="scan_registry_direct", survey_year=2025)
        result = service.resolve_pdf_scan_direct(scan_id="scan_registry_direct", survey_year=2025)
        assert result["refreshed"] == 1
    finally:
        service_session.close()

    verify_session = session_factory()
    try:
        row = verify_session.get(SurveyPdfDataPointCandidate, "cand_retention_direct")
        assert row is not None
        assert row.genie_value == "6267"
        assert row.direct_sql_failures == 0
        assert len(_RegistryFakeSqlReader.calls) >= 2
    finally:
        verify_session.close()


def test_cds_registry_maps_dimensional_c1_results_to_pdf_field_values() -> None:
    registry = CdsQueryRegistry.default()
    query = registry.query_for_field("AP_RECD_1ST_N")

    assert query is not None
    value = registry.extract_value(
        query=query,
        field_name="AP_RECD_1ST_N",
        columns=["dimension", "bucket", "applied_n", "admitted_n", "enrolled_n", "enrolled_ft_n", "enrolled_pt_n"],
        rows=[
            ["sex", "MEN", 100, 50, 10, 9, 1],
            ["total", "TOTAL", 88424, 40105, 5884, None, None],
        ],
    )

    assert value == "88424"


def test_registry_resolution_overwrites_existing_genie_sql_for_known_field(tmp_path, monkeypatch) -> None:
    session_factory = _file_session_factory(tmp_path)
    seed_session = session_factory()
    try:
        _add_scan_with_candidate(
            seed_session,
            scan_id="scan_registry_overwrite",
            candidate_id="cand_retention_overwrite",
            genie_sql_template="SELECT 'old' AS value WHERE survey_year = __SURVEY_YEAR__",
        )
        row = seed_session.get(SurveyPdfDataPointCandidate, "cand_retention_overwrite")
        assert row is not None
        row.field_name = "RETENTION_FRSH_N"
        row.label_source = "openai_enriched"
        row.genie_value = "old"
        row.genie_reason = "resolved via Genie narrow-format query"
        row.status = "GENIE_RESOLVED"
        seed_session.commit()
    finally:
        seed_session.close()

    _RegistryFakeSqlReader.calls = []
    monkeypatch.setattr(
        "apps.api.databricks_resolver.DatabricksSqlValueReader",
        _RegistryFakeSqlReader,
    )

    service_session = session_factory()
    try:
        service = PdfDatapointService(
            service_session,
            settings=Settings(databricks_sql_warehouse_id="warehouse"),
        )
        result = service.resolve_pdf_scan_via_genie(scan_id="scan_registry_overwrite", survey_year=2025)
        assert result["resolved"] == 1
    finally:
        service_session.close()

    verify_session = session_factory()
    try:
        row = verify_session.get(SurveyPdfDataPointCandidate, "cand_retention_overwrite")
        assert row is not None
        assert row.genie_value == "6267"
        assert row.genie_reason.startswith("Resolved via CDS registry")
        assert row.genie_sql_template != "SELECT 'old' AS value WHERE survey_year = __SURVEY_YEAR__"
    finally:
        verify_session.close()


def test_cds_registry_rewrites_deprecated_iramaster_ers_tables_to_production_silver() -> None:
    registry = CdsQueryRegistry.default()

    for field_name, expected_table in [
        ("EN_FRSH_FT_MEN_N", "production.silver.erss"),
        ("AP_RECD_1ST_N", "production.silver.ersa"),
        ("DEG_BACH_N", "production.silver.ersd"),
    ]:
        query = registry.query_for_field(field_name)
        assert query is not None
        assert expected_table in query.sql_template
        assert "IRAMASTER.ERSS" not in query.sql_template
        assert "IRAMASTER.ERSA" not in query.sql_template
        assert "IRAMASTER.ERSD" not in query.sql_template


def test_cds_registry_renders_production_silver_term_filters() -> None:
    from apps.api.cds_query_registry import apply_registry_year

    registry = CdsQueryRegistry.default()
    b1 = registry.query_for_field("EN_FRSH_FT_MEN_N")
    c1 = registry.query_for_field("AP_RECD_1ST_N")

    assert b1 is not None
    assert c1 is not None
    rendered_b1 = apply_registry_year(b1.sql_template, 2025)
    rendered_c1 = apply_registry_year(c1.sql_template, 2025)
    assert "YEARS || TERM" not in rendered_b1
    assert "YEARS || TERM" not in rendered_c1
    assert "YEARS = 2025" in rendered_b1
    assert "TERM = '4'" in rendered_b1
    assert "CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT) = 20254" in rendered_c1


def test_cds_registry_rewrites_deprecated_ethnicity_function_to_production_function() -> None:
    registry = CdsQueryRegistry.default()
    query = registry.query_for_field("EN_1ST_HISPANIC_ETHNICITY_N")

    assert query is not None
    assert "production.functions.ira_ethnicity" in query.sql_template
    assert "IRAMASTER.ETHNICITY" not in query.sql_template


def test_cds_registry_uses_current_ira_ethnicity_signature() -> None:
    registry = CdsQueryRegistry.default()
    query = registry.query_for_field("EN_1ST_HISPANIC_ETHNICITY_N")

    assert query is not None
    assert "production.functions.ira_ethnicity" in query.sql_template
    assert "MULTIPLE_RACE_CATEGORY" not in query.sql_template
    assert "ETHNIC_CODE_OLD" in query.sql_template
    assert "CAST(YEARS AS INT) * 10 + CAST(TERM AS INT)" in query.sql_template

def test_cds_registry_rewrites_fin_aid_table_to_cms_awards() -> None:
    registry = CdsQueryRegistry.default()
    query = registry.query_for_field("GRS_BACH_INIT_PELL_N")
    assert query is not None
    assert "bronze.cms.ps_stdnt_awards" in query.sql_template
    assert "LEFT JOIN cds_fin_aid_status" not in query.sql_template


def test_cds_registry_rewrites_hegis_table_to_production_reference() -> None:
    registry = CdsQueryRegistry.default()
    query = registry.query_for_field("BACH_TOT_P")
    assert query is not None
    assert "production.reference.ira_ss_hegis_cip" in query.sql_template
    assert "iramaster.SS_HEGIS_CIP" not in query.sql_template.lower()


def test_cds_registry_f1_uses_date_diff_for_age() -> None:
    from apps.api.cds_query_registry import apply_registry_year

    registry = CdsQueryRegistry.default()
    query = registry.query_for_field("EN_1ST_OLD_P")
    assert query is not None
    assert "DATEDIFF" in apply_registry_year(query.sql_template, 2025)
    assert "TO_DATE(A.BIRTH_DATE)" not in apply_registry_year(query.sql_template, 2025)


def test_cds_registry_b1_mapper_computes_totals_from_dimensional_rows() -> None:
    registry = CdsQueryRegistry.default()
    query = next(q for q in registry._queries if q.query_id == "Q-B1")
    columns = ["row_bucket", "load_bucket", "sex_bucket", "value"]
    rows = [
        ["FRSH", "FT", "MEN", "100"],
        ["FRSH", "FT", "WMN", "200"],
        ["OTH_1ST", "FT", "MEN", "10"],
        ["DEG", "FT", "MEN", "50"],
        ["GRAD", "FT", "MEN", "5"],
    ]
    assert registry.extract_value(query=query, field_name="EN_FRSH_FT_MEN_N", columns=columns, rows=rows) == "100"
    assert registry.extract_value(query=query, field_name="EN_TOT_DEG_FT_MEN_N", columns=columns, rows=rows) == "160"
    assert registry.extract_value(query=query, field_name="EN_GRAD_DEG_FT_MEN_N", columns=columns, rows=rows) == "5"


def test_cds_registry_b2_maps_visa_non_us_to_nonresident_fields() -> None:
    registry = CdsQueryRegistry.default()
    query = next(q for q in registry._queries if q.query_id == "Q-B2")
    columns = ["race_ethnicity", "first_time_first_year_n", "degree_seeking_ug_n", "total_ug_n"]
    rows = [["Visa Non-U.S.", "109", "882", "882"]]
    assert registry.extract_value(query=query, field_name="EN_1ST_NONRES_ALIEN_1ST_N", columns=columns, rows=rows) == "109"


def test_cds_registry_uses_analyst_section_c_2025_query_patterns() -> None:
    from apps.api.cds_query_registry import apply_registry_year

    registry = CdsQueryRegistry.default()
    c1 = registry.query_for_field("AP_RECD_1ST_MEN_N")
    c11 = registry.query_for_field("FRSH_GPA_SUBMIT_1_P")

    assert c1 is not None
    assert c11 is not None
    rendered_c1 = apply_registry_year(c1.sql_template, 2025)
    rendered_c11 = apply_registry_year(c11.sql_template, 2025)
    assert "GENDER_IDENTITY_CODE" in rendered_c1
    assert "RESIDENCE_CODE" in rendered_c1
    assert "CAST(A.YEARS AS INT) * 10 + CAST(A.TERM AS INT) = 20254" in rendered_c1
    assert "HS_GPA >= 400" in rendered_c11
    assert "production.silver.erss" in rendered_c11


def test_cds_registry_maps_section_i1_faculty_results_to_pdf_field_values() -> None:
    registry = CdsQueryRegistry.default()
    query = registry.query_for_field("FT_N")

    assert query is not None
    columns = ["metric", "ft_n", "pt_n", "total_n"]
    rows = [
        ["TOTAL", "1063", "1545", "2608"],
        ["MINORITY", "410", "525", "935"],
        ["WOMEN", "555", "805", "1360"],
        ["MEN", "508", "740", "1248"],
        ["NONRESIDENT", "25", "40", "65"],
        ["TERMINAL_DEGREE", "840", "620", "1460"],
        ["MASTERS_NON_TERMINAL", "120", "500", "620"],
        ["BACHELORS", "60", "300", "360"],
        ["OTHER_UNKNOWN", "43", "125", "168"],
        ["GRAD_ONLY", "12", "8", "20"],
    ]

    assert registry.extract_value(query=query, field_name="FT_N", columns=columns, rows=rows) == "1063"
    assert registry.extract_value(query=query, field_name="PT_N", columns=columns, rows=rows) == "1545"
    assert registry.extract_value(query=query, field_name="TOT_N", columns=columns, rows=rows) == "2608"
    assert registry.extract_value(query=query, field_name="MIN_TOT_N", columns=columns, rows=rows) == "935"
    assert registry.extract_value(query=query, field_name="TOT_WMN_N", columns=columns, rows=rows) == "1360"
    assert registry.extract_value(query=query, field_name="FT_DEG_TERM_N", columns=columns, rows=rows) == "840"
    assert registry.extract_value(query=query, field_name="GRAD_TOT_N", columns=columns, rows=rows) == "20"
