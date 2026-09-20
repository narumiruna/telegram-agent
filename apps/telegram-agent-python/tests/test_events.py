from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from telegramagent.events import EventManagementTool
from telegramagent.events import EventSettings
from telegramagent.events import EventWatcher
from telegramagent.events import ImmediateEvent
from telegramagent.events import event_prompt
from telegramagent.events import parse_event


@pytest.mark.parametrize("field", ["at", "schedule", "timezone"])
def test_parse_event_rejects_schedule_fields(field: str) -> None:
    payload = {"type": "immediate", "name": "ok", "chat_id": 1, "text": "hi", field: "nope"}

    with pytest.raises(ValueError, match="schedule fields are not supported"):
        parse_event(json.dumps(payload), max_text_chars=100)


def test_parse_event_validates_name_and_text_length() -> None:
    valid = parse_event(
        json.dumps(
            {
                "type": "immediate",
                "name": "summarize-video",
                "chat_id": 123,
                "text": "hello",
                "reply_mode": "edit-status",
            }
        ),
        max_text_chars=10,
    )

    assert valid.name == "summarize-video"
    assert valid.reply_mode == "edit-status"
    assert event_prompt(valid) == "[EVENT:summarize-video] hello"

    with pytest.raises(ValidationError):
        parse_event(
            json.dumps({"type": "immediate", "name": "Bad_Name", "chat_id": 1, "text": "hi"}), max_text_chars=10
        )
    with pytest.raises(ValueError, match="exceeds max length"):
        parse_event(json.dumps({"type": "immediate", "name": "ok", "chat_id": 1, "text": "too long"}), max_text_chars=3)


@pytest.mark.asyncio
async def test_event_watcher_dispatches_and_archives_processed_file(tmp_path: Path) -> None:
    dispatched: list[ImmediateEvent] = []
    watcher = EventWatcher(
        settings=EventSettings(events_dir=tmp_path / ".events", max_queued_per_chat=5),
        dispatch=lambda event: _record(dispatched, event),
    )
    event_path = watcher.inbox_dir / "summarize-video.json"
    watcher.ensure_dirs()
    event_path.write_text(
        json.dumps({"type": "immediate", "name": "summarize-video", "chat_id": 123, "text": "整理 URL"}),
        encoding="utf-8",
    )

    await watcher.scan_once()

    assert [event.name for event in dispatched] == ["summarize-video"]
    assert not event_path.exists()
    assert (watcher.processed_dir / "summarize-video.json").exists()


@pytest.mark.asyncio
async def test_event_watcher_moves_invalid_file_to_failed(tmp_path: Path) -> None:
    dispatched: list[ImmediateEvent] = []
    watcher = EventWatcher(
        settings=EventSettings(events_dir=tmp_path / ".events", parse_retry_delay_seconds=0),
        dispatch=lambda event: _record(dispatched, event),
    )
    watcher.ensure_dirs()
    event_path = watcher.inbox_dir / "bad.json"
    event_path.write_text("{not json", encoding="utf-8")

    await watcher.scan_once()

    assert dispatched == []
    assert not event_path.exists()
    assert [path.name for path in watcher.failed_dir.glob("*.json")] == ["bad.JSONDecodeError.json"]


@pytest.mark.asyncio
async def test_event_watcher_defers_events_over_per_chat_queue_cap(tmp_path: Path) -> None:
    dispatched: list[ImmediateEvent] = []
    watcher = EventWatcher(
        settings=EventSettings(events_dir=tmp_path / ".events", max_queued_per_chat=1),
        dispatch=lambda event: _record(dispatched, event),
    )
    watcher.ensure_dirs()
    for name in ["first", "second"]:
        (watcher.inbox_dir / f"{name}.json").write_text(
            json.dumps({"type": "immediate", "name": name, "chat_id": 123, "text": name}),
            encoding="utf-8",
        )

    await watcher.scan_once()

    assert [event.name for event in dispatched] == ["first"]
    assert not (watcher.inbox_dir / "first.json").exists()
    assert (watcher.inbox_dir / "second.json").exists()

    await watcher.scan_once()

    assert [event.name for event in dispatched] == ["first", "second"]
    assert not (watcher.inbox_dir / "second.json").exists()


@pytest.mark.asyncio
async def test_event_watcher_deletes_processed_file_when_archive_disabled(tmp_path: Path) -> None:
    dispatched: list[ImmediateEvent] = []
    watcher = EventWatcher(
        settings=EventSettings(events_dir=tmp_path / ".events", archive_processed=False),
        dispatch=lambda event: _record(dispatched, event),
    )
    watcher.ensure_dirs()
    event_path = watcher.inbox_dir / "now.json"
    event_path.write_text(
        json.dumps({"type": "immediate", "name": "now", "chat_id": 123, "text": "hi"}), encoding="utf-8"
    )

    await watcher.scan_once()

    assert [event.name for event in dispatched] == ["now"]
    assert not event_path.exists()
    assert not watcher.processed_dir.exists()


@pytest.mark.asyncio
async def test_event_management_tool_is_admin_gated_and_can_list_show_cancel(tmp_path: Path) -> None:
    watcher = EventWatcher(
        settings=EventSettings(events_dir=tmp_path / ".events"),
        dispatch=lambda event: _record([], event),
    )
    watcher.ensure_dirs()
    (watcher.inbox_dir / "hello.json").write_text(
        json.dumps({"type": "immediate", "name": "hello", "chat_id": 123, "text": "hi"}),
        encoding="utf-8",
    )
    tool = EventManagementTool(watcher=watcher, admins={999})

    assert await tool.handle("/events list", chat_id=123, user_id=111) == "你沒有權限管理 events。"
    assert await tool.handle("/events list", chat_id=123, user_id=999) == "Pending events:\n- hello"
    shown = await tool.handle("/events show hello", chat_id=123, user_id=999)
    assert shown is not None
    assert '"name": "hello"' in shown
    assert await tool.handle("/events cancel hello", chat_id=123, user_id=999) == "已取消 event: hello"
    assert not (watcher.inbox_dir / "hello.json").exists()
    assert [path.name for path in watcher.failed_dir.glob("*.json")] == ["hello.cancelled.json"]


async def _record(events: list[ImmediateEvent], event: ImmediateEvent) -> None:
    events.append(event)
