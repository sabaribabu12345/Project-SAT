from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExecuteSectionPipelineInput:
    run_id: str
    section_id: str
    portal_url: str
    browser_session_id: str | None = None


@dataclass
class ExecuteSectionPipelineResult:
    run_id: str
    section_id: str
    scan_task_id: str
    scan_workflow_id: str
    validate_task_ids: list[str]
    validate_workflow_ids: list[str]
    resolved_field_count: int

    @property
    def expected_workflow_ids(self) -> list[str]:
        return [self.scan_workflow_id, *self.validate_workflow_ids]


@dataclass
class ExecuteDraftFillPipelineInput:
    run_id: str
    section_id: str
    portal_url: str
    browser_session_id: str | None = None


@dataclass
class ExecuteDraftFillPipelineResult:
    run_id: str
    section_id: str
    fill_task_ids: list[str]
    fill_workflow_ids: list[str]
    field_count: int
    submit_enabled: bool

    @property
    def expected_workflow_ids(self) -> list[str]:
        return self.fill_workflow_ids


@dataclass
class ListStageWorkflowIdsInput:
    run_id: str
    section_id: str
    purpose: str
    stage: str


@dataclass
class ListStageWorkflowIdsResult:
    task_ids: list[str]
    workflow_ids: list[str]


@dataclass
class RecordRunEventInput:
    run_id: str
    event_type: str
    payload: dict[str, object]
