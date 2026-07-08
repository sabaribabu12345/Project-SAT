from __future__ import annotations

import asyncio

import pytest

from apps.temporal_worker import signaler as signaler_module
from apps.temporal_worker.signaler import TemporalSignaler
from apps.temporal_worker.workflows import RunWorkflow, RunWorkflowState


class FakeTemporalClient:
    def __init__(self) -> None:
        self.started: dict[str, object] | None = None

    async def start_workflow(self, workflow: str, **kwargs: object) -> None:
        self.started = {"workflow": workflow, **kwargs}


def test_start_run_workflow_uses_temporal_args_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = FakeTemporalClient()

    async def fake_connect(target_host: str, namespace: str) -> FakeTemporalClient:
        assert target_host == "temporal:7233"
        assert namespace == "default"
        return fake_client

    monkeypatch.setattr(signaler_module.Client, "connect", fake_connect)

    signaler = TemporalSignaler(target_host="temporal:7233", namespace="default")
    asyncio.run(
        signaler.start_run_workflow(
            run_id="run_test",
            section_id="institution",
            portal_url="http://fake-form",
            callback_timeout_seconds=60,
            workflow_mode="draft_fill",
        )
    )

    assert fake_client.started == {
        "workflow": "RunWorkflow.run",
        "args": ["run_test", "institution", "http://fake-form", 60, "draft_fill", None],
        "id": "run_test",
        "task_queue": "survey-automation-task-queue",
    }


def test_run_workflow_signal_tracks_expected_callbacks_exactly() -> None:
    workflow = RunWorkflow()
    workflow._state = RunWorkflowState(  # noqa: SLF001
        run_id="run_test",
        section_id="institution",
        portal_url="http://fake-form",
        expected_workflow_ids=["wf_scan", "wf_validate"],
    )

    workflow.skyvern_webhook_received({"workflow_id": "wf_scan"})
    workflow.skyvern_webhook_received({"workflow_id": "wf_scan"})
    workflow.skyvern_webhook_received({"workflow_id": "wf_other"})
    workflow.skyvern_webhook_received({"id": "wf_validate"})

    assert workflow._state.received_workflow_ids == ["wf_scan", "wf_validate"]  # noqa: SLF001
    assert workflow._state.duplicate_callback_count == 1  # noqa: SLF001
    assert workflow._state.unexpected_callback_count == 1  # noqa: SLF001
