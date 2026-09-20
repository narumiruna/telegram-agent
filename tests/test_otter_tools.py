from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from typing import cast

import pytest

from telegramagent.otter_tools import OtterCliConfig
from telegramagent.otter_tools import OtterCliRuntime
from telegramagent.otter_tools import OtterExpenseTools
from telegramagent.otter_tools import OtterMutationCancelledError
from telegramagent.otter_tools import build_otter_tools


def _fake_otter(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-otter"
    executable.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


@pytest.mark.asyncio
async def test_runtime_passes_argv_and_only_scoped_otter_environment(tmp_path: Path, monkeypatch) -> None:
    executable = _fake_otter(
        tmp_path,
        """
import json
import os
import sys
print(json.dumps({
    "argv": sys.argv[1:],
    "url": os.environ.get("OTTER_URL"),
    "token": os.environ.get("OTTER_TOKEN"),
    "config": os.environ.get("OTTER_CONFIG_PATH"),
    "bot_token": os.environ.get("BOT_TOKEN"),
    "allow_insecure": os.environ.get("OTTER_ALLOW_INSECURE_HTTP"),
}))
""",
    )
    monkeypatch.setenv("BOT_TOKEN", "telegram-secret")
    monkeypatch.setenv("OTTER_ALLOW_INSECURE_HTTP", "1")
    config_path = tmp_path / "credentials.json"
    config = OtterCliConfig(
        command=str(executable),
        base_url="https://otter.example/",
        token="otter-secret",
        config_path=config_path,
    )
    runtime = OtterCliRuntime(config)

    assert "otter-secret" not in repr(config)
    result = await runtime.run(["trips", "get", "--trip", "trip id"])

    assert result["status"] == "success"
    assert result["data"] == {
        "argv": ["trips", "get", "--trip", "trip id"],
        "url": "https://otter.example/",
        "token": "[redacted]",
        "config": str(config_path),
        "bot_token": None,
        "allow_insecure": None,
    }
    assert "authoritative Otter JSON" in result["response_contract"]


@pytest.mark.asyncio
async def test_runtime_does_not_interpret_argument_as_shell_code(tmp_path: Path) -> None:
    marker = tmp_path / "shell-ran"
    executable = _fake_otter(
        tmp_path,
        """
import json
import sys
print(json.dumps({"argv": sys.argv[1:]}))
""",
    )
    runtime = OtterCliRuntime(OtterCliConfig(command=str(executable)))

    result = await runtime.run(["trips", "get", "--trip", f"$(touch {marker})"])

    assert result["data"]["argv"][-1] == f"$(touch {marker})"
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "status", "category"),
    [
        ("AUTH_ERROR", 401, "authentication"),
        ("API_ERROR", 403, "permission"),
        ("API_ERROR", 409, "conflict"),
        ("CONFIG_ERROR", None, "configuration"),
        ("INSECURE_HTTP", None, "configuration"),
        ("CONNECTION_ERROR", None, "connection"),
        ("USAGE", 400, "input"),
        ("API_ERROR", 503, "server"),
    ],
)
async def test_runtime_classifies_cli_json_errors(tmp_path: Path, code: str, status: int | None, category: str) -> None:
    payload: dict[str, Any] = {"code": code, "message": "failed"}
    if status is not None:
        payload["status"] = status
    executable = _fake_otter(
        tmp_path,
        f"import json, sys\nsys.stderr.write(json.dumps({json.dumps({'error': payload})}))\nsys.exit(1)",
    )
    runtime = OtterCliRuntime(OtterCliConfig(command=str(executable)))

    result = await runtime.run(["auth", "status"])

    assert result["status"] == "error"
    assert result["error"]["category"] == category
    assert result["error"]["code"] == code
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_runtime_redacts_secret_from_non_json_error(tmp_path: Path) -> None:
    executable = _fake_otter(
        tmp_path,
        "import sys\nsys.stderr.write('Bearer otter-secret token=otter-secret')\nsys.exit(1)",
    )
    runtime = OtterCliRuntime(OtterCliConfig(command=str(executable), token="otter-secret"))

    result = await runtime.run(["auth", "status"])

    assert result["error"]["message"] == "Bearer [redacted] token=[redacted]"
    assert "otter-secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_runtime_reports_malformed_success_without_claiming_success(tmp_path: Path) -> None:
    executable = _fake_otter(tmp_path, "print('not json')")
    runtime = OtterCliRuntime(OtterCliConfig(command=str(executable)))

    result = await runtime.run(["trips", "list"])

    assert result["status"] == "error"
    assert result["error"]["code"] == "INVALID_JSON"


