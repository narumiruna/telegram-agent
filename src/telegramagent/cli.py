from __future__ import annotations

import asyncio
import sys

import typer
from loguru import logger

from telegramagent.llm import ChatAgent
from telegramagent.llm import TopicEndAgent
from telegramagent.settings import Settings
from telegramagent.telegram import TelegramBot
from telegramagent.telegram import TelegramClient

app = typer.Typer(help="Run a Telegram AI bot.")


def configure_logging(verbose: bool = False) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{line} - {message}",
        backtrace=False,
        diagnose=False,
    )


@app.command()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging.")) -> None:
    """Start the Telegram bot with long polling."""
    configure_logging(verbose)
    settings = Settings()

    agent = ChatAgent(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    topic_end_judge = TopicEndAgent(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    telegram = TelegramClient(settings.bot_token)
    bot = TelegramBot(
        telegram=telegram,
        agent=agent,
        whitelist=settings.bot_whitelist,
        max_consecutive_replies_to_bots=settings.bot_max_consecutive_replies_to_bots,
        topic_end_judge=topic_end_judge,
    )

    try:
        asyncio.run(bot.run_forever())
    except KeyboardInterrupt:
        logger.info("Telegram bot stopped")


if __name__ == "__main__":
    app()
