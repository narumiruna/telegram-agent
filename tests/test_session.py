from __future__ import annotations

from pathlib import Path

from telegramagent.session import SessionLog


def test_session_log_appends_replays_edits_and_deletes(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")

    user = log.append(123, "user", text="hello", role="user")
    assistant = log.append(123, "assistant", text="old", role="assistant")
    log.append_edit(123, target_id=assistant.id, text="new")
    log.append_delete(123, target_id=user.id)

    replay = log.replay(123)

    assert [(record.type, record.text) for record in replay] == [("assistant", "new")]
    assert log.history(123) == [("assistant", "new")]


def test_session_log_reconstructs_last_n_context(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    for index in range(3):
        log.append_turn(123, user_text=f"u{index}", assistant_text=f"a{index}")

    assert log.history(123, limit=4) == [("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2")]


def test_session_log_can_clear_chat(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    log.append_turn(123, user_text="hi", assistant_text="ok")

    log.clear_chat(123)

    assert log.records(123) == []
    assert log.history(123) == []
