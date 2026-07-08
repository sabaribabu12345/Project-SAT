from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from apps.temporal_worker.types import (
        ExecuteDraftFillPipelineInput,
        ExecuteSectionPipelineInput,
        ListStageWorkflowIdsInput,
        RecordRunEventInput,
    )


@dataclass
class RunWorkflowState:
    run_id: str
    section_id: str
    portal_url: str
    workflow_mode: str = "validate"
    stage: str = "initialized"
    scan_task_id: str = ""
    fill_task_ids: list[str] = field(default_factory=list)
    validate_task_ids: list[str] = field(default_factory=list)
    expected_workflow_ids: list[str] = field(default_factory=list)
    received_workflow_ids: list[str] = field(default_factory=list)
    duplicate_callback_count: int = 0
    unexpected_callback_count: int = 0
    webhook_events: list[dict[str, object]] = field(default_factory=list)


@workflow.defn
class RunWorkflow:
    def __init__(self) -> None:
        self._state: RunWorkflowState | None = None

    @workflow.run
    async def run(
        self,
        run_id: str,
        section_id: str,
        portal_url: str = "http://fake-form",
        callback_timeout_seconds: int = 1800,
        workflow_mode: str = "validate",
        browser_session_id: str | None = None,
    ) -> dict[str, object]:
        # Derive a stable session ID from run+section so every task in this
        # section reuses the same authenticated browser context.
        effective_session_id = browser_session_id or f"sess_{run_id}_{section_id}"
        self._state = RunWorkflowState(
            run_id=run_id,
            section_id=section_id,
            portal_url=portal_url,
            workflow_mode=workflow_mode,
            stage="dispatching_pipeline",
        )
        if workflow_mode == "draft_fill":
            return await self._run_draft_fill_mode(
                run_id=run_id,
                section_id=section_id,
                portal_url=portal_url,
                callback_timeout_seconds=callback_timeout_seconds,
                browser_session_id=effective_session_id,
            )
        if workflow_mode != "validate":
            raise ValueError(f"Unsupported workflow_mode: {workflow_mode}")
        return await self._run_validate_mode(
            run_id=run_id,
            section_id=section_id,
            portal_url=portal_url,
            callback_timeout_seconds=callback_timeout_seconds,
            browser_session_id=effective_session_id,
        )

    async def _run_validate_mode(
        self,
        run_id: str,
        section_id: str,
        portal_url: str,
        callback_timeout_seconds: int,
        browser_session_id: str | None = None,
    ) -> dict[str, object]:
        pipeline_result = await workflow.execute_activity(
            "execute_section_pipeline_activity",
            ExecuteSectionPipelineInput(run_id=run_id, section_id=section_id, portal_url=portal_url, browser_session_id=browser_session_id),
            start_to_close_timeout=timedelta(minutes=5),
        )
        self._state.scan_task_id = pipeline_result.scan_task_id
        self._state.validate_task_ids = pipeline_result.validate_task_ids
        phase_result = await self._wait_for_expected_workflows(
            phase="validate_callbacks",
            expected_workflow_ids=pipeline_result.expected_workflow_ids,
            callback_timeout_seconds=callback_timeout_seconds,
            waiting_payload={
                "section_id": section_id,
                "workflow_mode": "validate",
                "scan_workflow_id": pipeline_result.scan_workflow_id,
                "validate_workflow_ids": pipeline_result.validate_workflow_ids,
            },
        )
        return self._build_result(stage=phase_result["stage"], missing_workflow_ids=phase_result["missing_workflow_ids"])

    async def _run_draft_fill_mode(
        self,
        run_id: str,
        section_id: str,
        portal_url: str,
        callback_timeout_seconds: int,
        browser_session_id: str | None = None,
    ) -> dict[str, object]:
        fill_result = await workflow.execute_activity(
            "execute_draft_fill_pipeline_activity",
            ExecuteDraftFillPipelineInput(run_id=run_id, section_id=section_id, portal_url=portal_url, browser_session_id=browser_session_id),
            start_to_close_timeout=timedelta(minutes=5),
        )
        self._state.fill_task_ids = fill_result.fill_task_ids
        fill_phase_result = await self._wait_for_expected_workflows(
            phase="fill_callbacks",
            expected_workflow_ids=fill_result.expected_workflow_ids,
            callback_timeout_seconds=callback_timeout_seconds,
            waiting_payload={
                "section_id": section_id,
                "workflow_mode": "draft_fill",
                "fill_workflow_ids": fill_result.fill_workflow_ids,
                "field_count": fill_result.field_count,
                "submit_enabled": fill_result.submit_enabled,
            },
        )
        if fill_phase_result["timed_out"]:
            return self._build_result(
                stage=fill_phase_result["stage"],
                missing_workflow_ids=fill_phase_result["missing_workflow_ids"],
            )

        post_fill_validate_task_ids, post_fill_validate_workflow_ids = await self._wait_for_stage_tasks(
            run_id=run_id,
            section_id=section_id,
            purpose="validate",
            stage="post_fill_validate",
            timeout_seconds=60,
        )
        if not post_fill_validate_workflow_ids:
            return self._build_result(
                stage="post_fill_validate_dispatch_missing",
                missing_workflow_ids=[],
            )
        self._state.validate_task_ids = post_fill_validate_task_ids
        validate_phase_result = await self._wait_for_expected_workflows(
            phase="post_fill_validate_callbacks",
            expected_workflow_ids=post_fill_validate_workflow_ids,
            callback_timeout_seconds=callback_timeout_seconds,
            waiting_payload={
                "section_id": section_id,
                "workflow_mode": "draft_fill",
                "post_fill_validate_workflow_ids": post_fill_validate_workflow_ids,
            },
        )
        return self._build_result(
            stage=validate_phase_result["stage"],
            missing_workflow_ids=validate_phase_result["missing_workflow_ids"],
        )

    async def _wait_for_expected_workflows(
        self,
        phase: str,
        expected_workflow_ids: list[str],
        callback_timeout_seconds: int,
        waiting_payload: dict[str, object],
    ) -> dict[str, object]:
        if self._state is None:
            raise RuntimeError("Workflow state is not initialized")
        self._state.expected_workflow_ids.extend(
            workflow_id
            for workflow_id in expected_workflow_ids
            if workflow_id not in self._state.expected_workflow_ids
        )
        self._state.stage = f"waiting_for_{phase}"
        await self._record_event(
            "TEMPORAL_WORKFLOW_WAITING_FOR_CALLBACKS",
            {
                **waiting_payload,
                "phase": phase,
                "expected_workflow_ids": expected_workflow_ids,
                "expected_callback_count": len(expected_workflow_ids),
                "callback_timeout_seconds": callback_timeout_seconds,
            },
        )

        timed_out = False
        try:
            await workflow.wait_condition(
                lambda: self._state is not None
                and set(self._state.received_workflow_ids) >= set(expected_workflow_ids),
                timeout=timedelta(seconds=callback_timeout_seconds),
                timeout_summary="Timed out waiting for Skyvern callbacks",
            )
        except TimeoutError:
            timed_out = True

        stage = f"{phase}_timed_out" if timed_out else f"{phase}_received"
        self._state.stage = stage
        missing_workflow_ids = [workflow_id for workflow_id in expected_workflow_ids if workflow_id not in self._state.received_workflow_ids]
        await self._record_event(
            "TEMPORAL_WORKFLOW_CALLBACKS_TIMED_OUT" if timed_out else "TEMPORAL_WORKFLOW_CALLBACKS_RECEIVED",
            {
                **waiting_payload,
                "phase": phase,
                "expected_workflow_ids": expected_workflow_ids,
                "received_workflow_ids": self._state.received_workflow_ids,
                "missing_workflow_ids": missing_workflow_ids,
                "duplicate_callback_count": self._state.duplicate_callback_count,
                "unexpected_callback_count": self._state.unexpected_callback_count,
            },
        )
        return {"timed_out": timed_out, "stage": stage, "missing_workflow_ids": missing_workflow_ids}

    async def _wait_for_stage_tasks(
        self,
        run_id: str,
        section_id: str,
        purpose: str,
        stage: str,
        timeout_seconds: int,
    ) -> tuple[list[str], list[str]]:
        task_ids: list[str] = []
        workflow_ids: list[str] = []
        deadline = workflow.now() + timedelta(seconds=timeout_seconds)
        while workflow.now() < deadline:
            result = await workflow.execute_activity(
                "list_stage_workflow_ids_activity",
                ListStageWorkflowIdsInput(run_id=run_id, section_id=section_id, purpose=purpose, stage=stage),
                start_to_close_timeout=timedelta(seconds=30),
            )
            task_ids = result.task_ids
            workflow_ids = result.workflow_ids
            if workflow_ids:
                return task_ids, workflow_ids
            await workflow.sleep(timedelta(seconds=2))
        await self._record_event(
            "TEMPORAL_WORKFLOW_STAGE_TASKS_NOT_FOUND",
            {"section_id": section_id, "purpose": purpose, "stage": stage, "timeout_seconds": timeout_seconds},
        )
        return task_ids, workflow_ids

    def _build_result(self, stage: str, missing_workflow_ids: list[str]) -> dict[str, object]:
        if self._state is None:
            raise RuntimeError("Workflow state is not initialized")
        return {
            "run_id": self._state.run_id,
            "section_id": self._state.section_id,
            "workflow_mode": self._state.workflow_mode,
            "stage": stage,
            "scan_task_id": self._state.scan_task_id,
            "fill_task_ids": self._state.fill_task_ids,
            "validate_task_ids": self._state.validate_task_ids,
            "expected_workflow_ids": self._state.expected_workflow_ids,
            "received_workflow_ids": self._state.received_workflow_ids,
            "missing_workflow_ids": missing_workflow_ids,
            "received_callbacks": len(self._state.received_workflow_ids),
            "duplicate_callback_count": self._state.duplicate_callback_count,
            "unexpected_callback_count": self._state.unexpected_callback_count,
        }

    @workflow.signal
    def skyvern_webhook_received(self, payload: dict[str, object]) -> None:
        if self._state is None:
            return
        self._state.webhook_events.append(payload)
        workflow_id = str(payload.get("workflow_id") or payload.get("id") or "").strip()
        if not workflow_id:
            self._state.unexpected_callback_count += 1
            return
        if workflow_id not in self._state.expected_workflow_ids:
            self._state.unexpected_callback_count += 1
            return
        if workflow_id in self._state.received_workflow_ids:
            self._state.duplicate_callback_count += 1
            return
        self._state.received_workflow_ids.append(workflow_id)

    async def _record_event(self, event_type: str, payload: dict[str, object]) -> None:
        if self._state is None:
            return
        await workflow.execute_activity(
            "record_run_event_activity",
            RecordRunEventInput(
                run_id=self._state.run_id,
                event_type=event_type,
                payload=payload,
            ),
            start_to_close_timeout=timedelta(seconds=30),
        )
