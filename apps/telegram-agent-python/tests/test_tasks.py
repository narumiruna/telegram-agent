from __future__ import annotations

import asyncio

import pytest

from telegramagent.tasks import TaskManagementTool
from telegramagent.tasks import TaskQueue
from telegramagent.tasks import TaskRecord


@pytest.mark.asyncio
async def test_task_queue_records_success_and_failure() -> None:
    queue = TaskQueue(max_concurrent_per_chat=1)

    async def ok(record: TaskRecord) -> str:
        return f"done {record.id}"

    async def fail(record: TaskRecord) -> str:
        raise RuntimeError("boom")

    success = await queue.run(chat_id=123, description="ok", action=ok)
    failure = await queue.run(chat_id=123, description="fail", action=fail)

    assert success.status == "completed"
    assert success.output.startswith("done task_")
    assert failure.status == "failed"
    assert failure.error == "boom"


@pytest.mark.asyncio
async def test_task_queue_respects_priority_order_and_chat_limit() -> None:
    queue = TaskQueue(max_concurrent_per_chat=1)
    started: list[str] = []
    release = asyncio.Event()

    async def first(record: TaskRecord) -> str:
        started.append(record.description)
        await release.wait()
        return record.description

    async def later(record: TaskRecord) -> str:
        started.append(record.description)
        return record.description

    first_task = asyncio.create_task(queue.run(chat_id=123, description="first", action=first, priority="next"))
    await asyncio.sleep(0)
    low_task = asyncio.create_task(queue.run(chat_id=123, description="low", action=later, priority="later"))
    high_task = asyncio.create_task(queue.run(chat_id=123, description="high", action=later, priority="now"))
    await asyncio.sleep(0)

    release.set()
    await asyncio.gather(first_task, low_task, high_task)

    assert started == ["first", "high", "low"]


@pytest.mark.asyncio
async def test_task_queue_can_cancel_pending_task() -> None:
    queue = TaskQueue(max_concurrent_per_chat=1)
    release = asyncio.Event()

    async def wait(record: TaskRecord) -> str:
        await release.wait()
        return record.description

    running = asyncio.create_task(queue.run(chat_id=123, description="running", action=wait))
    await asyncio.sleep(0)
    pending = asyncio.create_task(queue.run(chat_id=123, description="pending", action=wait))
    await asyncio.sleep(0)
    pending_record = next(record for record in queue.list_records(chat_id=123) if record.description == "pending")

    assert queue.cancel(pending_record.id) is True
    release.set()
    done_running, done_pending = await asyncio.gather(running, pending)

    assert done_running.status == "completed"
    assert done_pending.status == "cancelled"


@pytest.mark.asyncio
async def test_task_management_tool_is_admin_gated() -> None:
    queue = TaskQueue()

    async def ok(record: TaskRecord) -> str:
        return "ok"

    record = await queue.run(chat_id=123, description="job", action=ok)
    tool = TaskManagementTool(queue=queue, admins={999})

    assert await tool.handle("/tasks list", chat_id=123, user_id=111) == "你沒有權限管理 tasks。"
    listed = await tool.handle("/tasks list", chat_id=123, user_id=999)
    assert listed is not None
    assert record.id in listed
    shown = await tool.handle(f"/tasks show {record.id}", chat_id=123, user_id=999)
    assert shown is not None
    assert "status: completed" in shown
