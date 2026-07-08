from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from apps.api.db.models import (
    Base,
    RunEvent,
    ReviewItem,
    SkyvernTask,
    SurveyFieldCatalog,
    SurveyPdfDataPointCandidate,
    SurveyPdfScan,
)
from apps.api.service import Slice1Service
from apps.api.settings import Settings
from apps.skyvern_worker.skyvern_client import SkyvernWorkflow


class FakeSkyvernClient:
    def __init__(self) -> None:
        self._counter = 0
        self.fill_prompts: list[str] = []
        self.validate_prompts: list[str] = []

    def create_scan_workflow(
        self,
        user_prompt: str,
        extracted_information_schema: dict[str, dict[str, str]],
        max_steps: int = 35,
        browser_session_id: str | None = None,
    ) -> SkyvernWorkflow:
        del user_prompt, extracted_information_schema, max_steps, browser_session_id
        return self._next_workflow("scan")

    def create_validate_workflow(
        self,
        user_prompt: str,
        extracted_information_schema: dict[str, dict[str, str]],
        max_steps: int = 25,
        browser_session_id: str | None = None,
    ) -> SkyvernWorkflow:
        del extracted_information_schema, max_steps, browser_session_id
        self.validate_prompts.append(user_prompt)
        return self._next_workflow("validate")

    def create_fill_workflow(
        self,
        user_prompt: str,
        extracted_information_schema: dict[str, dict[str, str]],
        max_steps: int = 35,
        browser_session_id: str | None = None,
    ) -> SkyvernWorkflow:
        del extracted_information_schema, max_steps, browser_session_id
        self.fill_prompts.append(user_prompt)
        return self._next_workflow("fill")

    def _next_workflow(self, prefix: str) -> SkyvernWorkflow:
        self._counter += 1
        workflow_id = f"wf_{prefix}_{self._counter}"
        return SkyvernWorkflow(workflow_id=workflow_id, raw_response={"workflow_id": workflow_id})


def test_execute_section_pipeline_creates_missing_review_once() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    with session_factory() as session:
        service = Slice1Service(
            session=session,
            skyvern_client=FakeSkyvernClient(),  # type: ignore[arg-type]
            webhook_callback_url="http://control-plane:8010/webhooks/skyvern",
            settings=Settings(
                skyvern_api_key="test-key",
                skyvern_max_fields_per_task=8,
                databricks_resolver_mode="fake",
            ),
        )
        service.create_run(run_id="run_test", survey_id="usnews_main", survey_year=2026)
        session.add_all(
            [
                SurveyFieldCatalog(
                    field_id="institution.name",
                    section_id="institution",
                    label_text="Institution Name",
                    input_kind="text",
                    required_flag=True,
                    databricks_view="surveys.usnews_main.v_institution_name",
                    databricks_value_column="value",
                    databricks_year_column="survey_year",
                    transform_json="{}",
                    status="ACTIVE",
                ),
                SurveyFieldCatalog(
                    field_id="institution.missing",
                    section_id="institution",
                    label_text="Missing Field",
                    input_kind="text",
                    required_flag=False,
                    databricks_view="surveys.usnews_main.v_missing",
                    databricks_value_column="value",
                    databricks_year_column="survey_year",
                    transform_json="{}",
                    status="ACTIVE",
                ),
            ]
        )
        session.commit()

        result = service.execute_section_pipeline(
            run_id="run_test",
            section_id="institution",
            portal_url="http://fake-form",
        )

        review_items = list(session.execute(select(ReviewItem)).scalars())
        assert result["resolved_field_count"] == 1
        assert len(result["validate_task_ids"]) == 1
        assert result["scan_workflow_id"] == "wf_scan_1"
        assert result["validate_workflow_ids"] == ["wf_validate_2"]
        assert len(review_items) == 1
        assert review_items[0].field_id == "institution.missing"
        assert review_items[0].reason_code == "MISSING_IN_DATABRICKS"


def test_draft_fill_pipeline_uses_no_submit_prompt_and_triggers_post_fill_validate() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    with session_factory() as session:
        fake_skyvern = FakeSkyvernClient()
        service = Slice1Service(
            session=session,
            skyvern_client=fake_skyvern,  # type: ignore[arg-type]
            webhook_callback_url="http://control-plane:8010/webhooks/skyvern",
            settings=Settings(
                skyvern_api_key="test-key",
                skyvern_max_fields_per_task=20,
                databricks_resolver_mode="fake",
            ),
        )
        service.create_run(run_id="run_fill", survey_id="usnews_main", survey_year=2026)
        service.bootstrap_section_catalog("institution")

        result = service.execute_draft_fill_pipeline(
            run_id="run_fill",
            section_id="institution",
            portal_url="http://fake-form",
        )
        fill_workflow_id = result["fill_workflow_ids"][0]

        assert result["submit_enabled"] is False
        assert fake_skyvern.fill_prompts
        assert "Do not click Submit" in fake_skyvern.fill_prompts[0]
        assert "stop without saving" in fake_skyvern.fill_prompts[0]

        service.process_skyvern_webhook(
            {
                "workflow_id": fill_workflow_id,
                "status": "completed",
                "extracted_information": {
                    "filled_fields_json": "{}",
                    "submit_attempted": "false",
                },
            }
        )

        validate_tasks = list(
            session.execute(
                select(SkyvernTask)
                .where(SkyvernTask.purpose == "validate")
                .where(SkyvernTask.stage == "post_fill_validate")
            ).scalars()
        )
        events = list(session.execute(select(RunEvent).where(RunEvent.run_id == "run_fill")).scalars())

        assert len(validate_tasks) == 1
        assert validate_tasks[0].chunk_index == 0
        assert any(event.event_type == "POST_FILL_VALIDATE_READY" for event in events)
        assert any(event.event_type == "POST_FILL_VALIDATE_STAGE_DISPATCHED" for event in events)


