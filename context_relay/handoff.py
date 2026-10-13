"""Decide when a handoff is needed and write it into the project's .relay/ folder."""
from __future__ import annotations

import itertools
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import gitinfo
from .sources import Session, discover
from .transcript import render_brief, render_transcript

HANDOFF_MAX_AGE_DAYS = 14
CURSOR_RULE = Path(".cursor/rules/relay-handoff.mdc")


@dataclass
class Handoff:
    session: Session
    root: Path
    transcript: Path
    context: Path
    brief: str


def candidate(root: Path, target: Optional[str], exclude_ids=(), force: bool = False) -> Optional[Session]:
    """The session to hand off to `target`.

    Only hand off when the most recent work in this project was done by a *different* agent
    (that is what "switching agents" means). Skip sessions with no user messages.
    """
    for s in discover(str(root), HANDOFF_MAX_AGE_DAYS if not force else 365):
        if s.id in exclude_ids or not s.user_messages():
            continue
        if s.agent == target and not force:
            return None  # the target agent itself was last here; its own history covers it
        return s
    return None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write(session: Session, root: Path, target: Optional[str]) -> Handoff:
    relay_dir = root / ".relay"
    gitignore = relay_dir / ".gitignore"
    if not gitignore.exists():
        _write(gitignore, "# Created by Context Relay: keep handoffs out of git\n*\n")
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(session.updated))
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", session.id)[:12]
    transcript = relay_dir / "transcripts" / f"{session.agent}-{stamp}-{safe_id}.md"
    # Drop older exports of the same session so the folder doesn't fill with duplicates.
    for old in transcript.parent.glob(f"{session.agent}-*-{safe_id}.md") if transcript.parent.exists() else []:
        if old != transcript:
            old.unlink()
    _write(transcript, render_transcript(session))
    rel = transcript.relative_to(root).as_posix()
    brief = render_brief(session, rel, gitinfo.summary_lines(root), target)
    context = relay_dir / "CONTEXT.md"
    _write(context, brief + "\n")
    return Handoff(session, root, transcript, context, brief)


def prepare(project, target: Optional[str], exclude_ids=(), force: bool = False) -> Optional[Handoff]:
    root = gitinfo.project_root(project)
    session = candidate(root, target, exclude_ids, force)
    if target != "cursor":
        clear_cursor_rule(root)  # a previous "Continue in Cursor" rule is stale once anyone else takes over
    if not session:
        return None
    return write(session, root, target)


def kickoff_prompt(h: Handoff) -> str:
    rel = h.transcript.relative_to(h.root).as_posix()
    return (f"Continue where {h.session.name} left off. The handoff is in .relay/CONTEXT.md and the "
            f"full transcript in {rel}; read them first.")


def cursor_prompt(h: Handoff) -> str:
    # Plain words only: Cursor's deeplink parser rejects some prompts with paths, backticks or newlines.
    # The rule file Cursor loads already carries the full brief and the transcript path.
    return (f"Continue where {h.session.name} left off. Read the Context Relay handoff project rule "
            f"and the full transcript it points to, then continue that work.")


def write_cursor_rule(h: Handoff) -> Path:
    path = h.root / CURSOR_RULE
    body = h.brief.replace("<context-relay>", "").replace("</context-relay>", "").strip()
    _write(path, "---\ndescription: Context Relay handoff from the previous coding agent\n"
                 "alwaysApply: true\n---\n\n" + body + "\n")
    return path


def clear_cursor_rule(root: Path) -> bool:
    path = root / CURSOR_RULE
    if path.exists():
        path.unlink()
        return True
    return False


def projects(limit: int = 15, sessions_per_project: int = 5) -> List[dict]:
    """Recent projects across all agents, for the menu bar app."""
    groups = {}
    roots = {}
    for s in discover():
        if s.cwd not in roots:
            roots[s.cwd] = str(gitinfo.project_root(s.cwd)) if os.path.isdir(s.cwd) else None
        root = roots[s.cwd]
        if root:
            groups.setdefault(root, []).append(s)
    out = []
    for root, sessions in list(groups.items())[:limit]:
        sessions = list(itertools.islice((s for s in sessions if s.title or s.user_messages()), sessions_per_project))
        if not sessions:
            continue
        changes = gitinfo.changed_files(root)
        out.append({
            "path": root,
            "name": os.path.basename(root),
            "last_agent": sessions[0].agent,
            "last_active": _iso(sessions[0].updated),
            "branch": gitinfo.branch(root),
            "changed_files": None if changes is None else len(changes),
            "sessions": [{"agent": s.agent, "id": s.id, "title": title(s), "updated": _iso(s.updated)}
                         for s in sessions],
        })
    return out


def title(s: Session) -> str:
    if s.title:
        return s.title
    first = next(iter(s.user_messages()), "")
    first = " ".join(first.split())
    return first[:80] + ("…" if len(first) > 80 else "")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))
