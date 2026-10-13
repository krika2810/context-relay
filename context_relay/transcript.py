"""Turn a normalized session into (1) a complete Markdown transcript and (2) a compact brief.

The transcript keeps everything: every message, thinking block, tool call and tool result.
The brief is what gets injected into the next agent's context. It is small enough for hook
limits and points at the transcript for the full detail.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

from .sources import RELAY_TAG, Event, Session

BRIEF_LIMIT = 9000  # hook outputs are capped (Claude Code truncates additionalContext at 10k chars)
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "edit_file", "write", "search_replace",
               "create_file", "delete_file", "str_replace_based_edit_tool"}
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)


def _fence(text: str, lang: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`{3,}", text)), default=2)
    f = "`" * (longest + 1)
    return f"{f}{lang}\n{text.rstrip()}\n{f}"


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + f" … [+{len(text) - limit} chars]"


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def ago(ts: float) -> str:
    s = max(0, int(time.time() - ts))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit} ago"
    return "just now"


def stats(events: List[Event]) -> Dict[str, int]:
    return {
        "messages": sum(e.kind in ("user", "assistant") for e in events),
        "tool_calls": sum(e.kind == "tool_call" for e in events),
    }


def render_transcript(session: Session) -> str:
    events = session.events()
    st = stats(events)
    tool_names: Dict[str, str] = {}
    out = [
        f"# {session.name} session transcript",
        "",
        f"- **Agent:** {session.name}",
        f"- **Session:** `{session.id}`" + (f" — {session.title}" if session.title else ""),
        f"- **Project:** `{session.cwd}`",
        f"- **Started:** {session.started or 'unknown'} · **Last activity:** {_when(session.updated)}",
        f"- **Contents:** {st['messages']} messages, {st['tool_calls']} tool calls",
        f"- **Source:** `{session.source}`",
        "",
        "Complete record exported by Context Relay: every message, reasoning block, tool call and tool result, in order.",
        "",
    ]
    turn = 0
    for e in events:
        if e.kind == "user":
            turn += 1
            out += ["---", "", f"## 👤 User (turn {turn})", "", e.text, ""]
        elif e.kind == "assistant":
            out += [f"### 🤖 {session.name}", "", e.text, ""]
        elif e.kind == "thinking":
            out += ["<details><summary>💭 Thinking</summary>", "", e.text, "", "</details>", ""]
        elif e.kind == "tool_call":
            tool_names[e.call_id] = e.tool
            out += [f"#### 🔧 Tool call: `{e.tool}`", "", _fence(e.text), ""]
        elif e.kind == "tool_result":
            name = tool_names.get(e.call_id, "tool")
            label = f"#### {'❌' if e.is_error else '↳'} Result: `{name}`" + (" (error)" if e.is_error else "")
            out += [label, "", _fence(e.text or "(no output)"), ""]
    return "\n".join(out).rstrip() + "\n"


def todos(events: List[Event]) -> List[str]:
    """The latest todo/plan list the agent wrote (Claude TodoWrite, Codex update_plan, Cursor todos)."""
    for e in reversed(events):
        if e.kind != "tool_call" or e.tool not in ("TodoWrite", "update_plan", "todo_write"):
            continue
        try:
            data = json.loads(e.text)
        except ValueError:
            continue
        items = data.get("todos") or data.get("plan") or []
        lines = []
        for it in items:
            if not isinstance(it, dict):
                continue
            text = it.get("content") or it.get("step") or it.get("title") or ""
            status = str(it.get("status", "")).lower()
            mark = "x" if "complete" in status or status == "done" else "~" if "progress" in status else " "
            if text:
                lines.append(f"- [{mark}] {text}")
        return lines
    return []


def edited_files(events: List[Event], root: str = "") -> List[str]:
    files: List[str] = []
    for e in events:
        if e.kind != "tool_call":
            continue
        found: List[str] = []
        if e.tool in _EDIT_TOOLS:
            try:
                data = json.loads(e.text)
                path = data.get("file_path") or data.get("target_file") or data.get("path") or data.get("notebook_path")
                found = [path] if path else []
            except ValueError:
                found = [e.text.strip()] if "\n" not in e.text.strip() else []
        found += _PATCH_FILE.findall(e.text)  # apply_patch (Codex), possibly wrapped in other tools
        for f in found:
            f = os.path.relpath(f, root) if root and os.path.isabs(f) and f.startswith(root.rstrip("/") + "/") else f
            if f and f not in files:
                files.append(f)
    return files


def render_brief(session: Session, transcript_rel: str, git_lines: List[str], target: Optional[str]) -> str:
    events = session.events()
    st = stats(events)
    users = [e.text for e in events if e.kind == "user"]
    last_assistant = next((e.text for e in reversed(events) if e.kind == "assistant"), "")
    title = f' "{session.title}"' if session.title else ""

    head = [
        f"<{RELAY_TAG}>",
        f"# Context Relay: continuing from {session.name}",
        "",
        f"The user was working in this project with **{session.name}** (session{title}, last active "
        f"{ago(session.updated)}, {_when(session.updated)}) and has now switched to you. Treat that work as "
        "your own prior work: continue it rather than starting over.",
        "",
        f"**Full transcript** ({st['messages']} messages, {st['tool_calls']} tool calls — every message, tool "
        f"call and tool result): `{transcript_rel}`",
        "Read it (at least the final part) before acting on anything related to earlier work, and always when "
        'the user says "continue". Verify the current state with `git status` / `git diff`.',
        "",
    ]
    if git_lines:
        head += ["## Repository state now", ""] + git_lines + [""]
    todo = todos(events)
    if todo:
        head += [f"## {session.name}'s todo list (latest)", ""] + todo + [""]
    edits = edited_files(events, session.cwd)
    if edits:
        shown = edits[:25]
        head += ["## Files the previous agent edited", ""] + [f"- `{f}`" for f in shown]
        if len(edits) > len(shown):
            head.append(f"- … and {len(edits) - len(shown)} more (see transcript)")
        head.append("")
    tail = ["## Last message from " + session.name, "", _clip(last_assistant, 2500) or "_(none)_", "", f"</{RELAY_TAG}>"]

    # Fit as many user messages as the budget allows: the first request plus the most recent ones.
    budget = BRIEF_LIMIT - len("\n".join(head + tail)) - 200
    lines: List[str] = []
    if users:
        first = f"1. {_clip(users[0], 1200)}"
        recent: List[str] = []
        used = len(first)
        for i in range(len(users) - 1, 0, -1):
            line = f"{i + 1}. {_clip(users[i], 500)}"
            if used + len(line) > budget:
                break
            recent.insert(0, line)
            used += len(line) + 1
        lines = [first]
        skipped = len(users) - 1 - len(recent)
        if skipped:
            lines.append(f"   … {skipped} more message(s) in the transcript …")
        lines += recent
    middle = ["## What the user asked (in order)", ""] + (lines or ["_(no user messages)_"]) + [""]
    brief = "\n".join(head + middle + tail)
    if len(brief) > BRIEF_LIMIT:  # e.g. a huge todo list; keep the closing tag intact
        brief = brief[: BRIEF_LIMIT - 40].rstrip() + f"\n…[truncated]\n</{RELAY_TAG}>"
    return brief
