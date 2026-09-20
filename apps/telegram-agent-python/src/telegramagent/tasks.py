from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

TaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
TaskPriority = Literal["now", "next", "later"]
TaskAction = Callable[["TaskRecord"], Awaitable[str]]


@dataclass
class TaskRecord:
    id: str
    chat_id: int
    description: str
    priority: TaskPriority
    status: TaskStatus = "pending"
    created_at: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None
    status_message_id: int | None = None
    output: str = ""
    error: str = ""


@dataclass
class _TaskItem:
    record: TaskRecord
    sequence: int
    action: TaskAction


class TaskQueue:
    def __init__(self, *, max_concurrent_per_chat: int = 1) -> None:
        self.max_concurrent_per_chat = max_concurrent_per_chat
        self.records: dict[str, TaskRecord] = {}
        self._pending: list[_TaskItem] = []
        self._running_by_chat: Counter[int] = Counter()
        self._condition = asyncio.Condition()
        self._sequence = 0

    async def run(
        self,
        *,
        chat_id: int,
        description: str,
        action: TaskAction,
        priority: TaskPriority = "next",
        status_message_id: int | None = None,
    ) -> TaskRecord:
        record = TaskRecord(
            id=f"task_{uuid.uuid4().hex[:10]}",
            chat_id=chat_id,
            description=description,
            priority=priority,
            created_at=time.time(),
            status_message_id=status_message_id,
        )
        async with self._condition:
            self.records[record.id] = record
            item = _TaskItem(record=record, sequence=self._sequence, action=action)
            self._sequence += 1
            self._pending.append(item)
            self._condition.notify_all()
            while not self._can_start(item):
                await self._condition.wait()
            if item in self._pending:
                self._pending.remove(item)
            if record.status == "cancelled":
                record.completed_at = time.time()
                return record
            record.status = "running"
            record.started_at = time.time()
            self._running_by_chat[chat_id] += 1

        try:
            record.output = await action(record)
            if record.status != "cancelled":
                record.status = "completed"
        except asyncio.CancelledError:
            record.status = "cancelled"
            record.error = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - queue records failures for callers and management commands
            record.status = "failed"
            record.error = str(exc) or type(exc).__name__
        finally:
            record.completed_at = time.time()
            async with self._condition:
                self._running_by_chat[chat_id] -= 1
                if self._running_by_chat[chat_id] <= 0:
                    del self._running_by_chat[chat_id]
                self._condition.notify_all()
        return record

    def cancel(self, task_id: str) -> bool:
        record = self.records.get(task_id)
        if record is None or record.status in {"completed", "failed", "cancelled"}:
            return False
        record.status = "cancelled"
        for item in [item for item in self._pending if item.record.id == task_id]:
            self._pending.remove(item)
        return True

    def list_records(self, *, chat_id: int | None = None) -> list[TaskRecord]:
        records = list(self.records.values())
        if chat_id is not None:
            records = [record for record in records if record.chat_id == chat_id]
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def get(self, task_id: str) -> TaskRecord | None:
        return self.records.get(task_id)

    def _can_start(self, item: _TaskItem) -> bool:
        if item.record.status == "cancelled":
            return True
        if self._running_by_chat[item.record.chat_id] >= self.max_concurrent_per_chat:
            return False
        eligible = [candidate for candidate in self._pending if candidate.record.status != "cancelled"]
        if not eligible:
            return True
        return item is min(
            eligible, key=lambda candidate: (_priority_rank(candidate.record.priority), candidate.sequence)
        )


class TaskManagementTool:
    def __init__(
        self,
        *,
        queue: TaskQueue,
        admins: set[int] | None = None,
        fallback_admins: set[int] | None = None,
    ) -> None:
        self.queue = queue
        self.admins = admins or set()
        self.fallback_admins = fallback_admins or set()

    async def handle(self, text: str, *, chat_id: int, user_id: int | None) -> str | None:
        command, _, args = text.strip().partition(" ")
        command_name = command.split("@", maxsplit=1)[0].lower()
        if command_name != "/tasks":
            return None
        if not self._is_admin(chat_id=chat_id, user_id=user_id):
            return "你沒有權限管理 tasks。"

        action, _, rest = args.strip().partition(" ")
        match action.lower() or "list":
            case "list":
                return self._list(chat_id=chat_id)
            case "show":
                if not rest.strip():
                    return "用法: /tasks show <id>"
                return self._show(rest.strip())
            case "cancel":
                if not rest.strip():
                    return "用法: /tasks cancel <id>"
                return self._cancel(rest.strip())
            case _:
                return "用法:\n/tasks list\n/tasks show <id>\n/tasks cancel <id>"

    def _is_admin(self, *, chat_id: int, user_id: int | None) -> bool:
        admin_ids = self.admins or self.fallback_admins
        if not admin_ids:
            return True
        return chat_id in admin_ids or (user_id is not None and user_id in admin_ids)

    def _list(self, *, chat_id: int) -> str:
        records = self.queue.list_records(chat_id=chat_id)
        if not records:
            return "目前沒有 task。"
        return "Tasks:\n" + "\n".join(
            f"- {record.id} [{record.status}] {record.description}" for record in records[:20]
        )

    def _show(self, task_id: str) -> str:
        record = self.queue.get(task_id)
        if record is None:
            return f"找不到 task: {task_id}"
        details = [
            f"id: {record.id}",
            f"chat_id: {record.chat_id}",
            f"status: {record.status}",
            f"priority: {record.priority}",
            f"description: {record.description}",
        ]
        if record.output:
            details.append(f"output: {record.output[:1000]}")
        if record.error:
            details.append(f"error: {record.error}")
        return "\n".join(details)

    def _cancel(self, task_id: str) -> str:
        if not self.queue.cancel(task_id):
            return f"無法取消 task: {task_id}"
        return f"已取消 task: {task_id}"


def _priority_rank(priority: TaskPriority) -> int:
    return {"now": 0, "next": 1, "later": 2}[priority]
