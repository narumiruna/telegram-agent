from __future__ import annotations

import json
from pathlib import Path

from telegramagent.session import SessionLog


def test_session_log_reconstructs_last_n_context(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    for index in range(3):
        log.append_turn(123, user_text=f"u{index}", assistant_text=f"a{index}")

    assert log.history(123, limit=4) == [("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2")]


def test_session_log_reads_records_written_before_id_removal(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    path = log.root / "123" / "log.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "id": "rec_legacy",
                "chat_id": 123,
                "type": "user",
                "created_at": 1.0,
                "text": "legacy",
                "role": "user",
                "target_id": None,
                "message_id": None,
                "metadata": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert log.history(123) == [("user", "legacy")]


def test_session_log_can_clear_chat(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    log.append_turn(123, user_text="hi", assistant_text="ok")

    log.clear_chat(123)

    assert log.records(123) == []
    assert log.history(123) == []
