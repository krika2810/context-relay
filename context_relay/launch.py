"""Open the target agent on a project (macOS)."""
from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

TERMINAL_COMMANDS = {"claude": "claude", "codex": "codex"}


def _applescript_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cursor_prompt_url(prompt: str) -> str:
    """Cursor prompt deeplink: opens Cursor's chat with `prompt` pre-filled (Cursor never auto-sends it)."""
    return "cursor://anysphere.cursor-deeplink/prompt?text=" + quote(prompt, safe="")


def _copy(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True, capture_output=True)


def _launch_cursor(root: Path, prompt: Optional[str]) -> None:
    was_running = subprocess.run(["pgrep", "-x", "Cursor"], capture_output=True).returncode == 0
    proc = subprocess.run(["open", "-a", "Cursor", str(root)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("Cursor is not installed (couldn't open Cursor.app)")
    if not prompt:
        return
    _copy(prompt)  # fallback: if the deeplink is rejected, ⌘V in Cursor's chat still works
    # The deeplink targets the frontmost Cursor window, so let the project window open first.
    time.sleep(1.5 if was_running else 6)
    subprocess.run(["open", cursor_prompt_url(prompt)], capture_output=True)


def launch(agent: str, root: Path, prompt: Optional[str]) -> None:
    if agent == "cursor":
        _launch_cursor(root, prompt)
        return
    cmd = f"cd {shlex.quote(str(root))} && {TERMINAL_COMMANDS[agent]}"
    if prompt:
        cmd += " " + shlex.quote(prompt)
    script = f'tell application "Terminal"\n  activate\n  do script {_applescript_str(cmd)}\nend tell'
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"couldn't open Terminal: {proc.stderr.strip()}")