@pytest.mark.asyncio
async def test_runtime_bounds_output(tmp_path: Path) -> None:
    executable = _fake_otter(tmp_path, "print('x' * 101)")
    runtime = OtterCliRuntime(OtterCliConfig(command=str(executable), max_output_chars=100))

    result = await runtime.run(["trips", "list"])

    assert result["status"] == "error"
    assert result["error"]["code"] == "OUTPUT_TOO_LARGE"


@pytest.mark.asyncio
async def test_runtime_timeout_marks_mutation_outcome_unknown(tmp_path: Path) -> None:
    executable = _fake_otter(tmp_path, "import time\ntime.sleep(2)")
    runtime = OtterCliRuntime(OtterCliConfig(command=str(executable), timeout_seconds=0.01))

    result = await runtime.run(["expenses", "add"], mutation=True)

    assert result["status"] == "error"
    assert result["error"]["category"] == "timeout"
    assert result["outcome_unknown"] is True
    assert "Do not retry" in result["response_contract"]


@pytest.mark.asyncio
async def test_runtime_cancellation_warns_that_mutation_outcome_is_unknown(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    executable = _fake_otter(
        tmp_path,
        f"from pathlib import Path\nimport time\nPath({str(marker)!r}).write_text('started')\ntime.sleep(2)",
    )
    runtime = OtterCliRuntime(OtterCliConfig(command=str(executable)))
    task = asyncio.create_task(runtime.run(["expenses", "add"], mutation=True))
    for _ in range(100):
        if marker.exists():
            break
        await asyncio.sleep(0.01)
    assert marker.exists()

    task.cancel()

    with pytest.raises(OtterMutationCancelledError) as raised:
        await task
    assert "結果不明" in raised.value.user_message
    assert "不要直接重試" in raised.value.user_message


@pytest.mark.asyncio
async def test_runtime_timeout_covers_children_holding_output_streams(tmp_path: Path) -> None:
    executable = _fake_otter(
        tmp_path,
        """
import subprocess
import sys
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
""",
    )
    runtime = OtterCliRuntime(OtterCliConfig(command=str(executable), timeout_seconds=0.05))

    result = await runtime.run(["trips", "list"])

    assert result["status"] == "error"
    assert result["error"]["code"] == "COMMAND_TIMEOUT"


@pytest.mark.asyncio
async def test_runtime_marks_mutation_connection_failure_outcome_unknown(tmp_path: Path) -> None:
    executable = _fake_otter(
        tmp_path,
        """
import json
import sys
sys.stderr.write(json.dumps({"error": {"code": "CONNECTION_ERROR", "message": "response lost"}}))
sys.exit(1)
""",
    )
    runtime = OtterCliRuntime(OtterCliConfig(command=str(executable)))

    result = await runtime.run(["expenses", "add"], mutation=True)

    assert result["status"] == "error"
    assert result["outcome_unknown"] is True
    assert "Do not retry" in result["response_contract"]


@pytest.mark.asyncio
async def test_runtime_reports_missing_executable() -> None:
    runtime = OtterCliRuntime(OtterCliConfig(command="/definitely/missing/otter"))

    result = await runtime.run(["auth", "status"])

    assert result["status"] == "error"
    assert result["error"]["code"] == "COMMAND_NOT_FOUND"


class _RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bool]] = []

    async def run(self, arguments: list[str], *, mutation: bool = False) -> dict[str, Any]:
        self.calls.append((arguments, mutation))
        return {"status": "success", "data": {"id": "result-id"}}


