from __future__ import annotations

import asyncio
import re
import shlex
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AgentSkill:
    name: str
    description: str
    content: str
    path: Path


@dataclass(frozen=True)
class SkillInstallResult:
    command: list[str]
    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


ReloadSkills = Callable[[], Awaitable[int]]


class SkillInstallerProtocol(Protocol):
    async def add(self, args: str) -> SkillInstallResult: ...

    async def list(self) -> SkillInstallResult: ...


class SkillInstaller:
    def __init__(self, *, project_root: Path, timeout_seconds: int = 180) -> None:
        self.project_root = project_root
        self.timeout_seconds = timeout_seconds

    async def add(self, args: str) -> SkillInstallResult:
        parts = shlex.split(args)
        if not parts:
            return SkillInstallResult(
                command=[], exit_code=2, output="請提供 skill package, 例如: /skills add owner/repo"
            )
        if parts[0].startswith("-"):
            return SkillInstallResult(command=[], exit_code=2, output="第一個參數必須是 skill package。")

        command = ["npx", "skills", "add", *parts]
        if "--yes" not in command and "-y" not in command and "--all" not in command:
            command.append("--yes")
        if "--copy" not in command:
            command.append("--copy")

        return await self._run(command)

    async def list(self) -> SkillInstallResult:
        return await self._run(["npx", "skills", "list", "--json"])

    async def _run(self, command: Sequence[str]) -> SkillInstallResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=self.project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except TimeoutError:
            return SkillInstallResult(command=[*command], exit_code=124, output="skills command timed out")
        except FileNotFoundError as exc:
            return SkillInstallResult(command=[*command], exit_code=127, output=str(exc))

        output = stdout.decode(errors="replace").strip()
        return SkillInstallResult(command=[*command], exit_code=process.returncode or 0, output=output[-3500:])


class SkillManagementTool:
    def __init__(
        self,
        *,
        installer: SkillInstallerProtocol,
        skill_admins: set[int] | None = None,
        fallback_admins: set[int] | None = None,
        reload_skills: ReloadSkills | None = None,
    ) -> None:
        self.installer = installer
        self.skill_admins = skill_admins or set()
        self.fallback_admins = fallback_admins or set()
        self.reload_skills = reload_skills

    async def handle(self, text: str, *, chat_id: int, user_id: int | None) -> str | None:
        command = self._parse_command(text)
        if command is None:
            return None
        if not self._is_admin(chat_id=chat_id, user_id=user_id):
            return "你沒有權限管理 Agent Skills。"

        action, _, args = command.partition(" ")
        match action.lower():
            case "add":
                return await self._add(args.strip())
            case "list" | "ls":
                result = await self.installer.list()
                return result.output.strip() or "[]"
            case _:
                return self.usage()

    def _parse_command(self, text: str) -> str | None:
        command, _, args = text.strip().partition(" ")
        command_name = command.split("@", maxsplit=1)[0].lower()
        if command_name == "/skills":
            return args.strip()
        return parse_natural_skill_command(text)

    def _is_admin(self, *, chat_id: int, user_id: int | None) -> bool:
        admin_ids = self.skill_admins or self.fallback_admins
        if not admin_ids:
            return True
        return chat_id in admin_ids or (user_id is not None and user_id in admin_ids)

    async def _add(self, args: str) -> str:
        result = await self.installer.add(args)
        if not result.ok:
            return f"Skill 安裝失敗。\n\n{result.output.strip()}"
        if self.reload_skills is not None:
            count = await self.reload_skills()
            return f"Skill 安裝完成並已重新載入 {count} 個 skill。\n\n{result.output.strip()}"
        return f"Skill 安裝完成。\n\n{result.output.strip()}"

    def usage(self) -> str:
        return "用法:\n/skills add <package> [npx skills add options]\n/skills list"


def parse_natural_skill_command(text: str) -> str | None:
    normalized = text.strip()
    match = re.search(r"(?:安裝|新增|加入)\s+([\w.-]+/[\w.-]+)(?:\s+的\s+skills?)?", normalized, flags=re.IGNORECASE)
    if not match:
        return None

    args = match.group(1)
    if re.search(r"\b(all|全部|所有)\b", normalized, flags=re.IGNORECASE):
        args = f"{args} --all"
    return f"add {args}"


def load_agent_skills(skills_dir: Path, *, enabled_names: set[str] | None = None) -> list[AgentSkill]:
    """Load Agent Skills from directories containing SKILL.md.

    This intentionally loads instructions only. It does not execute scripts bundled
    with skills.
    """
    if not skills_dir.exists():
        return []

    skills: list[AgentSkill] = []
    for skill_file in sorted(skills_dir.rglob("SKILL.md")):
        skill = parse_agent_skill(skill_file)
        if enabled_names and skill.name not in enabled_names:
            continue
        skills.append(skill)
    return skills


def parse_agent_skill(path: Path) -> AgentSkill:
    content = path.read_text(encoding="utf-8")
    frontmatter = _extract_frontmatter(content)
    name = frontmatter.get("name") or path.parent.name
    description = frontmatter.get("description") or ""
    return AgentSkill(name=name, description=description, content=content, path=path)


def format_skills_for_instructions(skills: list[AgentSkill]) -> str:
    if not skills:
        return ""

    sections = [
        "你可以使用以下 Agent Skills 作為行為指引。"
        "這些 skills 只提供指示與流程; 不要宣稱你能執行 skill 內的本機腳本或外部工具, "
        "除非系統另外提供工具。"
    ]
    sections.extend(
        f"\n---\nSkill: {skill.name}\nDescription: {skill.description}\n\n{skill.content.strip()}" for skill in skills
    )
    return "\n".join(sections)


def _extract_frontmatter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator:
            data[key.strip()] = value.strip().strip("\"'")
    return data
