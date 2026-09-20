from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Literal
from typing import cast

from pydantic_ai import Tool

OtterCurrency = Literal["TWD", "JPY", "USD", "EUR"]
OtterExpenseCategory = Literal["餐飲", "交通", "住宿", "門票", "購物", "其他"]

_READ_RESPONSE_CONTRACT = (
    "Treat data as authoritative Otter JSON. Use returned IDs exactly, never guess an ID from a name, and ask the "
    "user when multiple records have the same requested name."
)
_MUTATION_RESPONSE_CONTRACT = (
    "The CLI reported a mutation result, but do not report completion yet. Verify it with the narrowest available "
    "read, using its returned ID when supported. Then call otter_get_balances for expense or settlement changes. "
    "Never repeat this mutation automatically."
)
_UNKNOWN_MUTATION_RESPONSE_CONTRACT = (
    "The mutation outcome is unknown. Do not retry it. Read the narrow target resource and balances to determine "
    "whether it committed before taking another action or reporting completion."
)
_ERROR_RESPONSE_CONTRACT = (
    "Do not claim the Otter operation succeeded. Correct only clear input errors; authentication, permission, "
    "conflict, connection, and configuration failures require operator action and must not be retried automatically."
)
_SENSITIVE_VALUE_RE = re.compile(r"(?i)\b(token|api[_-]?key|authorization|cookie|set-cookie|password|secret)=([^\s;]+)")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_ENV_ALLOWLIST = {
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "NODE_EXTRA_CA_CERTS",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "XDG_CONFIG_HOME",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


@dataclass(frozen=True)
class OtterCliConfig:
    command: str
    base_url: str = "https://otter.narumi.dev/"
    token: str | None = field(default=None, repr=False)
    config_path: Path | None = None
    timeout_seconds: float = 30.0
    max_output_chars: int = 20_000


class OtterCliRuntime:
    def __init__(self, config: OtterCliConfig) -> None:
        if config.timeout_seconds <= 0:
            raise ValueError("Otter CLI timeout must be positive")
        if config.max_output_chars < 100:
            raise ValueError("Otter CLI output limit must be at least 100 characters")
        self.config = config

    async def run(self, arguments: list[str], *, mutation: bool = False) -> dict[str, Any]:
        env = self._environment()
        try:
            process = await asyncio.create_subprocess_exec(
                self.config.command,
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            return self._error(
                category="configuration",
                code="COMMAND_NOT_FOUND",
                message="The configured Otter CLI executable was not found.",
            )
        except OSError as exc:
            return self._error(
                category="configuration",
                code="COMMAND_START_FAILED",
                message=_redact_text(str(exc), secrets=self._secrets()),
            )

        if process.stdout is None or process.stderr is None:  # pragma: no cover - subprocess contract
            process.kill()
            await process.wait()
            return self._error(
                category="runtime",
                code="OUTPUT_CAPTURE_FAILED",
                message="The Otter CLI output streams were unavailable.",
            )

        stdout_task = asyncio.create_task(_read_bounded(process.stdout, self.config.max_output_chars))
        stderr_task = asyncio.create_task(_read_bounded(process.stderr, self.config.max_output_chars))
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                exit_code = await process.wait()
                (stdout_bytes, stdout_truncated), (stderr_bytes, stderr_truncated) = await asyncio.gather(
                    stdout_task, stderr_task
                )
        except TimeoutError:
            await _stop_process(process, stdout_task, stderr_task)
            return self._error(
                category="timeout",
                code="COMMAND_TIMEOUT",
                message=f"The Otter CLI timed out after {self.config.timeout_seconds:g} seconds.",
                outcome_unknown=mutation,
            )
        except asyncio.CancelledError:
            await _stop_process(process, stdout_task, stderr_task)
            raise

        if stdout_truncated or stderr_truncated:
            return self._error(
                category="output",
                code="OUTPUT_TOO_LARGE",
                message="The Otter CLI output exceeded the configured limit.",
                outcome_unknown=mutation,
            )

        stdout = _redact_text(stdout_bytes.decode(errors="replace"), secrets=self._secrets()).strip()
        stderr = _redact_text(stderr_bytes.decode(errors="replace"), secrets=self._secrets()).strip()
        if exit_code == 0:
            payload = _parse_json(stdout)
            if payload is None:
                return self._error(
                    category="response",
                    code="INVALID_JSON",
                    message="The Otter CLI returned invalid JSON.",
                    outcome_unknown=mutation,
                )
            return {
                "status": "success",
                "data": _sanitize_json(payload, secrets=self._secrets()),
                "response_contract": _MUTATION_RESPONSE_CONTRACT if mutation else _READ_RESPONSE_CONTRACT,
            }

        error_payload = _parse_json(stderr or stdout)
        return self._cli_error(error_payload, fallback_text=stderr or stdout, mutation=mutation)

    def _environment(self) -> dict[str, str]:
        environment = {key: value for key, value in os.environ.items() if key in _ENV_ALLOWLIST}
        environment["OTTER_URL"] = self.config.base_url
        if self.config.token:
            environment["OTTER_TOKEN"] = self.config.token
        if self.config.config_path is not None:
            environment["OTTER_CONFIG_PATH"] = str(self.config.config_path)
        return environment

    def _cli_error(self, payload: object | None, *, fallback_text: str, mutation: bool) -> dict[str, Any]:
        error = payload.get("error") if isinstance(payload, Mapping) else None
        if isinstance(error, Mapping):
            code = str(error.get("code") or "CLI_ERROR")
            message = str(error.get("message") or "The Otter CLI command failed.")
            status = error.get("status")
        else:
            code = "CLI_ERROR"
            message = fallback_text or "The Otter CLI command failed without an error response."
            status = None
        category = _error_category(code, status)
        return self._error(
            category=category,
            code=code,
            message=_redact_text(message, secrets=self._secrets()),
            http_status=status if isinstance(status, int) else None,
            outcome_unknown=mutation and category in {"cli", "connection", "server"},
        )

    def _error(
        self,
        *,
        category: str,
        code: str,
        message: str,
        http_status: int | None = None,
        outcome_unknown: bool = False,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {
            "category": category,
            "code": code,
            "message": message,
        }
        if http_status is not None:
            error["http_status"] = http_status
        result: dict[str, Any] = {
            "status": "error",
            "error": error,
            "retryable": False,
            "response_contract": (_UNKNOWN_MUTATION_RESPONSE_CONTRACT if outcome_unknown else _ERROR_RESPONSE_CONTRACT),
        }
        if outcome_unknown:
            result["outcome_unknown"] = True
        return result

    def _secrets(self) -> tuple[str, ...]:
        return (self.config.token,) if self.config.token else ()


class OtterExpenseTools:
    def __init__(self, runtime: OtterCliRuntime) -> None:
        self.runtime = runtime

    async def auth_status(self) -> dict[str, Any]:
        """Check whether the configured Otter CLI identity is authorized."""
        return await self.runtime.run(["auth", "status"])

    async def list_trips(self) -> dict[str, Any]:
        """List visible Otter trips before resolving a trip name to an ID."""
        return await self.runtime.run(["trips", "list"])

    async def get_trip(self, trip_id: str) -> dict[str, Any]:
        """Get one Otter trip by an exact ID returned by list_trips."""
        return await self.runtime.run(["trips", "get", "--trip", _required(trip_id, "trip_id")])

    async def create_trip(self, name: str, currency: OtterCurrency = "TWD") -> dict[str, Any]:
        """Create an Otter trip. The currency is the trip base currency."""
        return await self.runtime.run(
            ["trips", "create", "--name", _required(name, "name"), "--currency", currency], mutation=True
        )

    async def update_trip(
        self,
        trip_id: str,
        name: str | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        """Update the name or archived state of an exact Otter trip ID."""
        arguments = ["trips", "update", "--trip", _required(trip_id, "trip_id")]
        _append_optional(arguments, "name", name)
        if archived is not None:
            arguments.extend(["--archived", str(archived).lower()])
        _require_change(arguments, fixed_length=4)
        return await self.runtime.run(arguments, mutation=True)

    async def list_participants(self, trip_id: str) -> dict[str, Any]:
        """List participants for an exact Otter trip ID before resolving names."""
        return await self.runtime.run(["participants", "list", "--trip", _required(trip_id, "trip_id")])

    async def add_participant(self, trip_id: str, name: str) -> dict[str, Any]:
        """Add one named participant to an exact Otter trip ID."""
        return await self.runtime.run(
            [
                "participants",
                "add",
                "--trip",
                _required(trip_id, "trip_id"),
                "--name",
                _required(name, "name"),
            ],
            mutation=True,
        )

    async def rename_participant(self, trip_id: str, participant_id: str, name: str) -> dict[str, Any]:
        """Rename an exact participant ID after listing current trip participants."""
        return await self.runtime.run(
            [
                "participants",
                "rename",
                "--trip",
                _required(trip_id, "trip_id"),
                "--participant",
                _required(participant_id, "participant_id"),
                "--name",
                _required(name, "name"),
            ],
            mutation=True,
        )

    async def list_expenses(self, trip_id: str) -> dict[str, Any]:
        """List expenses for an exact Otter trip ID."""
        return await self.runtime.run(["expenses", "list", "--trip", _required(trip_id, "trip_id")])

    async def add_expense(
        self,
        trip_id: str,
        description: str,
        amount: str,
        currency: OtterCurrency,
        paid_by_participant_id: str,
        split_with_participant_ids: list[str],
        expense_date: str | None = None,
        category: OtterExpenseCategory | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add one equal-split expense using major-unit amount text and exact participant IDs."""
        arguments = [
            "expenses",
            "add",
            "--trip",
            _required(trip_id, "trip_id"),
            "--description",
            _required(description, "description"),
            "--amount",
            _required(amount, "amount"),
            "--currency",
            currency,
            "--paid-by",
            _required(paid_by_participant_id, "paid_by_participant_id"),
            "--split-with",
            _participant_list(split_with_participant_ids),
        ]
        _append_optional(arguments, "date", expense_date)
        _append_optional(arguments, "category", category)
        _append_list(arguments, "tags", tags)
        return await self.runtime.run(arguments, mutation=True)

    async def update_expense(
        self,
        trip_id: str,
        expense_id: str,
        description: str | None = None,
        amount: str | None = None,
        currency: OtterCurrency | None = None,
        paid_by_participant_id: str | None = None,
        split_with_participant_ids: list[str] | None = None,
        expense_date: str | None = None,
        category: OtterExpenseCategory | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update selected fields on an exact expense ID; an empty tags list clears tags."""
        arguments = [
            "expenses",
            "update",
            "--trip",
            _required(trip_id, "trip_id"),
            "--expense",
            _required(expense_id, "expense_id"),
        ]
        _append_optional(arguments, "description", description)
        _append_optional(arguments, "amount", amount)
        _append_optional(arguments, "currency", currency)
        _append_optional(arguments, "paid-by", paid_by_participant_id)
        if split_with_participant_ids is not None:
            arguments.extend(["--split-with", _participant_list(split_with_participant_ids)])
        _append_optional(arguments, "date", expense_date)
        _append_optional(arguments, "category", category)
        _append_list(arguments, "tags", tags)
        _require_change(arguments, fixed_length=6)
        return await self.runtime.run(arguments, mutation=True)

    async def get_balances(self, trip_id: str) -> dict[str, Any]:
        """Get balances and settlement suggestions for an exact Otter trip ID."""
        return await self.runtime.run(["balances", "get", "--trip", _required(trip_id, "trip_id")])

    async def list_settlements(self, trip_id: str) -> dict[str, Any]:
        """List suggested settlements and recorded payments for an exact Otter trip ID."""
        return await self.runtime.run(["settlements", "list", "--trip", _required(trip_id, "trip_id")])

    async def record_settlement(
        self,
        trip_id: str,
        from_participant_id: str,
        to_participant_id: str,
        amount: str,
        currency: OtterCurrency | None = None,
        payment_date: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Record a settlement in major units; this records a payment but does not transfer real funds."""
        arguments = [
            "settlements",
            "record",
            "--trip",
            _required(trip_id, "trip_id"),
            "--from",
            _required(from_participant_id, "from_participant_id"),
            "--to",
            _required(to_participant_id, "to_participant_id"),
            "--amount",
            _required(amount, "amount"),
        ]
        _append_optional(arguments, "currency", currency)
        _append_optional(arguments, "date", payment_date)
        _append_optional(arguments, "note", note)
        return await self.runtime.run(arguments, mutation=True)


def build_otter_tools(config: OtterCliConfig) -> tuple[Tool[Any], ...]:
    expense_tools = OtterExpenseTools(OtterCliRuntime(config))
    read_methods = (
        ("otter_auth_status", expense_tools.auth_status),
        ("otter_list_trips", expense_tools.list_trips),
        ("otter_get_trip", expense_tools.get_trip),
        ("otter_list_participants", expense_tools.list_participants),
        ("otter_list_expenses", expense_tools.list_expenses),
        ("otter_get_balances", expense_tools.get_balances),
        ("otter_list_settlements", expense_tools.list_settlements),
    )
    mutation_methods = (
        ("otter_create_trip", expense_tools.create_trip),
        ("otter_update_trip", expense_tools.update_trip),
        ("otter_add_participant", expense_tools.add_participant),
        ("otter_rename_participant", expense_tools.rename_participant),
        ("otter_add_expense", expense_tools.add_expense),
        ("otter_update_expense", expense_tools.update_expense),
        ("otter_record_settlement", expense_tools.record_settlement),
    )
    reads = tuple(Tool(cast(Any, method), name=name, takes_ctx=False) for name, method in read_methods)
    mutations = tuple(
        Tool(cast(Any, method), name=name, takes_ctx=False, sequential=True) for name, method in mutation_methods
    )
    return (*reads, *mutations)


async def _stop_process(
    process: asyncio.subprocess.Process,
    *reader_tasks: asyncio.Task[tuple[bytes, bool]],
) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await process.wait()
    for task in reader_tasks:
        task.cancel()
    await asyncio.gather(*reader_tasks, return_exceptions=True)


async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    data = bytearray()
    truncated = False
    while chunk := await stream.read(8192):
        remaining = limit + 1 - len(data)
        if remaining > 0:
            data.extend(chunk[:remaining])
        if len(data) > limit or len(chunk) > remaining:
            truncated = True
    return bytes(data[:limit]), truncated


def _parse_json(value: str) -> object | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _sanitize_json(value: object, *, secrets: tuple[str, ...]) -> object:
    if isinstance(value, str):
        return _redact_text(value, secrets=secrets)
    if isinstance(value, list):
        return [_sanitize_json(item, secrets=secrets) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _sanitize_json(item, secrets=secrets) for key, item in value.items()}
    return value


def _redact_text(value: str, *, secrets: tuple[str, ...]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    redacted = _SENSITIVE_VALUE_RE.sub(lambda match: f"{match.group(1)}=[redacted]", redacted)
    return _BEARER_RE.sub("Bearer [redacted]", redacted)


def _error_category(code: str, status: object) -> str:
    normalized = code.upper()
    if status == 401 or normalized == "AUTH_ERROR":
        return "authentication"
    if status == 403:
        return "permission"
    if status == 409 or normalized == "CONFLICT":
        return "conflict"
    if normalized in {"CONFIG_ERROR", "INSECURE_HTTP"}:
        return "configuration"
    if normalized == "CONNECTION_ERROR":
        return "connection"
    if normalized in {"USAGE", "CONFIRMATION_REQUIRED"} or status == 400:
        return "input"
    if isinstance(status, int) and status >= 500:
        return "server"
    return "cli"


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _append_optional(arguments: list[str], name: str, value: object | None) -> None:
    if value is None:
        return
    normalized = _required(str(value), name)
    arguments.extend([f"--{name}", normalized])


def _append_list(arguments: list[str], name: str, values: list[str] | None) -> None:
    if values is None:
        return
    normalized = [_required(value, name) for value in values]
    arguments.extend([f"--{name}", ",".join(dict.fromkeys(normalized))])


def _participant_list(values: list[str]) -> str:
    normalized = [_required(value, "participant_id") for value in values]
    unique = list(dict.fromkeys(normalized))
    if not unique:
        raise ValueError("split_with_participant_ids must contain at least one participant ID")
    return ",".join(unique)


def _require_change(arguments: list[str], *, fixed_length: int) -> None:
    if len(arguments) == fixed_length:
        raise ValueError("at least one field must be provided for update")
