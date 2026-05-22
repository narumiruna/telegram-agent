from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentSkill:
    name: str
    description: str
    content: str
    path: Path


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
