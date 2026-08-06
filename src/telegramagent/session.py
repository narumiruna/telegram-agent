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
        messages: list[ModelMessage] = []
        for record in self.records(chat_id):
            if record.type == "compaction":
                messages = list(record.messages)
            else:
                messages.extend(record.messages)
        return messages

    def history(self, chat_id: int, *, limit: int = 20) -> list[tuple[str, str]]:
        turns: list[tuple[str, str]] = []
        for message in self.model_history(chat_id):
            projected = _project_message(message)
            if projected is not None:
                turns.append(projected)
        return turns[-limit:]

    def clear_chat(self, chat_id: int) -> None:
        self._path(chat_id).unlink(missing_ok=True)

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