@pytest.mark.asyncio
async def test_typed_tools_build_every_supported_cli_action_with_exact_ids() -> None:
    runtime = _RecordingRuntime()
    tools = OtterExpenseTools(cast(Any, runtime))

    await tools.auth_status()
    await tools.list_trips()
    await tools.get_trip("trip-1")
    await tools.create_trip("日本旅行", "JPY")
    await tools.update_trip("trip-1", name="日本 2026", archived=False)
    await tools.list_participants("trip-1")
    await tools.add_participant("trip-1", "小明")
    await tools.rename_participant("trip-1", "participant-1", "大明")
    await tools.list_expenses("trip-1")
    await tools.add_expense(
        "trip-1",
        "晚餐",
        "12000",
        "JPY",
        "participant-1",
        ["participant-1", "participant-2", "participant-1"],
        expense_date="2026-09-20",
        category="餐飲",
        tags=["東京", "晚餐"],
    )
    await tools.update_expense(
        "trip-1",
        "expense-1",
        amount="12500",
        split_with_participant_ids=["participant-1", "participant-2"],
        tags=[],
    )
    await tools.get_balances("trip-1")
    await tools.list_settlements("trip-1")
    await tools.record_settlement(
        "trip-1",
        "participant-2",
        "participant-1",
        "6250",
        currency="JPY",
        payment_date="2026-09-21",
        note="現金",
    )

    assert runtime.calls == [
        (["auth", "status"], False),
        (["trips", "list"], False),
        (["trips", "get", "--trip", "trip-1"], False),
        (["trips", "create", "--name", "日本旅行", "--currency", "JPY"], True),
        (["trips", "update", "--trip", "trip-1", "--name", "日本 2026", "--archived", "false"], True),
        (["participants", "list", "--trip", "trip-1"], False),
        (["participants", "add", "--trip", "trip-1", "--name", "小明"], True),
        (
            [
                "participants",
                "rename",
                "--trip",
                "trip-1",
                "--participant",
                "participant-1",
                "--name",
                "大明",
            ],
            True,
        ),
        (["expenses", "list", "--trip", "trip-1"], False),
        (
            [
                "expenses",
                "add",
                "--trip",
                "trip-1",
                "--description",
                "晚餐",
                "--amount",
                "12000",
                "--currency",
                "JPY",
                "--paid-by",
                "participant-1",
                "--split-with",
                "participant-1,participant-2",
                "--date",
                "2026-09-20",
                "--category",
                "餐飲",
                "--tags",
                "東京,晚餐",
            ],
            True,
        ),
        (
            [
                "expenses",
                "update",
                "--trip",
                "trip-1",
                "--expense",
                "expense-1",
                "--amount",
                "12500",
                "--split-with",
                "participant-1,participant-2",
                "--tags",
                "",
            ],
            True,
        ),
        (["balances", "get", "--trip", "trip-1"], False),
        (["settlements", "list", "--trip", "trip-1"], False),
        (
            [
                "settlements",
                "record",
                "--trip",
                "trip-1",
                "--from",
                "participant-2",
                "--to",
                "participant-1",
                "--amount",
                "6250",
                "--currency",
                "JPY",
                "--date",
                "2026-09-21",
                "--note",
                "現金",
            ],
            True,
        ),
    ]


@pytest.mark.asyncio
async def test_typed_tools_reject_empty_splits_and_empty_updates() -> None:
    tools = OtterExpenseTools(cast(Any, _RecordingRuntime()))

    with pytest.raises(ValueError, match="at least one participant"):
        await tools.add_expense("trip-1", "晚餐", "100", "TWD", "participant-1", [])
    with pytest.raises(ValueError, match="at least one field"):
        await tools.update_trip("trip-1")
    with pytest.raises(ValueError, match="at least one field"):
        await tools.update_expense("trip-1", "expense-1")


def test_build_otter_tools_exposes_only_reviewed_non_destructive_actions() -> None:
    tools = build_otter_tools(OtterCliConfig(command="otter"))
    names = [tool.name for tool in tools]

    assert names == [
        "otter_auth_status",
        "otter_list_trips",
        "otter_get_trip",
        "otter_list_participants",
        "otter_list_expenses",
        "otter_get_balances",
        "otter_list_settlements",
        "otter_create_trip",
        "otter_update_trip",
        "otter_add_participant",
        "otter_rename_participant",
        "otter_add_expense",
        "otter_update_expense",
        "otter_record_settlement",
    ]
    assert all("delete" not in name and "login" not in name and "shell" not in name for name in names)
    assert [tool.sequential for tool in tools] == [False] * 7 + [True] * 7
