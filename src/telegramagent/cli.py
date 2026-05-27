from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Any

import typer
from loguru import logger

from telegramagent.actions import ActionSettings
from telegramagent.actions import KabigonExternalLoader
from telegramagent.actions import PendingActionStore
from telegramagent.actions import ProactiveActionTool
from telegramagent.capabilities import Capability
from telegramagent.capabilities import CapabilityRegistry
from telegramagent.container_tools import ContainerToolConfig
from telegramagent.container_tools import build_container_tools
from telegramagent.container_tools import is_running_in_container
from telegramagent.context_files import ContextFile
from telegramagent.context_files import ContextManagementTool
from telegramagent.context_files import load_context_file
from telegramagent.events import EventManagementTool
from telegramagent.events import EventSettings
from telegramagent.events import EventWatcher
from telegramagent.events import ImmediateEvent
from telegramagent.events import event_prompt
from telegramagent.gurume_tools import build_gurume_tools
from telegramagent.images import OpenAIImageGenerator
from telegramagent.llm import ChatAgent
from telegramagent.llm import TopicEndAgent
from telegramagent.mcp import YFinanceMcpConfig
from telegramagent.mcp import build_yfinance_mcp_toolsets
from telegramagent.mcp import command_available
from telegramagent.observability import LogfireConfig
from telegramagent.observability import configure_logfire
from telegramagent.session import SessionLog
from telegramagent.settings import Settings
from telegramagent.skills import SkillInstaller
from telegramagent.skills import SkillManagementTool
from telegramagent.skills import load_agent_skills
from telegramagent.tasks import TaskManagementTool
from telegramagent.tasks import TaskQueue
from telegramagent.telegram import TelegramBot
from telegramagent.telegram import TelegramClient

app = typer.Typer(help="Run a Telegram AI bot.")


_TELEGRAM_BOT_TOKEN_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")
_SENSITIVE_LOG_VALUE_RE = re.compile(
    r"(?i)\b(token|api[_-]?key|authorization|cookie|set-cookie|password|secret)=([^\s;]+)"
)
_BEARER_LOG_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class LoguruInterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(exception=record.exc_info, depth=6).log(
            level,
            "{}: {}",
            record.name,
            _redact_log_message(record.getMessage()),
        )


def configure_logging(verbose: bool = False) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{line} - {message}",
        backtrace=False,
        diagnose=False,
    )
    logging.captureWarnings(True)
    root_logger = logging.getLogger()
    root_logger.handlers = [LoguruInterceptHandler()]
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    for noisy_logger_name in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy_logger_name).setLevel(logging.WARNING)


def _redact_log_message(message: str) -> str:
    message = _TELEGRAM_BOT_TOKEN_RE.sub("/bot[redacted]", message)
    message = _SENSITIVE_LOG_VALUE_RE.sub(lambda match: f"{match.group(1)}=[redacted]", message)
    return _BEARER_LOG_RE.sub("Bearer [redacted]", message)


def _yfinance_mcp_unavailable_reason(config: YFinanceMcpConfig, *, available: bool) -> str:
    return _stdio_mcp_unavailable_reason(config, available=available)


def _stdio_mcp_unavailable_reason(config: YFinanceMcpConfig, *, available: bool) -> str:
    if available:
        return ""
    if not config.enabled:
        return "disabled"
    if not command_available(config.command):
        return f"command not found: {config.command}"
    return "not configured"


def _image_generation_unavailable_reason(settings: Settings) -> str:
    if not settings.bot_image_generation_enabled:
        return "disabled"
    if not settings.openai_api_key:
        return "OPENAI_API_KEY not configured"
    return ""


def _container_tools_from_settings(
    settings: Settings, *, project_root: Path, in_container: bool | None = None
) -> tuple[tuple[Any, ...], Capability]:
    description = "Docker-only local tools: bash, edit, find, grep, ls, read, write"
    if not settings.bot_container_tools_enabled:
        return (), Capability("container_tools", False, description, "disabled")

    detected = is_running_in_container() if in_container is None else in_container
    if not detected:
        return (), Capability("container_tools", False, description, "not running in Docker/container")

    root = settings.bot_container_tools_root
    if not root.is_absolute():
        root = project_root / root
    tools = build_container_tools(
        ContainerToolConfig(
            root=root,
            timeout_seconds=settings.bot_container_tools_timeout_seconds,
            max_output_chars=settings.bot_container_tools_max_output_chars,
            max_read_chars=settings.bot_container_tools_max_read_chars,
            max_results=settings.bot_container_tools_max_results,
        )
    )
    return tools, Capability("container_tools", True, description)


def _gurume_tools_from_settings(settings: Settings) -> tuple[tuple[Any, ...], Capability]:
    description = "Direct Gurume Python tools for Tabelog restaurant recommendations, search, suggestions, and details"
    if not settings.bot_gurume_tools_enabled:
        return (), Capability("tool.gurume", False, description, "disabled")
    return build_gurume_tools(), Capability("tool.gurume", True, description)


