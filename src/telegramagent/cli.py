from __future__ import annotations

import asyncio
import logging

import typer

from telegramagent.llm import ChatAgent
from telegramagent.llm import TopicEndAgent
from telegramagent.settings import Settings
from telegramagent.telegram import TelegramBot
from telegramagent.telegram import TelegramClient

logger = logging.getLogger(__name__)
app = typer.Typer(help="Run a Telegram AI bot.")


def configure_logging(verbose: bool = False) -> None:
    format_str = "%(asctime)s | %(levelname)s | %(name)s:%(lineno)d - %(message)s"
    logging.basicConfig(format=format_str, level=logging.DEBUG if verbose else logging.INFO)


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
