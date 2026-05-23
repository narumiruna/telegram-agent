from __future__ import annotations

from telegramagent.observability import LogfireConfig
from telegramagent.observability import configure_logfire


class FakeLogfire:
    def __init__(self) -> None:
        self.calls = []

    def configure(self, **kwargs) -> None:
        self.calls.append(("configure", kwargs))

    def instrument_httpx(self, **kwargs) -> None:
        self.calls.append(("instrument_httpx", kwargs))

    def instrument_pydantic_ai(self, **kwargs) -> None:
        self.calls.append(("instrument_pydantic_ai", kwargs))

    def instrument_mcp(self) -> None:
        self.calls.append(("instrument_mcp", {}))

    def loguru_handler(self):
        self.calls.append(("loguru_handler", {}))
        return {"sink": "fake-sink", "format": "{message}"}


class FakeLogger:
    def __init__(self) -> None:
        self.add_calls = []
        self.info_calls = []

    def add(self, **kwargs) -> None:
        self.add_calls.append(kwargs)

    def info(self, *args) -> None:
        self.info_calls.append(args)


def test_configure_logfire_noops_without_token(monkeypatch) -> None:
    fake_logfire = FakeLogfire()
    monkeypatch.setattr("telegramagent.observability.logfire", fake_logfire)

    assert configure_logfire(LogfireConfig(token=None)) is False

    assert fake_logfire.calls == []


def test_configure_logfire_sets_integrations(monkeypatch) -> None:
    fake_logfire = FakeLogfire()
    fake_logger = FakeLogger()
    monkeypatch.setattr("telegramagent.observability.logfire", fake_logfire)
    monkeypatch.setattr("telegramagent.observability.logger", fake_logger)

    enabled = configure_logfire(
        LogfireConfig(token="secret-token", environment="prod", service_name="bot", include_content=True),
        verbose=True,
    )

    assert enabled is True
    assert [name for name, _ in fake_logfire.calls] == [
        "configure",
        "instrument_httpx",
        "instrument_pydantic_ai",
        "instrument_mcp",
        "loguru_handler",
    ]
    assert fake_logfire.calls[0][1] == {
        "token": "secret-token",
        "send_to_logfire": True,
        "service_name": "bot",
        "environment": "prod",
        "console": False,
        "inspect_arguments": False,
    }
    assert fake_logfire.calls[1][1] == {
        "capture_headers": False,
        "capture_request_body": False,
        "capture_response_body": False,
    }
    assert fake_logfire.calls[2][1] == {"include_content": True}
    assert fake_logger.add_calls == [{"sink": "fake-sink", "format": "{message}", "level": "DEBUG"}]
    assert fake_logger.info_calls
