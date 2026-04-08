"""Skill loading for the Volto layout agent.

Skills are directories under a configurable root (default: ``skills/``).
Each directory contains ``description.md`` (one-liner for the skill list) and
``content.md`` (full instructions loaded on demand).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillInfo:
    """Lightweight descriptor returned by the API."""

    name: str
    description: str


def discover_skills(root: Path) -> list[SkillInfo]:
    """Return all valid skills found under *root*, sorted by name."""
    if not root.is_dir():
        return []
    skills: list[SkillInfo] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        desc_file = entry / "description.md"
        content_file = entry / "content.md"
        if desc_file.exists() and content_file.exists():
            skills.append(
                SkillInfo(
                    name=entry.name,
                    description=desc_file.read_text().strip(),
                )
            )
    return skills


def load_skill(root: Path, name: str) -> str | None:
    """Load the full content of a skill by name. Returns *None* if not found."""
    content_file = root / name / "content.md"
    if not content_file.exists():
        return None
    return content_file.read_text()
