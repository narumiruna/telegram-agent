from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from typing import cast

SessionRecordType = Literal[
    "user", "assistant", "synthetic", "action-start", "action-result", "action-error", "edit", "delete"
]
SessionRole = Literal["user", "assistant", "system"]


@dataclass(frozen=True)
class SessionRecord:
    id: str
    chat_id: int
    type: SessionRecordType
    created_at: float
    text: str = ""
    role: SessionRole | None = None
    target_id: str | None = None
    message_id: int | None = None
    metadata: dict[str, object] | None = None


class SessionLog:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        chat_id: int,
        record_type: SessionRecordType,
        *,
        text: str = "",
        role: SessionRole | None = None,
        target_id: str | None = None,
        message_id: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> SessionRecord:
        record = SessionRecord(
            id=f"rec_{uuid.uuid4().hex}",
            chat_id=chat_id,
            type=record_type,
            created_at=time.time(),
            text=text,
            role=role,
            target_id=target_id,
            message_id=message_id,
            metadata=metadata,
        )
        path = self._path(chat_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":")) + "\n")
        return record

    def append_turn(self, chat_id: int, *, user_text: str, assistant_text: str, synthetic: bool = False) -> None:
        self.append(chat_id, "synthetic" if synthetic else "user", text=user_text, role="user")
        self.append(chat_id, "assistant", text=assistant_text, role="assistant")

    def append_edit(self, chat_id: int, *, target_id: str, text: str) -> SessionRecord:
        return self.append(chat_id, "edit", text=text, target_id=target_id)

    def append_delete(self, chat_id: int, *, target_id: str) -> SessionRecord:
        return self.append(chat_id, "delete", target_id=target_id)

    def records(self, chat_id: int) -> list[SessionRecord]:
        path = self._path(chat_id)
        if not path.exists():
            return []
        records: list[SessionRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if not isinstance(data, dict):
                continue
            records.append(
                SessionRecord(
                    id=str(data["id"]),
                    chat_id=int(data["chat_id"]),
                    type=data["type"],
                    created_at=float(data["created_at"]),
                    text=str(data.get("text") or ""),
                    role=data.get("role"),
                    target_id=data.get("target_id"),
                    message_id=data.get("message_id"),
                    metadata=data.get("metadata"),
                )
            )
        return records

    def replay(self, chat_id: int) -> list[SessionRecord]:
        active: dict[str, SessionRecord] = {}
        order: list[str] = []
        for record in self.records(chat_id):
            if record.type == "edit" and record.target_id in active:
                original = active[record.target_id]
                active[record.target_id] = SessionRecord(
                    id=original.id,
                    chat_id=original.chat_id,
                    type=original.type,
                    created_at=original.created_at,
                    text=record.text,
                    role=original.role,
                    target_id=original.target_id,
                    message_id=original.message_id,
                    metadata=original.metadata,
                )
            elif record.type == "delete" and record.target_id in active:
                active.pop(record.target_id, None)
                order = [item for item in order if item != record.target_id]
            elif record.type in {"user", "assistant", "synthetic", "action-start", "action-result", "action-error"}:
                active[record.id] = record
                order.append(record.id)
        return [active[record_id] for record_id in order if record_id in active]

    def history(self, chat_id: int, *, limit: int = 20) -> list[tuple[str, str]]:
        turns = [
            (cast(str, record.role), record.text)
            for record in self.replay(chat_id)
            if record.role in {"user", "assistant"} and record.text
        ]
        return turns[-limit:]

    def clear_chat(self, chat_id: int) -> None:
        self._path(chat_id).unlink(missing_ok=True)

    def _path(self, chat_id: int) -> Path:
        return self.root / str(chat_id) / "log.jsonl"
