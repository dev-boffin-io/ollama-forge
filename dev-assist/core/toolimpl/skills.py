"""
The `skill` tool — load a SKILL.md resource (port of opencode's SkillTool).

Skills live in one of `skills/`/`.dev-assist/skills/` under the working
directory, `~/.config/dev-assist/skills`, or the dev-assist package tree.
Each skill is a directory containing `SKILL.md` (the instructions) plus
whatever scripts/reference files it needs.
"""

from __future__ import annotations

import os

SKILL_LIMIT_FILES = 10


def _candidate_dirs(workdir: str, package_dir: str) -> list[str]:
    home_skills = os.path.expanduser(os.path.join("~", ".config", "dev-assist", "skills"))
    return [
        os.path.join(workdir, "skills"),
        os.path.join(workdir, ".dev-assist", "skills"),
        home_skills,
        os.path.join(package_dir, "..", "skills"),
    ]


def _skill_dirs(root: str) -> list[tuple[str, str]]:
    """Return [(skill_name, skill_dir)] for every valid skill under a root."""
    found = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    for name in entries:
        skill_dir = os.path.join(root, name)
        if os.path.isdir(skill_dir) and os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
            found.append((name, skill_dir))
    return found


def list_skills(workdir: str) -> list[str]:
    package_dir = os.path.dirname(os.path.abspath(__file__))
    names: list[str] = []
    for root in _candidate_dirs(workdir, package_dir):
        for name, _ in _skill_dirs(root):
            if name not in names:
                names.append(name)
    return names


def load_skill(name: str, workdir: str):
    """Load skill `name`. Returns dict(name, content, dir, files) or None."""
    package_dir = os.path.dirname(os.path.abspath(__file__))
    for root in _candidate_dirs(workdir, package_dir):
        for skill_name, skill_dir in _skill_dirs(root):
            if skill_name.strip().lower() != name.strip().lower():
                continue
            with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8", errors="replace") as f:
                content = f.read()
            files = _sample_files(skill_dir)
            return {
                "name": skill_name,
                "dir": skill_dir,
                "content": content,
                "files": files,
            }
    return None


def _sample_files(skill_dir: str) -> list[str]:
    """Up to SKILL_LIMIT_FILES absolute file paths under the skill dir."""
    out = []
    skip = {".git", "__pycache__", "node_modules"}
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if os.path.basename(full) == "SKILL.md":
                continue
            out.append(os.path.abspath(full))
            if len(out) >= SKILL_LIMIT_FILES:
                return out
    return out
