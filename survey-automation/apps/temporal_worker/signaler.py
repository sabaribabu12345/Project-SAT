from __future__ import annotations

from temporalio.client import Client


class TemporalSignaler:
    def __init__(self, target_host: str, namespace: str) -> None:
        self._target_host = target_host
        self._namespace = namespace

    async def signal_skyvern_webhook(self, run_id: str, payload: dict[str, object]) -> None:
        client = await Client.connect(self._target_host, namespace=self._namespace)
        handle = client.get_workflow_handle(run_id)
        await handle.signal("skyvern_webhook_received", payload)

    async def start_run_workflow(
        self,
        run_id: str,
        section_id: str,
        portal_url: str = "http://fake-form",
        callback_timeout_seconds: int = 1800,
        workflow_mode: str = "validate",
        browser_session_id: str | None = None,
    ) -> None:
        client = await Client.connect(self._target_host, namespace=self._namespace)
        await client.start_workflow(
            "RunWorkflow.run",
            args=[run_id, section_id, portal_url, callback_timeout_seconds, workflow_mode, browser_session_id],
            id=run_id,
            task_queue="survey-automation-task-queue",
        )