def test_draft_fill_pipeline_can_use_pdf_genie_resolved_values() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    with session_factory() as session:
        fake_skyvern = FakeSkyvernClient()
        service = Slice1Service(
            session=session,
            skyvern_client=fake_skyvern,  # type: ignore[arg-type]
            webhook_callback_url="http://control-plane:8010/webhooks/skyvern",
            settings=Settings(
                skyvern_api_key="test-key",
                skyvern_max_fields_per_task=20,
                databricks_resolver_mode="fake",
            ),
        )
        service.create_run(run_id="run_pdf_fill", survey_id="usnews_pdf", survey_year=2024)
        session.add(
            SurveyPdfScan(
                scan_id="scan_genie_fill",
                survey_id="usnews_pdf",
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
                    candidate_id="cand_applicants",
                    scan_id="scan_genie_fill",
                    survey_id="usnews_pdf",
                    candidate_key="acroform.ap_recd_1st_n",
                    source="acroform",
                    field_name="AP_RECD_1ST_N",
                    label_text="Total first-time first-year applicants",
                    normalized_label="total first time first year applicants",
                    input_kind="number",
                    confidence=0.95,
                    label_source="openai_enriched",
                    field_rect_json="[]",
                    nearby_text="Admissions | Section: Admissions | Original nearby text: applicants",
                    genie_sql_template="SELECT total FROM table WHERE survey_year = __SURVEY_YEAR__",
                    genie_value="12345",
                    genie_confidence=91,
                    status="GENIE_RESOLVED",
                ),
                SurveyPdfDataPointCandidate(
                    candidate_id="cand_low_confidence",
                    scan_id="scan_genie_fill",
                    survey_id="usnews_pdf",
                    candidate_key="acroform.low_confidence",
                    source="acroform",
                    field_name="LOW_CONFIDENCE",
                    label_text="Low confidence value",
                    normalized_label="low confidence value",
                    input_kind="text",
                    confidence=0.95,
                    label_source="openai_enriched",
                    field_rect_json="[]",
                    nearby_text="Admissions | Section: Admissions | Original nearby text: low",
                    genie_sql_template="SELECT value FROM table WHERE survey_year = __SURVEY_YEAR__",
                    genie_value="do-not-fill",
                    genie_confidence=42,
                    status="GENIE_LOW_CONFIDENCE",
                ),
            ]
        )
        session.commit()

        result = service.execute_draft_fill_pipeline(
            run_id="run_pdf_fill",
            section_id="admissions",
            portal_url="http://fake-form",
            scan_id="scan_genie_fill",
        )

        assert result["field_count"] == 1
        assert fake_skyvern.fill_prompts
        assert "Total first-time first-year applicants: 12345" in fake_skyvern.fill_prompts[0]
        assert "Low confidence value" not in fake_skyvern.fill_prompts[0]
        task = session.get(SkyvernTask, result["fill_task_ids"][0])
        assert task is not None
        assert task.expected_values_json == '{"cand_applicants": "12345"}'


def test_pdf_genie_fill_completion_dispatches_pdf_post_fill_validation() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    with session_factory() as session:
        fake_skyvern = FakeSkyvernClient()
        service = Slice1Service(
            session=session,
            skyvern_client=fake_skyvern,  # type: ignore[arg-type]
            webhook_callback_url="http://control-plane:8010/webhooks/skyvern",
            settings=Settings(
                skyvern_api_key="test-key",
                skyvern_max_fields_per_task=20,
                databricks_resolver_mode="fake",
            ),
        )
        service.create_run(run_id="run_pdf_validate", survey_id="usnews_pdf", survey_year=2024)
        session.add(
            SurveyPdfScan(
                scan_id="scan_pdf_validate",
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
                candidate_id="cand_enrolled",
                scan_id="scan_pdf_validate",
                survey_id="usnews_pdf",
                candidate_key="acroform.enrolled_total",
                source="acroform",
                field_name="ENROLLED_TOTAL",
                label_text="Total enrolled students",
                normalized_label="total enrolled students",
                input_kind="number",
                confidence=0.95,
                label_source="openai_enriched",
                field_rect_json="[]",
                nearby_text="Enrollment | Section: Enrollment | Original nearby text: enrolled",
                genie_sql_template="SELECT enrolled FROM table WHERE survey_year = __SURVEY_YEAR__",
                genie_value="9876",
                genie_confidence=88,
                status="GENIE_RESOLVED",
            )
        )
        session.commit()

        result = service.execute_draft_fill_pipeline(
            run_id="run_pdf_validate",
            section_id="enrollment",
            portal_url="http://fake-form",
            scan_id="scan_pdf_validate",
        )
        fill_workflow_id = result["fill_workflow_ids"][0]

        service.process_skyvern_webhook(
            {
                "workflow_id": fill_workflow_id,
                "status": "completed",
                "extracted_information": {
                    "filled_fields_json": "{}",
                    "submit_attempted": "false",
                },
            }
        )

        validate_tasks = list(
            session.execute(
                select(SkyvernTask)
                .where(SkyvernTask.purpose == "validate")
                .where(SkyvernTask.stage == "post_fill_validate")
            ).scalars()
        )
        assert len(validate_tasks) == 1
        assert validate_tasks[0].expected_values_json == '{"cand_enrolled": "9876"}'
        assert fake_skyvern.validate_prompts
        assert "Total enrolled students" in fake_skyvern.validate_prompts[-1]