@app.command()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging.")) -> None:  # noqa: C901
    """Start the Telegram bot with long polling."""
    configure_logging(verbose)
    settings = Settings()
    configure_logfire(
        LogfireConfig(
            enabled=settings.logfire_enabled,
            token=settings.logfire_token,
            environment=settings.logfire_environment,
            service_name=settings.logfire_service_name,
            include_content=settings.logfire_include_content,
        ),
        verbose=verbose,
    )

    project_root = Path.cwd()
    soul = load_context_file(
        settings.bot_soul_path,
        label="SOUL.md",
        max_chars=settings.bot_soul_max_chars,
        required=settings.bot_soul_required,
    )
    memory = load_context_file(
        settings.bot_memory_path,
        label="MEMORY.md",
        max_chars=settings.bot_memory_max_chars,
        required=settings.bot_memory_required,
    )
    skills = load_agent_skills(
        settings.bot_skills_dir,
        enabled_names=settings.bot_enabled_skills or None,
    )
    if skills:
        logger.info("Loaded {} Agent Skill(s) from {}", len(skills), settings.bot_skills_dir)

    capabilities = CapabilityRegistry()
    yfinance_mcp_config = YFinanceMcpConfig(
        enabled=settings.bot_yfinance_mcp_enabled,
        command=settings.bot_yfinance_mcp_command,
        args=settings.bot_yfinance_mcp_args,
        init_timeout_seconds=settings.bot_yfinance_mcp_init_timeout_seconds,
        read_timeout_seconds=settings.bot_yfinance_mcp_read_timeout_seconds,
    )
    yfinance_mcp_toolsets = build_yfinance_mcp_toolsets(yfinance_mcp_config)
    capabilities.set(
        Capability(
            "mcp.yfinance",
            bool(yfinance_mcp_toolsets),
            "Yahoo Finance market data MCP tools via yfmcp",
            _yfinance_mcp_unavailable_reason(yfinance_mcp_config, available=bool(yfinance_mcp_toolsets)),
        )
    )
    capabilities.set(
        Capability(
            "image_input.telegram",
            settings.bot_image_input_enabled,
            "Telegram photo/image-document input passed to the chat model; requires a vision-capable model/provider",
            "disabled" if not settings.bot_image_input_enabled else "",
        )
    )
    capabilities.set(
        Capability(
            "image_output.openai",
            settings.bot_image_generation_enabled and bool(settings.openai_api_key),
            "OpenAI-compatible /images/generations image output via /image",
            _image_generation_unavailable_reason(settings),
        )
    )
    container_tools, container_tools_capability = _container_tools_from_settings(settings, project_root=project_root)
    capabilities.set(container_tools_capability)
    if container_tools:
        logger.info("Enabled {} Docker-only container tool(s)", len(container_tools))
    gurume_tools, gurume_tools_capability = _gurume_tools_from_settings(settings)
    capabilities.set(gurume_tools_capability)
    if gurume_tools:
        logger.info("Enabled {} Gurume Python tool(s)", len(gurume_tools))
    agent = ChatAgent(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        skills=skills,
        soul=soul,
        memory=memory,
        capability_summary=capabilities.summary(),
        kabigon_tool_timeout_seconds=settings.bot_kabigon_timeout_seconds,
        mcp_toolsets=tuple(yfinance_mcp_toolsets),
        tools=(*gurume_tools, *container_tools),
    )
    topic_end_judge = TopicEndAgent(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    image_generator = (
        OpenAIImageGenerator(
            api_key=settings.openai_api_key,
            model=settings.bot_image_generation_model,
            base_url=settings.openai_base_url,
            size=settings.bot_image_generation_size,
            timeout_seconds=settings.bot_image_generation_timeout_seconds,
        )
        if settings.bot_image_generation_enabled and settings.openai_api_key
        else None
    )
    skill_installer = SkillInstaller(project_root=project_root)
    proactive_tool = ProactiveActionTool(
        settings=ActionSettings(
            enabled=settings.bot_proactive_enabled,
            url_timeout_seconds=settings.bot_proactive_url_timeout_seconds,
            max_extracted_chars=settings.bot_proactive_max_extracted_chars,
            pending_ttl_seconds=settings.bot_proactive_pending_ttl_seconds,
            allowed_schemes=frozenset(settings.bot_proactive_allowed_schemes),
            external_loader_timeout_seconds=settings.bot_kabigon_timeout_seconds,
        ),
        pending=PendingActionStore(ttl_seconds=settings.bot_proactive_pending_ttl_seconds),
        capabilities=capabilities,
        external_loader=KabigonExternalLoader(
            timeout_seconds=settings.bot_kabigon_timeout_seconds,
            max_chars=settings.bot_proactive_max_extracted_chars,
        )
        if capabilities.is_available("external_loader.kabigon")
        else None,
    )

    def installed_skill_names() -> set[str]:
        return {
            skill.name
            for skill in load_agent_skills(
                settings.bot_skills_dir,
                enabled_names=settings.bot_enabled_skills or None,
            )
        }

    current_soul = soul
    current_memory = memory

    def get_soul_context() -> ContextFile:
        return current_soul

    def get_memory_context() -> ContextFile:
        return current_memory

    async def reload_soul() -> ContextFile:
        nonlocal current_soul
        current_soul = load_context_file(
            settings.bot_soul_path,
            label="SOUL.md",
            max_chars=settings.bot_soul_max_chars,
            required=settings.bot_soul_required,
        )
        agent.reload_context(soul=current_soul)
        logger.info("Reloaded SOUL.md from {}", settings.bot_soul_path)
        return current_soul

    async def reload_memory() -> ContextFile:
        nonlocal current_memory
        current_memory = load_context_file(
            settings.bot_memory_path,
            label="MEMORY.md",
            max_chars=settings.bot_memory_max_chars,
            required=settings.bot_memory_required,
        )
        agent.reload_context(memory=current_memory)
        logger.info("Reloaded MEMORY.md from {}", settings.bot_memory_path)
        return current_memory

    async def reload_skills() -> int:
        updated_skills = load_agent_skills(
            settings.bot_skills_dir,
            enabled_names=settings.bot_enabled_skills or None,
        )
        agent.reload_skills(updated_skills)
        logger.info("Reloaded {} Agent Skill(s) from {}", len(updated_skills), settings.bot_skills_dir)
        return len(updated_skills)

    session_log = SessionLog(settings.bot_session_log_dir)
    task_queue = TaskQueue(max_concurrent_per_chat=settings.bot_tasks_max_concurrent_per_chat)
    telegram = TelegramClient(settings.bot_token)
    event_watcher = EventWatcher(
        settings=EventSettings(
            enabled=settings.bot_events_enabled,
            events_dir=settings.bot_events_dir,
            scan_seconds=settings.bot_events_scan_seconds,
            max_queued_per_chat=settings.bot_events_max_queued_per_chat,
            max_text_chars=settings.bot_events_max_text_chars,
            archive_processed=settings.bot_events_archive_processed,
        ),
        dispatch=lambda event: dispatch_event(event),
    )

    async def dispatch_event(event: ImmediateEvent) -> None:
        await bot.dispatch_synthetic_message(
            chat_id=event.chat_id,
            text=event_prompt(event),
            reply_to_message_id=event.reply_to_message_id,
            reply_mode=event.reply_mode,
        )

    bot = TelegramBot(
        telegram=telegram,
        agent=agent,
        whitelist=settings.bot_whitelist,
        max_consecutive_replies_to_bots=settings.bot_max_consecutive_replies_to_bots,
        group_passive_context_enabled=settings.bot_group_passive_context_enabled,
        topic_end_judge=topic_end_judge,
        skill_tool=SkillManagementTool(
            installer=skill_installer,
            skill_admins=settings.bot_skill_admins,
            fallback_admins=settings.bot_whitelist,
            reload_skills=reload_skills,
            installed_skill_names=installed_skill_names,
        ),
        proactive_tool=proactive_tool,
        session_log=session_log,
        task_queue=task_queue,
        image_input_enabled=settings.bot_image_input_enabled,
        image_max_bytes=settings.bot_image_max_bytes,
        image_generator=image_generator,
        tools=[
            ContextManagementTool(
                command_name="soul",
                display_name="SOUL.md",
                current_context=get_soul_context,
                reload_context=reload_soul,
                admins=settings.bot_skill_admins,
                fallback_admins=settings.bot_whitelist,
            ),
            ContextManagementTool(
                command_name="memory",
                display_name="MEMORY.md",
                current_context=get_memory_context,
                reload_context=reload_memory,
                admins=settings.bot_skill_admins,
                fallback_admins=settings.bot_whitelist,
            ),
            EventManagementTool(
                watcher=event_watcher,
                admins=settings.bot_skill_admins,
                fallback_admins=settings.bot_whitelist,
            ),
            TaskManagementTool(
                queue=task_queue,
                admins=settings.bot_skill_admins,
                fallback_admins=settings.bot_whitelist,
            ),
        ],
    )

    async def run() -> None:
        if not settings.bot_events_enabled:
            await bot.run_forever()
            return
        event_task = asyncio.create_task(event_watcher.run_forever())
        try:
            await bot.run_forever()
        finally:
            event_watcher.stop()
            await event_task

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        event_watcher.stop()
        logger.info("Telegram bot stopped")


if __name__ == "__main__":
    app()
