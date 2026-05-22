from telegramagent.cli import app


def test_cli_app_exists() -> None:
    assert app.info.help == "Run a Telegram AI bot."
