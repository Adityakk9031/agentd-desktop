"""Skills — instruction packs the agent loads when a task needs them.

A skill is a folder with a SKILL.md file: a short header (name and
description) and plain instructions, the format OpenClaw uses. The agent
sees every skill's name and description, and calls ``use_skill`` to read
the full instructions only when one fits, so they cost nothing otherwise.

Bundled skills ship in agentd/skills; your own go in data/skills and win
over a bundled one with the same name.
"""

import os
import re
from pathlib import Path
from typing import Dict, List

from langchain_core.tools import StructuredTool

BUNDLED = Path(__file__).resolve().parent.parent / "skills"
_HEADER = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def user_skills_dir() -> Path:
    return Path(os.getenv("AGENTD_SKILLS_DIR") or Path("data") / "skills")


def _parse(path: Path) -> Dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = _HEADER.match(text)
    meta: Dict[str, str] = {}
    body = text
    if match:
        body = match.group(2)
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if value:
                meta[key.strip().lower()] = value.strip().strip('"')
    name = meta.get("name") or path.parent.name
    return {"name": name, "description": meta.get("description", ""), "body": body.strip(), "path": str(path)}


def load_skills() -> Dict[str, Dict[str, str]]:
    skills: Dict[str, Dict[str, str]] = {}
    for root in (BUNDLED, user_skills_dir()):  # later roots win
        if not root.is_dir():
            continue
        for file in sorted(root.glob("*/SKILL.md")):
            try:
                skill = _parse(file)
            except (OSError, UnicodeDecodeError):
                continue
            skills[skill["name"]] = skill
    return skills


def skills_prompt(skills: Dict[str, Dict[str, str]]) -> str:
    if not skills:
        return ""
    lines = [f"- {s['name']}: {s['description']}" for s in skills.values()]
    return ("\nSkills (instructions for particular kinds of task). When a task matches one, call use_skill "
            "with its name before you start and follow it:\n" + "\n".join(lines) + "\n")


def build_skill_tool(skills: Dict[str, Dict[str, str]]) -> List[StructuredTool]:
    if not skills:
        return []

    async def use_skill(name: str) -> str:
        """Read the full instructions of a skill listed in your instructions."""
        skill = skills.get(name) or next((s for s in skills.values() if s["name"].lower() == name.lower()), None)
        if skill is None:
            return f"There is no skill called '{name}'. Available: {', '.join(skills)}."
        return skill["body"]

    return [StructuredTool.from_function(coroutine=use_skill, name="use_skill", description=use_skill.__doc__)]


_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


def build_learning_tools(gate) -> List[StructuredTool]:
    """``save_skill``: turn a way of doing a task that worked into a skill for next time (from Hermes Agent's /learn)."""

    async def save_skill(name: str, description: str, instructions: str) -> str:
        """Save a reusable skill: step-by-step instructions for a kind of task, so next time you can do it faster. Only when the user asks, or agrees after you offer. name: lowercase-with-hyphens; description: one sentence saying when to use it; instructions: the steps, tools and pitfalls, written as Markdown."""
        from agentd.computer.tools import scrub_secrets

        name = (name or "").strip().lower()
        description = " ".join((description or "").split())[:200]
        body = scrub_secrets((instructions or "").strip())[:12000]
        if not _SKILL_NAME.match(name):
            return "ERROR: name must be 3-64 lowercase letters, digits and hyphens, like 'weekly-report'."
        if not description or len(body) < 40:
            return "ERROR: give a one-sentence description and the steps."
        folder = user_skills_dir() / name
        replacing = (folder / "SKILL.md").is_file()
        if not await gate.request("skill", f"{'Update' if replacing else 'Save'} the skill \"{name}\"",
                                  f"{description}\n\n{body[:3000]}"):
            return "The user declined saving this skill."
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8")
        return f"Saved the skill '{name}' in {folder}. It is available from the next task."

    return [StructuredTool.from_function(coroutine=save_skill, name="save_skill", description=save_skill.__doc__)]

