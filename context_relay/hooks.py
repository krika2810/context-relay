"""Install/remove the session-start hooks that inject handoffs automatically.

- Claude Code: ~/.claude/settings.json  -> hooks.SessionStart
- Codex:       ~/.codex/hooks.json      -> hooks.SessionStart (same schema as Claude Code)
- Cursor:      ~/.cursor/hooks.json     -> hooks.sessionStart

Relay's entries are recognized by the launcher path, so the user's other hooks are never touched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict

from .sources import home

MARKER = "context-relay"


def relay_home() -> Path:
    return Path(os.environ.get("RELAY_HOME") or home() / ".relay")


def launcher() -> Path:
    return relay_home() / "bin" / MARKER


def _config_paths() -> Dict[str, Path]:
    return {
        "claude": Path(os.environ.get("CLAUDE_CONFIG_DIR") or home() / ".claude") / "settings.json",
        "codex": Path(os.environ.get("CODEX_HOME") or home() / ".codex") / "hooks.json",
        "cursor": home() / ".cursor" / "hooks.json",
    }


def write_launcher() -> Path:
    """A stable path for hooks to call, pointing at whichever engine copy installed it."""
    path = launcher()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine_dir = Path(__file__).resolve().parent.parent
    python = sys.executable or "/usr/bin/python3"
    path.write_text(
        "#!/bin/sh\n"
        "# Installed by Context Relay. Re-run `relay hooks install` if the engine moves.\n"
        f'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="{engine_dir}" exec "{python}" -m context_relay "$@"\n'
    )
    path.chmod(0o755)
    return path


def _load(path: Path) -> dict:
    if not path.exists() or not path.read_text().strip():
        return {}
    data = json.loads(path.read_text())  # refuse to touch a file we can't parse
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".relay-backup")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".relay-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def _is_ours(entry) -> bool:
    return MARKER in json.dumps(entry)


# Codex only runs hooks the user has reviewed in its terminal UI, which never appears when Codex is
# driven by another app (e.g. T3 Code). Its global AGENTS.md is always read, so it also gets a
# standing instruction to pull the handoff itself.
BLOCK_START = "<!-- context-relay:start -->"
BLOCK_END = "<!-- context-relay:end -->"
_BLOCK_RE = re.compile(r"\n*" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.DOTALL)


def _codex_agents_md() -> Path:
    return Path(os.environ.get("CODEX_HOME") or home() / ".codex") / "AGENTS.md"


def _codex_block() -> str:
    return (
        f"{BLOCK_START}\n"
        "## Context Relay: handoffs from other coding agents\n\n"
        "At the start of a new conversation, unless your context already contains a `<context-relay>` block, "
        "run this command once in the project directory:\n\n"
        f'    "{launcher()}" pull --for codex\n\n'
        "If it prints a handoff, another coding agent (Claude Code or Cursor) worked in this project before you: "
        "read the handoff and the transcript it points to, and continue that work when the user asks. "
        'If it prints "No handoff", carry on normally.\n'
        f"{BLOCK_END}\n"
    )


def _set_codex_block(enabled: bool) -> None:
    path = _codex_agents_md()
    text = path.read_text() if path.exists() else ""
    rest = _BLOCK_RE.sub("", text).rstrip("\n")
    if enabled:
        _save_text(path, (rest + "\n\n" if rest else "") + _codex_block())
    elif rest:
        _save_text(path, rest + "\n")
    elif path.exists():
        path.unlink()


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".relay-tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _command(agent: str) -> str:
    return f'"{launcher()}" hook {agent}'


def _strip(data: dict, event: str) -> dict:
    hooks = data.get("hooks") or {}
    kept = [e for e in hooks.get(event) or [] if not _is_ours(e)]
    if kept:
        hooks[event] = kept
    else:
        hooks.pop(event, None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    return data


def _event(agent: str) -> str:
    return "sessionStart" if agent == "cursor" else "SessionStart"


def install() -> Dict[str, bool]:
    write_launcher()
    for agent, path in _config_paths().items():
        data = _strip(_load(path), _event(agent))
        if agent == "cursor":
            data.setdefault("version", 1)
            entry = {"command": _command(agent)}
        else:
            entry = {"hooks": [{"type": "command", "command": _command(agent), "timeout": 30}]}
        data.setdefault("hooks", {}).setdefault(_event(agent), []).append(entry)
        _save(path, data)
    _set_codex_block(True)
    return status()


def uninstall() -> Dict[str, bool]:
    _set_codex_block(False)
    for agent, path in _config_paths().items():
        if path.exists():
            data = _load(path)
            if any(_is_ours(e) for e in (data.get("hooks") or {}).get(_event(agent)) or []):
                data = _strip(data, _event(agent))
                if agent == "cursor" and list(data) == ["version"]:
                    path.unlink()
                else:
                    _save(path, data)
    return status()


def status() -> Dict[str, bool]:
    result = {}
    for agent, path in _config_paths().items():
        try:
            entries = (_load(path).get("hooks") or {}).get(_event(agent)) or []
        except (ValueError, OSError):
            entries = []
        result[agent] = any(_is_ours(e) for e in entries) and launcher().exists()
    return result


def output(agent: str, brief: str) -> str:
    """Hook stdout in the shape each agent expects."""
    if agent == "cursor":
        return json.dumps({"additional_context": brief, "continue": True})
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": brief}})
