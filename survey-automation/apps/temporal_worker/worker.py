from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from apps.api.settings import get_settings
from apps.temporal_worker.activities import (
    execute_draft_fill_pipeline_activity,
    execute_section_pipeline_activity,
    list_stage_workflow_ids_activity,
    record_run_event_activity,
)
from apps.temporal_worker.workflows import RunWorkflow


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_target_host, namespace=settings.temporal_namespace)
    with ThreadPoolExecutor(max_workers=8) as activity_executor:
        worker = Worker(
            client,
            task_queue="survey-automation-task-queue",
            workflows=[RunWorkflow],
            activities=[
                execute_section_pipeline_activity,
                execute_draft_fill_pipeline_activity,
                list_stage_workflow_ids_activity,
                record_run_event_activity,
            ],
            activity_executor=activity_executor,
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
