from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.models.function import DeltaToolCall
from pydantic_ai.models.function import FunctionModel

from telegramagent.llm import ChatAgent
from telegramagent.otter_tools import OtterCliConfig
from telegramagent.otter_tools import build_otter_tools
from telegramagent.skills import format_skills_for_instructions
from telegramagent.skills import load_agent_skills
from telegramagent.telegram import TelegramBot
from tests.telegram_test_support import FakeTelegram


def test_vendored_otter_skill_is_pinned_and_loadable() -> None:
    skill_dir = Path(".agents/skills/otter-manage-expenses")
    skills = load_agent_skills(Path(".agents/skills"), enabled_names={"otter-manage-expenses"})

    assert len(skills) == 1
    assert skills[0].name == "otter-manage-expenses"
    assert skills[0].path == skill_dir / "SKILL.md"
    assert (skill_dir / "references" / "cli.md").is_file()
    assert (skill_dir / "references" / "installation.md").is_file()
    upstream = (skill_dir / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "7f1af003c331f1f263f94385f9c2d4cdc2142f58" in upstream


def test_otter_skill_preserves_safe_id_resolution_and_mutation_workflow() -> None:
    [skill] = load_agent_skills(Path(".agents/skills"), enabled_names={"otter-manage-expenses"})
    instructions = format_skills_for_instructions([skill])

    assert "use returned IDs instead of guessing IDs from names" in instructions
    assert "only when exactly one returned record has that name" in instructions
    assert "pass every intended participant ID" in instructions
    assert "Run the mutation once" in instructions
    assert "verify the changed resource with a narrow read command" in instructions
    assert "run `balances get`" in instructions
    assert "do not retry authentication, permission, conflict, or connection failures" in instructions
    assert "Do not bypass an unsupported operation with direct SQL or an undocumented API call" in instructions


@pytest.mark.asyncio
async def test_telegram_agent_can_follow_expense_mutation_verification_and_balance_workflow(tmp_path: Path) -> None:
    capture_path = tmp_path / "calls.jsonl"
    executable = tmp_path / "otter"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"capture = open({str(capture_path)!r}, 'a', encoding='utf-8')\n"
        "capture.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "capture.close()\n"
        "print(json.dumps({'ok': True}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    tools = build_otter_tools(OtterCliConfig(command=str(executable)))
    [skill] = load_agent_skills(Path(".agents/skills"), enabled_names={"otter-manage-expenses"})
    model_calls = 0

    async def stream_function(_messages, _info):
        nonlocal model_calls
        calls = [
            ("otter_list_trips", {}, "call-1"),
            ("otter_list_participants", {"trip_id": "trip-1"}, "call-2"),
            (
                "otter_add_expense",
                {
                    "trip_id": "trip-1",
                    "description": "晚餐",
                    "amount": "300",
                    "currency": "TWD",
                    "paid_by_participant_id": "participant-1",
                    "split_with_participant_ids": ["participant-1", "participant-2"],
                },
                "call-3",
            ),
            ("otter_list_expenses", {"trip_id": "trip-1"}, "call-4"),
            ("otter_get_balances", {"trip_id": "trip-1"}, "call-5"),
        ]
        if model_calls < len(calls):
            name, arguments, call_id = calls[model_calls]
            model_calls += 1
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=call_id)}
            return
        yield "已記錄並確認最新餘額。"

    pydantic_agent = PydanticAgent(FunctionModel(stream_function=stream_function), tools=list(tools))
    agent = ChatAgent(
        api_key="key",
        model="model",
        skills=[skill],
        capability_summary="- tool.otter: available",
        agent_factory=lambda _instructions: pydantic_agent,
    )

    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=agent, whitelist={123})
    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 123, "is_bot": False},
                "chat": {"id": 123, "type": "private"},
                "text": "晚餐 300 元，小明付，兩人均分",
            },
        }
    )

    assert telegram.sent == [(123, "已記錄並確認最新餘額。", 10)]
    calls = [json.loads(line) for line in capture_path.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        ["trips", "list"],
        ["participants", "list", "--trip", "trip-1"],
        [
            "expenses",
            "add",
            "--trip",
            "trip-1",
            "--description",
            "晚餐",
            "--amount",
            "300",
            "--currency",
            "TWD",
            "--paid-by",
            "participant-1",
            "--split-with",
            "participant-1,participant-2",
        ],
        ["expenses", "list", "--trip", "trip-1"],
        ["balances", "get", "--trip", "trip-1"],
    ]
