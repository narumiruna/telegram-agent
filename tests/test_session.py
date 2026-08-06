from __future__ import annotations

import json
from pathlib import Path

from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart

from telegramagent.session import SessionLog


def test_session_log_reconstructs_last_n_context(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    for index in range(3):
        log.append_turn(123, user_text=f"u{index}", assistant_text=f"a{index}")

    assert log.history(123, limit=4) == [("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2")]


def test_session_log_round_trips_structured_tool_messages(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    messages = [
        ModelRequest(parts=[UserPromptPart(content="find weather")]),
        ModelResponse(parts=[ToolCallPart(tool_name="weather", args={"city": "Taipei"}, tool_call_id="call-1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="weather", content="sunny", tool_call_id="call-1")]),
        ModelResponse(parts=[TextPart(content="Taipei is sunny")]),
    ]

    log.append_messages(123, messages, metadata={"run_id": "run-1"})

    restored = log.model_history(123)
    assert restored == messages
    assert log.records(123)[0].metadata == {"run_id": "run-1"}
    assert log.history(123) == [("user", "find weather"), ("assistant", "Taipei is sunny")]


def test_compaction_record_replaces_prior_model_context(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    log.append_turn(123, user_text="old question", assistant_text="old answer")
    summary = [
        ModelRequest(parts=[UserPromptPart(content="Conversation summary")]),
        ModelResponse(parts=[TextPart(content="The user previously asked an old question.")]),
    ]

    log.append_compaction(123, summary, source_message_count=2)
    log.append_turn(123, user_text="new question", assistant_text="new answer")

    restored = log.model_history(123)
    assert restored[:2] == summary
    assert log.history(123) == [
        ("user", "Conversation summary"),
        ("assistant", "The user previously asked an old question."),
        ("user", "new question"),
        ("assistant", "new answer"),
    ]
    compaction = log.records(123)[1]
    assert compaction.type == "compaction"
    assert compaction.metadata == {"source_message_count": 2}


def test_v2_session_store_does_not_read_legacy_log(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    legacy_path = log.root / "123" / "log.jsonl"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(json.dumps({"type": "user", "text": "legacy"}) + "\n", encoding="utf-8")

    assert log.records(123) == []
    assert log.history(123) == []


def test_session_log_can_clear_chat(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "sessions")
    log.append_turn(123, user_text="hi", assistant_text="ok")

    log.clear_chat(123)

    assert log.records(123) == []
    assert log.history(123) == []
