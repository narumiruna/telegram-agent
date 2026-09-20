from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from collections import Counter
from collections.abc import Awaitable
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError
from pydantic import model_validator


class ImmediateEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["immediate"]
    name: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    chat_id: int
    text: str = Field(min_length=1)
    reply_to_message_id: int | None = None
    reply_mode: Literal["send", "edit-status"] = "send"
    created_by: str | None = None
    created_at: str | None = None
    dedupe_key: str | None = None

    @model_validator(mode="after")
    def reject_schedule_fields(self) -> ImmediateEvent:
        # Extra fields are already forbidden; this method documents the intended invariant
        # and gives future maintainers a clear place to keep schedule-related rejection.
        return self


@dataclass(frozen=True)
class EventSettings:
    enabled: bool = False
    events_dir: Path = Path(".events")
    scan_seconds: float = 2.0
    max_queued_per_chat: int = 5
    max_text_chars: int = 4000
    archive_processed: bool = True
    parse_retries: int = 3
    parse_retry_delay_seconds: float = 0.05


@dataclass(frozen=True)
class EventFile:
    path: Path
    event: ImmediateEvent


EventDispatcher = Callable[[ImmediateEvent], Awaitable[None]]


class EventWatcher:
    def __init__(self, *, settings: EventSettings, dispatch: EventDispatcher) -> None:
        self.settings = settings
        self.dispatch = dispatch
        self.inbox_dir = settings.events_dir / "inbox"
        self.processed_dir = settings.events_dir / "processed"
        self.failed_dir = settings.events_dir / "failed"
        self._stopped = asyncio.Event()
        self._seen_dedupe_keys: set[str] = set()

    def ensure_dirs(self) -> None:
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        if self.settings.archive_processed:
            self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    async def run_forever(self) -> None:
        if not self.settings.enabled:
            return
        self.ensure_dirs()
        logger.info("Event watcher started, inbox={}", self.inbox_dir)
        while not self._stopped.is_set():
            await self.scan_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stopped.wait(), timeout=self.settings.scan_seconds)
        logger.info("Event watcher stopped")

    def stop(self) -> None:
        self._stopped.set()

    async def scan_once(self) -> None:
        self.ensure_dirs()
        event_files: list[EventFile] = []
        for path in sorted(self.inbox_dir.glob("*.json")):
            event = await self._parse_event_file(path)
            if event is None:
                continue
            event_files.append(EventFile(path=path, event=event))

        counts: Counter[int] = Counter()
        for event_file in event_files:
            chat_id = event_file.event.chat_id
            if counts[chat_id] >= self.settings.max_queued_per_chat:
                logger.warning(
                    "Deferred event {} for chat_id={} because per-scan queue cap is {}",
                    event_file.path.name,
                    chat_id,
                    self.settings.max_queued_per_chat,
                )
                continue
            counts[chat_id] += 1
            await self._dispatch_file(event_file)

    async def _parse_event_file(self, path: Path) -> ImmediateEvent | None:
        last_error: Exception | None = None
        for attempt in range(self.settings.parse_retries):
            try:
                content = await asyncio.to_thread(path.read_text, encoding="utf-8")
                event = parse_event(content, max_text_chars=self.settings.max_text_chars)
            except (OSError, json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.settings.parse_retries:
                    await asyncio.sleep(self.settings.parse_retry_delay_seconds)
                    continue
                logger.warning("Invalid event file {}; moving to failed: {}", path.name, exc)
                self._move_to_failed(path, reason=type(exc).__name__)
                return None
            else:
                return event
        if last_error is not None:
            self._move_to_failed(path, reason=type(last_error).__name__)
        return None

    async def _dispatch_file(self, event_file: EventFile) -> None:
        event = event_file.event
        dedupe_key = event.dedupe_key
        if dedupe_key and dedupe_key in self._seen_dedupe_keys:
            logger.info("Skipping duplicate event dedupe_key={} file={}", dedupe_key, event_file.path.name)
            self._finish_processed(event_file.path)
            return

        try:
            await self.dispatch(event)
        except Exception as exc:  # noqa: BLE001 - event dispatcher must not crash watcher
            logger.exception("Event dispatch failed for {}", event_file.path.name)
            self._move_to_failed(event_file.path, reason=type(exc).__name__)
            return

        if dedupe_key:
            self._seen_dedupe_keys.add(dedupe_key)
        self._finish_processed(event_file.path)

    def _finish_processed(self, path: Path) -> None:
        if self.settings.archive_processed:
            destination = _unique_destination(self.processed_dir / path.name)
            shutil.move(str(path), destination)
        else:
            path.unlink(missing_ok=True)

    def _move_to_failed(self, path: Path, *, reason: str) -> None:
        if not path.exists():
            return
        destination = _unique_destination(self.failed_dir / f"{path.stem}.{reason}.json")
        shutil.move(str(path), destination)

    def list_inbox_events(self) -> list[Path]:
        self.ensure_dirs()
        return sorted(self.inbox_dir.glob("*.json"))

    def show_inbox_event(self, name: str) -> str | None:
        path = self._event_path(name)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def cancel_inbox_event(self, name: str) -> bool:
        path = self._event_path(name)
        if not path.exists():
            return False
        self._move_to_failed(path, reason="cancelled")
        return True

    def _event_path(self, name: str) -> Path:
        safe_name = name.removesuffix(".json")
        if not _EVENT_NAME_RE.fullmatch(safe_name):
            return self.inbox_dir / "__invalid__.json"
        return self.inbox_dir / f"{safe_name}.json"


class EventManagementTool:
    def __init__(
        self,
        *,
        watcher: EventWatcher,
        admins: set[int] | None = None,
        fallback_admins: set[int] | None = None,
    ) -> None:
        self.watcher = watcher
        self.admins = admins or set()
        self.fallback_admins = fallback_admins or set()

    async def handle(self, text: str, *, chat_id: int, user_id: int | None) -> str | None:
        command, _, args = text.strip().partition(" ")
        command_name = command.split("@", maxsplit=1)[0].lower()
        if command_name != "/events":
            return None
        if not self._is_admin(chat_id=chat_id, user_id=user_id):
            return "你沒有權限管理 events。"

        action, _, rest = args.strip().partition(" ")
        match action.lower() or "list":
            case "list":
                return self._list()
            case "show":
                if not rest.strip():
                    return "用法: /events show <name>"
                return self._show(rest.strip())
            case "cancel":
                if not rest.strip():
                    return "用法: /events cancel <name>"
                return self._cancel(rest.strip())
            case "reload":
                await self.watcher.scan_once()
                return "events 已重新掃描。"
            case _:
                return "用法:\n/events list\n/events show <name>\n/events cancel <name>\n/events reload"

    def _is_admin(self, *, chat_id: int, user_id: int | None) -> bool:
        admin_ids = self.admins or self.fallback_admins
        if not admin_ids:
            return True
        return chat_id in admin_ids or (user_id is not None and user_id in admin_ids)

    def _list(self) -> str:
        paths = self.watcher.list_inbox_events()
        if not paths:
            return "目前沒有 pending event。"
        names = "\n".join(f"- {path.stem}" for path in paths)
        return f"Pending events:\n{names}"

    def _show(self, name: str) -> str:
        content = self.watcher.show_inbox_event(name)
        if content is None:
            return f"找不到 event: {name}"
        return content

    def _cancel(self, name: str) -> str:
        if not self.watcher.cancel_inbox_event(name):
            return f"找不到 event: {name}"
        return f"已取消 event: {name}"


_EVENT_NAME_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_FORBIDDEN_SCHEDULE_FIELDS = {"at", "schedule", "timezone"}


def parse_event(content: str, *, max_text_chars: int) -> ImmediateEvent:
    data = json.loads(content)
    if not isinstance(data, dict):
        raise TypeError("event JSON must be an object")
    forbidden = _FORBIDDEN_SCHEDULE_FIELDS.intersection(data)
    if forbidden:
        fields = ", ".join(sorted(forbidden))
        raise ValueError(f"schedule fields are not supported: {fields}")
    event = ImmediateEvent.model_validate(data)
    if len(event.text) > max_text_chars:
        raise ValueError(f"event text exceeds max length {max_text_chars}")
    return event


def event_prompt(event: ImmediateEvent) -> str:
    return f"[EVENT:{event.name}] {event.text}"


def _unique_destination(path: Path) -> str:
    if not path.exists():
        return str(path)
    suffix = time.strftime("%Y%m%d%H%M%S")
    candidate = path.with_name(f"{path.stem}-{suffix}{path.suffix}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{suffix}-{counter}{path.suffix}")
        counter += 1
    return str(candidate)
