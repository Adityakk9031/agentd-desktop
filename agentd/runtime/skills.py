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
