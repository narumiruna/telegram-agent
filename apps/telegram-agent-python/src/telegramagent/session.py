from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic_ai.messages import BinaryContent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import UserPromptPart

SessionRecordType = Literal["messages", "compaction"]
LegacyRecordType = Literal["user", "assistant", "synthetic"]
SessionRole = Literal["user", "assistant"]


@dataclass(frozen=True)
class SessionRecord:
    chat_id: int
    type: SessionRecordType
    created_at: float
    messages: tuple[ModelMessage, ...]
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class TelegramThreadRecord:
    chat_id: int
    message_id: int
    parent_message_id: int | None
    created_at: float
    messages: tuple[ModelMessage, ...]
    resets_history: bool = False
    session_record_count: int | None = None


class SessionLog:
    """Append-only v2 store for exact Pydantic AI messages.

    The v2 store intentionally uses a new filename and does not read the legacy
    ``log.jsonl`` format. Operators must clear old sessions during deployment.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()

    def append_messages(
        self,
        chat_id: int,
        messages: Sequence[ModelMessage],
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> SessionRecord:
        return self._append_record(chat_id, "messages", messages, metadata=metadata)

    def append_compaction(
        self,
        chat_id: int,
        summary_messages: Sequence[ModelMessage],
        *,
        source_message_count: int,
    ) -> SessionRecord:
        return self._append_record(
            chat_id,
            "compaction",
            summary_messages,
            metadata={"source_message_count": source_message_count},
        )

    def append(
        self,
        chat_id: int,
        record_type: LegacyRecordType,
        *,
        text: str = "",
        role: SessionRole | None = None,
        message_id: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> SessionRecord:
        """Write a single projected Telegram message in the v2 format.

        This compatibility surface is retained for passive group context while
        callers migrate away from the former text-record schema.
        """
        effective_role: SessionRole = role or ("assistant" if record_type == "assistant" else "user")
        message: ModelMessage
        if effective_role == "assistant":
            message = ModelResponse(parts=[TextPart(content=text)])
        else:
            message = ModelRequest(parts=[UserPromptPart(content=text)])
        record_metadata = dict(metadata or {})
        record_metadata["source_type"] = record_type
        if message_id is not None:
            record_metadata["telegram_message_id"] = message_id
        return self.append_messages(chat_id, [message], metadata=record_metadata or None)

    def append_turn(self, chat_id: int, *, user_text: str, assistant_text: str, synthetic: bool = False) -> None:
        self.append_messages(
            chat_id,
            [
                ModelRequest(parts=[UserPromptPart(content=user_text)]),
                ModelResponse(parts=[TextPart(content=assistant_text)]),
            ],
            metadata={"synthetic": synthetic} if synthetic else None,
        )

    def records(self, chat_id: int) -> list[SessionRecord]:
        path = self._path(chat_id)
        if not path.exists():
            return []
        records: list[SessionRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if not isinstance(data, dict) or data.get("version") != 2:
                continue
            raw_messages = data.get("messages")
            messages = ModelMessagesTypeAdapter.validate_python(raw_messages)
            raw_metadata = data.get("metadata")
            metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else None
            records.append(
                SessionRecord(
                    chat_id=int(data["chat_id"]),
                    type=data["type"],
                    created_at=float(data["created_at"]),
                    messages=tuple(messages),
                    metadata=metadata,
                )
            )
        return records

    def model_history(self, chat_id: int) -> list[ModelMessage]:
        session_records = self.records(chat_id)
        thread_records = self.telegram_thread_records(chat_id)
        if thread_records:
            head = thread_records[-1]
            messages = list(self.telegram_thread_history(chat_id, head.message_id) or ())
            if head.session_record_count is not None:
                session_records = session_records[head.session_record_count :]
            else:
                session_records = [record for record in session_records if record.created_at > head.created_at]
        else:
            messages = []
        for record in session_records:
            if record.type == "compaction":
                messages = list(record.messages)
            else:
                messages.extend(record.messages)
        return messages

    def history(self, chat_id: int, *, limit: int = 20) -> list[tuple[str, str]]:
        return project_history(self.model_history(chat_id), limit=limit)

    def append_telegram_thread(
        self,
        chat_id: int,
        message_id: int,
        messages: Sequence[ModelMessage],
        *,
        parent_message_id: int | None,
    ) -> TelegramThreadRecord:
        history = tuple(messages)
        parent_history = (
            self.telegram_thread_history(chat_id, parent_message_id) if parent_message_id is not None else None
        )
        extends_parent = parent_history is not None and history[: len(parent_history)] == parent_history
        stored_messages = history[len(parent_history) :] if extends_parent and parent_history is not None else history
        record = TelegramThreadRecord(
            chat_id=chat_id,
            message_id=message_id,
            parent_message_id=parent_message_id,
            created_at=time.time(),
            messages=tuple(stored_messages),
            resets_history=parent_message_id is not None and not extends_parent,
            session_record_count=len(self.records(chat_id)),
        )
        payload = {
            "version": 1,
            "chat_id": chat_id,
            "message_id": message_id,
            "parent_message_id": parent_message_id,
            "created_at": record.created_at,
            "messages": ModelMessagesTypeAdapter.dump_python(list(stored_messages), mode="json"),
            "resets_history": record.resets_history,
            "session_record_count": record.session_record_count,
        }
        path = self._telegram_thread_path(chat_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock, path.open("a", encoding="utf-8") as file:
            file.write(encoded)
        return record

    def telegram_thread_records(self, chat_id: int) -> list[TelegramThreadRecord]:
        path = self._telegram_thread_path(chat_id)
        if not path.exists():
            return []
        records: list[TelegramThreadRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if not isinstance(data, dict) or data.get("version") != 1:
                continue
            raw_parent = data.get("parent_message_id")
            raw_messages = data.get("messages")
            raw_session_record_count = data.get("session_record_count")
            records.append(
                TelegramThreadRecord(
                    chat_id=int(data["chat_id"]),
                    message_id=int(data["message_id"]),
                    parent_message_id=int(raw_parent) if raw_parent is not None else None,
                    created_at=float(data["created_at"]),
                    messages=tuple(ModelMessagesTypeAdapter.validate_python(raw_messages)),
                    resets_history=bool(data.get("resets_history", False)),
                    session_record_count=(
                        int(raw_session_record_count) if raw_session_record_count is not None else None
                    ),
                )
            )
        return records

    def telegram_thread_history(self, chat_id: int, message_id: int) -> tuple[ModelMessage, ...] | None:
        records_by_message_id = {record.message_id: record for record in self.telegram_thread_records(chat_id)}
        if message_id not in records_by_message_id:
            return None
        cache: dict[int, tuple[ModelMessage, ...]] = {}

        def build(current_message_id: int, ancestors: frozenset[int]) -> tuple[ModelMessage, ...]:
            if current_message_id in cache:
                return cache[current_message_id]
            if current_message_id in ancestors:
                return ()
            record = records_by_message_id[current_message_id]
            parent_history: tuple[ModelMessage, ...] = ()
            if (
                not record.resets_history
                and record.parent_message_id is not None
                and record.parent_message_id in records_by_message_id
            ):
                parent_history = build(record.parent_message_id, ancestors | {current_message_id})
            history = (*parent_history, *record.messages)
            cache[current_message_id] = history
            return history

        return build(message_id, frozenset())

    def telegram_thread_head_message_id(self, chat_id: int) -> int | None:
        records = self.telegram_thread_records(chat_id)
        return records[-1].message_id if records else None

    def telegram_thread_head_history(self, chat_id: int) -> tuple[ModelMessage, ...] | None:
        message_id = self.telegram_thread_head_message_id(chat_id)
        if message_id is None:
            return None
        return self.telegram_thread_history(chat_id, message_id)

    def clear_chat(self, chat_id: int) -> None:
        self._path(chat_id).unlink(missing_ok=True)
        self._telegram_thread_path(chat_id).unlink(missing_ok=True)

    def _append_record(
        self,
        chat_id: int,
        record_type: SessionRecordType,
        messages: Sequence[ModelMessage],
        *,
        metadata: Mapping[str, object] | None,
    ) -> SessionRecord:
        record = SessionRecord(
            chat_id=chat_id,
            type=record_type,
            created_at=time.time(),
            messages=tuple(messages),
            metadata=dict(metadata) if metadata else None,
        )
        payload = {
            "version": 2,
            "chat_id": chat_id,
            "type": record_type,
            "created_at": record.created_at,
            "messages": ModelMessagesTypeAdapter.dump_python(list(messages), mode="json"),
            "metadata": record.metadata,
        }
        path = self._path(chat_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock, path.open("a", encoding="utf-8") as file:
            file.write(encoded)
        return record

    def _path(self, chat_id: int) -> Path:
        return self.root / str(chat_id) / "session-v2.jsonl"

    def _telegram_thread_path(self, chat_id: int) -> Path:
        return self.root / str(chat_id) / "telegram-threads-v1.jsonl"


def project_history(messages: Sequence[ModelMessage], *, limit: int = 20) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for message in messages:
        projected = _project_message(message)
        if projected is not None:
            turns.append(projected)
    return turns[-limit:]


def _project_message(message: ModelMessage) -> tuple[str, str] | None:
    if isinstance(message, ModelRequest):
        text = "\n".join(
            item
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for item in _project_user_content(part.content)
            if item
        )
        return ("user", text) if text else None
    if isinstance(message, ModelResponse):
        text = "\n".join(part.content for part in message.parts if isinstance(part, TextPart) and part.content)
        return ("assistant", text) if text else None
    return None


def _project_user_content(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    projected: list[str] = []
    for item in content:
        if isinstance(item, str):
            projected.append(item)
        elif isinstance(item, BinaryContent) and item.is_image:
            projected.append(f"[圖片: {item.identifier or 'image'}]")
    return projected
