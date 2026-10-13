"""Read repository state from Git (all functions tolerate non-git directories)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
OWNED_PATHS = (".relay/", ".cursor/rules/relay-handoff.mdc")  # relay's own files


def git(root, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                              errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def project_root(path) -> Path:
    """The git work tree containing `path`, or `path` itself outside git."""
    out = git(path, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out else Path(path).resolve()


def head(root) -> Optional[str]:
    return (git(root, "rev-parse", "--verify", "-q", "HEAD") or "").strip() or None


def branch(root) -> Optional[str]:
    if git(root, "rev-parse", "--git-dir") is None:
        return None
    return (git(root, "symbolic-ref", "--short", "-q", "HEAD") or "").strip() or "(detached HEAD)"


def subject(root, rev: str) -> str:
    return (git(root, "log", "-1", "--format=%s", rev) or "").strip()


def _label(xy: str) -> str:
    if xy == "??":
        return "untracked"
    if xy in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}:
        return "conflict"
    for code, name in (("D", "deleted"), ("R", "renamed"), ("A", "added"), ("C", "added")):
        if code in xy:
            return name
    return "modified"


def _numstat(root, base: str) -> Dict[str, Tuple[str, str]]:
    parts = (git(root, "diff", base, "--numstat", "-M", "-z") or "").split("\0")
    stats, i = {}, 0
    while i < len(parts):
        fields = parts[i].split("\t")
        i += 1
        if len(fields) != 3:
            continue
        added, removed, path = fields
        if not path:  # rename: old and new paths follow
            path = parts[i + 1] if i + 1 < len(parts) else ""
            i += 2
        stats[path] = (added, removed)
    return stats


def changed_files(root) -> Optional[List[Tuple[str, str, str]]]:
    """[(status, path, '+a/-r')] for uncommitted changes, or None outside git."""
    out = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if out is None:
        return None
    stats = _numstat(root, head(root) or EMPTY_TREE)
    parts, i, changes = out.split("\0"), 0, []
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            i += 1  # skip the original path
        if path.startswith(OWNED_PATHS):
            continue
        a, r = stats.get(path, ("", ""))
        changes.append((_label(xy), path, f"+{a}/-{r}" if a.isdigit() and r.isdigit() else ""))
    return changes


def summary_lines(root) -> List[str]:
    """Short Markdown bullet list describing the repo state, for the injected brief."""
    br = branch(root)
    if br is None:
        return []
    rev = head(root)
    lines = [f"- Branch `{br}`" + (f" @ `{rev[:8]}` — {subject(root, rev)}" if rev else " (no commits yet)")]
    changes = changed_files(root) or []
    if not changes:
        lines.append("- Working tree clean")
    else:
        lines.append(f"- {len(changes)} uncommitted file(s):")
        for status, path, stat in changes[:30]:
            lines.append(f"  - {status} `{path}` {stat}".rstrip())
        if len(changes) > 30:
            lines.append(f"  - … and {len(changes) - 30} more")
    return lines
