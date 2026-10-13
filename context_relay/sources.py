"""Read agent sessions straight from each agent's local storage.

- Claude Code: ~/.claude/projects/<slug>/<session>.jsonl
- Codex:       ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
- Cursor:      chat database (state.vscdb) under ~/Library/Application Support/Cursor/User,
               plus agent transcripts under ~/.cursor/projects/<slug>/agent-transcripts

Everything is normalized into a list of Events (user / assistant / thinking / tool_call /
tool_result) so the rest of relay doesn't care which agent produced a session.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import unquote, urlparse

AGENT_NAMES = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor"}
MAX_AGE_DAYS = 30
RELAY_TAG = "context-relay"  # wraps context relay injects, so it's never re-exported

_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_RELAY_BLOCK = re.compile(rf"<{RELAY_TAG}>.*?</{RELAY_TAG}>", re.DOTALL)
# Text that agents inject into the "user" role but the user never typed.
_INJECTED_PREFIXES = (
    "<environment_context>", "<user_instructions>", "# AGENTS.md instructions", "<INSTRUCTIONS>",
    "<permissions instructions>", "<local-command-caveat>", "Caveat: The messages below",
    "<turn_aborted>", "<subagent_notification>",
)


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


@dataclass
class Event:
    kind: str  # user | assistant | thinking | tool_call | tool_result
    text: str = ""
    tool: str = ""
    call_id: str = ""
    is_error: bool = False
    ts: str = ""


@dataclass
class Session:
    agent: str
    id: str
    cwd: str
    updated: float  # unix time
    started: str = ""
    title: str = ""
    source: str = ""  # file path (or db key) the session was read from
    loader: Optional[Callable[["Session"], List[Event]]] = field(default=None, repr=False)
    _events: Optional[List[Event]] = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return AGENT_NAMES.get(self.agent, self.agent)

    def events(self) -> List[Event]:
        if self._events is None:
            self._events = self.loader(self) if self.loader else []
        return self._events

    def user_messages(self) -> List[str]:
        return [e.text for e in self.events() if e.kind == "user"]


def clean_user_text(text: str) -> str:
    text = _RELAY_BLOCK.sub("", _SYSTEM_REMINDER.sub("", text)).strip()
    if text.startswith(_INJECTED_PREFIXES):
        return ""
    return text


def _flatten(content) -> str:
    """Tool results come as strings, lists of content blocks, or JSON-encoded versions of those."""
    if content is None:
        return ""
    if isinstance(content, str):
        s = content.strip()
        if s.startswith("[{") and s.endswith("}]"):
            try:
                return _flatten(json.loads(s))
            except ValueError:
                pass
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if "text" in c:
                    parts.append(str(c["text"]))
                elif c.get("type") in ("image", "input_image"):
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(c))
            else:
                parts.append(str(c))
        return "\n".join(parts)
    if isinstance(content, dict):
        return _flatten(content.get("content") or content.get("text") or json.dumps(content))
    return str(content)


def _pretty_args(args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return args
    if isinstance(args, dict) and len(args) == 1 and isinstance(next(iter(args.values())), str):
        return next(iter(args.values()))  # e.g. {"command": "ls"} -> ls
    return json.dumps(args, indent=2, ensure_ascii=False)


def _read_jsonl(path: Path) -> Iterable[dict]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # a partially written last line while the agent is running
            if isinstance(obj, dict):
                yield obj


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


def _fresh(path: Path, max_age_days: int) -> bool:
    try:
        return time.time() - path.stat().st_mtime < max_age_days * 86400
    except OSError:
        return False


# ---------------------------------------------------------------- Claude Code


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or home() / ".claude") / "projects"


def claude_slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def claude_sessions(project: Optional[str] = None, max_age_days: int = MAX_AGE_DAYS) -> List[Session]:
    root = _claude_dir()
    if not root.is_dir():
        return []
    slugs = ()
    if project:  # the same folder can be spelled /var/... or /private/var/... on macOS
        spellings = {project, os.path.realpath(project), re.sub(r"^/private/", "/", os.path.realpath(project))}
        slugs = tuple(claude_slug(p) for p in spellings)
    out = []
    for d in root.iterdir():
        if not d.is_dir() or (slugs and not d.name.startswith(slugs)):
            continue
        for f in d.glob("*.jsonl"):
            if not _fresh(f, max_age_days):
                continue
            s = _claude_header(f)
            if s:
                out.append(s)
    return out


def _claude_header(path: Path) -> Optional[Session]:
    cwd, title, started, sid = "", "", "", path.stem
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not cwd and '"cwd"' in line:
                try:
                    obj = json.loads(line)
                    cwd, started = obj.get("cwd", ""), obj.get("timestamp", "")
                    sid = obj.get("sessionId", sid)
                except ValueError:
                    pass
            if '"ai-title"' in line or '"type":"summary"' in line:
                try:
                    obj = json.loads(line)
                    title = obj.get("aiTitle") or obj.get("summary") or title
                except ValueError:
                    pass
    if not cwd:
        return None
    return Session("claude", sid, cwd, path.stat().st_mtime, started, title, str(path), _claude_events)


def _claude_events(session: Session) -> List[Event]:
    events: List[Event] = []
    for obj in _read_jsonl(Path(session.source)):
        kind = obj.get("type")
        if kind not in ("user", "assistant") or obj.get("isSidechain") or obj.get("isMeta"):
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        ts = obj.get("timestamp", "")
        for block in content or []:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text":
                text = clean_user_text(block.get("text", "")) if kind == "user" else block.get("text", "").strip()
                if text:
                    events.append(Event(kind, text, ts=ts))
            elif bt == "thinking" and block.get("thinking"):
                events.append(Event("thinking", block["thinking"], ts=ts))
            elif bt == "tool_use":
                events.append(Event("tool_call", _pretty_args(block.get("input")), block.get("name", "tool"),
                                    block.get("id", ""), ts=ts))
            elif bt == "tool_result":
                events.append(Event("tool_result", _flatten(block.get("content")), call_id=block.get("tool_use_id", ""),
                                    is_error=bool(block.get("is_error")), ts=ts))
            elif bt == "image":
                events.append(Event(kind, "[image]", ts=ts))
    return events


# ---------------------------------------------------------------- Codex


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or home() / ".codex")


def codex_sessions(project: Optional[str] = None, max_age_days: int = MAX_AGE_DAYS) -> List[Session]:
    root = _codex_home() / "sessions"
    if not root.is_dir():
        return []
    titles = _codex_titles()
    out = []
    for f in root.rglob("rollout-*.jsonl"):
        if not _fresh(f, max_age_days):
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                meta = json.loads(fh.readline()).get("payload") or {}
        except (ValueError, OSError):
            continue
        source = meta.get("source")
        if isinstance(source, dict) and "subagent" in source:
            continue  # internal reviewer / helper sessions, not user work
        cwd = meta.get("cwd") or ""
        if not cwd or (project and not _within(cwd, project)):
            continue
        sid = meta.get("id") or meta.get("session_id") or f.stem
        out.append(Session("codex", sid, cwd, f.stat().st_mtime, meta.get("timestamp", ""),
                           titles.get(sid, ""), str(f), _codex_events))
    return out


def _codex_titles() -> Dict[str, str]:
    titles: Dict[str, str] = {}
    index = _codex_home() / "session_index.jsonl"
    if index.exists():
        for obj in _read_jsonl(index):
            if obj.get("id") and obj.get("thread_name"):
                titles[obj["id"]] = obj["thread_name"]
    return titles


def _codex_events(session: Session) -> List[Event]:
    events: List[Event] = []
    for obj in _read_jsonl(Path(session.source)):
        if obj.get("type") != "response_item":
            continue
        p = obj.get("payload") or {}
        t, ts = p.get("type"), obj.get("timestamp", "")
        if t == "message" and p.get("role") in ("user", "assistant"):
            role = p["role"]
            text = "\n".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
            text = clean_user_text(text) if role == "user" else text.strip()
            if text:
                events.append(Event(role, text, ts=ts))
        elif t == "reasoning":
            summary = "\n".join(s.get("text", "") for s in p.get("summary") or [] if isinstance(s, dict))
            if summary.strip():
                events.append(Event("thinking", summary.strip(), ts=ts))
        elif t in ("function_call", "custom_tool_call"):
            args = p.get("arguments") if t == "function_call" else p.get("input")
            events.append(Event("tool_call", _pretty_args(args), p.get("name", "tool"), p.get("call_id", ""), ts=ts))
        elif t == "local_shell_call":
            cmd = (p.get("action") or {}).get("command")
            events.append(Event("tool_call", " ".join(cmd) if isinstance(cmd, list) else str(cmd), "shell",
                                p.get("call_id", ""), ts=ts))
        elif t in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
            out = p.get("output")
            if isinstance(out, dict):
                out = out.get("content") or out.get("output") or out
            events.append(Event("tool_result", _flatten(out), call_id=p.get("call_id", ""), ts=ts))
        elif t == "web_search_call":
            events.append(Event("tool_call", _pretty_args(p.get("action") or {}), "web_search", ts=ts))
    return events


# ---------------------------------------------------------------- Cursor


def _cursor_user_dir() -> Path:
    override = os.environ.get("RELAY_CURSOR_USER_DIR")
    return Path(override) if override else home() / "Library/Application Support/Cursor/User"


def _open_db_copy(path: Path) -> Optional[sqlite3.Connection]:
    """Cursor keeps its DB open (WAL mode); read a private copy so we never lock or corrupt it."""
    if not path.exists():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="relay-cursor-"))
    try:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(path) + suffix)
            if src.exists():
                shutil.copy2(src, tmp / ("state.vscdb" + suffix))
        con = sqlite3.connect(str(tmp / "state.vscdb"))
        con.execute("select 1")
        return con
    except (sqlite3.Error, OSError):
        shutil.rmtree(tmp, ignore_errors=True)
        return None


def _kv(con: sqlite3.Connection, table: str, key: str):
    try:
        row = con.execute(f"select value from {table} where key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def cursor_sessions(project: Optional[str] = None, max_age_days: int = MAX_AGE_DAYS) -> List[Session]:
    return _cursor_db_sessions(project, max_age_days) + _cursor_transcript_sessions(project, max_age_days)


def _cursor_db_sessions(project: Optional[str], max_age_days: int) -> List[Session]:
    user_dir = _cursor_user_dir()
    ws_root = user_dir / "workspaceStorage"
    if not ws_root.is_dir():
        return []
    cutoff = time.time() - max_age_days * 86400
    out: List[Session] = []
    global_db = user_dir / "globalStorage" / "state.vscdb"
    for ws in ws_root.iterdir():
        try:
            folder = json.loads((ws / "workspace.json").read_text()).get("folder", "")
        except (OSError, ValueError):
            continue
        if not folder.startswith("file://"):
            continue
        cwd = unquote(urlparse(folder).path)
        if project and not _within(cwd, project):
            continue
        if not _fresh(ws / "state.vscdb", max_age_days):
            continue
        con = _open_db_copy(ws / "state.vscdb")
        if not con:
            continue
        data = _kv(con, "ItemTable", "composer.composerData") or {}
        con.close()
        for c in data.get("allComposers") or []:
            updated = (c.get("lastUpdatedAt") or c.get("createdAt") or 0) / 1000
            cid = c.get("composerId")
            if not cid or updated < cutoff:
                continue
            started = _iso(c["createdAt"] / 1000) if c.get("createdAt") else ""
            out.append(Session("cursor", cid, cwd, updated, started, c.get("name") or "",
                               str(global_db) + "#" + cid, _cursor_db_events))
    return out


def _bubble_events(b: dict) -> List[Event]:
    events: List[Event] = []
    kind = "user" if b.get("type") == 1 else "assistant"
    thinking = b.get("thinking")
    if isinstance(thinking, dict) and thinking.get("text"):
        events.append(Event("thinking", thinking["text"]))
    tool = b.get("toolFormerData")
    if isinstance(tool, dict) and tool.get("name"):
        args = tool.get("rawArgs") or tool.get("params") or ""
        events.append(Event("tool_call", _pretty_args(args), tool["name"], tool.get("toolCallId", "")))
        if tool.get("result") is not None:
            events.append(Event("tool_result", _flatten(tool["result"]), call_id=tool.get("toolCallId", ""),
                                is_error=tool.get("status") == "error"))
    text = (b.get("text") or "").strip()
    if kind == "user":
        text = clean_user_text(text)
    if text:
        events.insert(0 if kind == "user" else len(events), Event(kind, text))
    return events


def _cursor_db_events(session: Session) -> List[Event]:
    db, cid = session.source.split("#", 1)
    con = _open_db_copy(Path(db))
    if not con:
        return []
    try:
        data = _kv(con, "cursorDiskKV", f"composerData:{cid}") or {}
        bubbles = data.get("conversation")  # older format: bubbles stored inline
        if not bubbles:
            bubbles = []
            for header in data.get("fullConversationHeadersOnly") or []:
                b = _kv(con, "cursorDiskKV", f"bubbleId:{cid}:{header.get('bubbleId')}")
                if b:
                    bubbles.append(b)
        events = [e for b in bubbles for e in _bubble_events(b)]
        todos = data.get("todos")
        if todos:  # Cursor keeps its todo list on the composer, not in a tool call
            events.append(Event("tool_call", json.dumps({"todos": todos}), "todo_write"))
        return events
    finally:
        con.close()


def _cursor_transcript_sessions(project: Optional[str], max_age_days: int) -> List[Session]:
    root = home() / ".cursor" / "projects"
    if not root.is_dir():
        return []
    want = project.strip("/").replace("/", "-") if project else None
    out = []
    for d in root.iterdir():
        tdir = d / "agent-transcripts"
        if not tdir.is_dir() or (want and not d.name.startswith(want)):
            continue
        cwd = project or "/" + d.name.replace("-", "/")  # lossy, only used without --project
        for f in list(tdir.glob("*.txt")) + list(tdir.glob("*.jsonl")) + list(tdir.glob("*.md")):
            if _fresh(f, max_age_days):
                out.append(Session("cursor", f.stem, cwd, f.stat().st_mtime, _iso(f.stat().st_mtime), "",
                                   str(f), _cursor_transcript_events))
    return out


_ROLE_LINE = re.compile(r"^(user|assistant|human|ai)\s*:\s*$", re.IGNORECASE)


def _cursor_transcript_events(session: Session) -> List[Event]:
    path = Path(session.source)
    if path.suffix == ".jsonl":
        events = []
        for obj in _read_jsonl(path):
            role = obj.get("role") or obj.get("type")
            msg = obj.get("message", obj)
            text = _flatten(msg.get("content") if isinstance(msg, dict) else msg).strip()
            if role in ("user", "assistant") and text:
                events.append(Event(role, clean_user_text(text) if role == "user" else text))
        return events
    events, kind, buf = [], "assistant", []

    def flush():
        text = "\n".join(buf).strip()
        if text:
            events.append(Event(kind, clean_user_text(text) if kind == "user" else text))

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _ROLE_LINE.match(line.strip())
        if m:
            flush()
            kind, buf = ("user" if m.group(1).lower() in ("user", "human") else "assistant"), []
        else:
            buf.append(line)
    flush()
    return [e for e in events if e.text]


# ---------------------------------------------------------------- discovery

READERS = {"claude": claude_sessions, "codex": codex_sessions, "cursor": cursor_sessions}


def _within(path: str, project: str) -> bool:
    path, project = os.path.realpath(path), os.path.realpath(project)
    return path == project or path.startswith(project.rstrip("/") + "/")


def discover(project: Optional[str] = None, max_age_days: int = MAX_AGE_DAYS) -> List[Session]:
    """All sessions (optionally only those inside `project`), newest first."""
    sessions: List[Session] = []
    for reader in READERS.values():
        try:
            found = reader(project, max_age_days)
        except Exception:  # one broken agent store must never hide the others
            continue
        sessions += [s for s in found if not project or _within(s.cwd, project)]
    return sorted(sessions, key=lambda s: s.updated, reverse=True)
