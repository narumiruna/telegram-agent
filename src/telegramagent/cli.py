from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from loguru import logger

from telegramagent.llm import ChatAgent
from telegramagent.llm import TopicEndAgent
from telegramagent.settings import Settings
from telegramagent.skills import SkillInstaller
from telegramagent.skills import SkillManagementTool
from telegramagent.skills import load_agent_skills
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

    project_root = Path.cwd()
    skills = load_agent_skills(
        settings.bot_skills_dir,
        enabled_names=settings.bot_enabled_skills or None,
    )
    if skills:
        logger.info("Loaded {} Agent Skill(s) from {}", len(skills), settings.bot_skills_dir)

    agent = ChatAgent(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        skills=skills,
    )
    topic_end_judge = TopicEndAgent(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    skill_installer = SkillInstaller(project_root=project_root)

    async def reload_skills() -> int:
        updated_skills = load_agent_skills(
            settings.bot_skills_dir,
            enabled_names=settings.bot_enabled_skills or None,
        )
        agent.reload_skills(updated_skills)
        logger.info("Reloaded {} Agent Skill(s) from {}", len(updated_skills), settings.bot_skills_dir)
        return len(updated_skills)

    telegram = TelegramClient(settings.bot_token)
    bot = TelegramBot(
        telegram=telegram,
        agent=agent,
        whitelist=settings.bot_whitelist,
        max_consecutive_replies_to_bots=settings.bot_max_consecutive_replies_to_bots,
        topic_end_judge=topic_end_judge,
        skill_tool=SkillManagementTool(
            installer=skill_installer,
            skill_admins=settings.bot_skill_admins,
            fallback_admins=settings.bot_whitelist,
            reload_skills=reload_skills,
        ),
    )

    try:
        asyncio.run(bot.run_forever())
    except KeyboardInterrupt:
        logger.info("Telegram bot stopped")


if __name__ == "__main__":
    app()
