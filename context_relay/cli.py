"""relay: move a coding session between Claude Code, Codex and Cursor with full context.

Normally you never run this by hand: the menu bar app and the agents' session-start hooks
call it. The commands are here for scripting and debugging.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from typing import List, Optional
from urllib.parse import unquote, urlparse

from . import __version__, gitinfo, handoff, hooks
from .launch import launch
from .sources import AGENT_NAMES, discover
from .transcript import ago, render_brief, stats

AGENTS = list(AGENT_NAMES)


def info(msg: str = "") -> None:
    print(msg, file=sys.stderr)


def emit(obj) -> None:
    print(json.dumps(obj, indent=2))


def _log(line: str) -> None:
    try:
        log = hooks.relay_home() / "logs" / "hook.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > 1_000_000:
            log.replace(log.with_suffix(".log.1"))
        with open(log, "a") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line.rstrip() + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- hook (called by the agents)


def _hook_cwd(data: dict) -> str:
    cwd = data.get("cwd")
    if not cwd:
        roots = data.get("workspace_roots") or []
        cwd = roots[0] if roots else os.getcwd()
    if cwd.startswith("file://"):
        cwd = unquote(urlparse(cwd).path)
    return cwd


def cmd_hook(args) -> int:
    """Session-start hook. Must never break the agent: always exit 0, log problems instead."""
    try:
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if data.get("source") in ("resume", "compact"):
            return 0  # same conversation continuing; nothing new to hand off
        cwd = _hook_cwd(data)
        ids = {str(data[k]) for k in ("session_id", "conversation_id") if data.get(k)}
        h = handoff.prepare(cwd, args.agent, ids)
        if h:
            print(hooks.output(args.agent, h.brief))
            _log(f"{args.agent}: injected {h.session.agent} session {h.session.id} into {cwd}")
        else:
            _log(f"{args.agent}: nothing to hand off in {cwd}")
    except Exception:
        _log(f"{args.agent}: error\n{traceback.format_exc()}")
    return 0


# ---------------------------------------------------------------- user-facing commands


def cmd_continue(args) -> int:
    root = gitinfo.project_root(args.project or os.getcwd())
    try:
        h = handoff.prepare(root, args.agent, force=True)
        if not h:
            raise RuntimeError(f"No agent sessions found for {root}")
        if args.agent == "cursor":
            handoff.write_cursor_rule(h)
            prompt = handoff.cursor_prompt(h)
        else:
            prompt = handoff.kickoff_prompt(h)
        if not args.no_launch:
            launch(args.agent, root, prompt)
    except RuntimeError as e:
        if args.json:
            emit({"ok": False, "error": str(e)})
        else:
            info(f"relay: {e}")
        return 1
    result = {"ok": True, "from_agent": h.session.agent, "transcript": str(h.transcript),
              "context": str(h.context), "launched": not args.no_launch}
    if args.json:
        emit(result)
    else:
        info(f"relay: handed off {h.session.name} session ({h.session.id}) to {AGENT_NAMES[args.agent]}")
        info(f"  context:    {h.context}")
        info(f"  transcript: {h.transcript}")
    return 0


def cmd_pull(args) -> int:
    """For an agent that is already running: fetch the handoff meant for it, if any."""
    root = gitinfo.project_root(args.project or os.getcwd())
    sessions = discover(str(root))
    # The newest session of the asking agent is the conversation that is running this command.
    current = next((s for s in sessions if s.agent == args.target), None)
    exclude = {current.id} if current and sessions and sessions[0] is current else set()
    try:
        h = handoff.prepare(root, args.target, exclude)
        brief = h.brief if h else None
    except OSError:  # read-only sandbox: still hand over the brief, just without the files
        session = handoff.candidate(root, args.target, exclude)
        brief = session and render_brief(session, f"(not saved: read-only sandbox; source log: {session.source})",
                                         gitinfo.summary_lines(root), args.target)
    if not brief:
        print(f"No handoff: no other agent worked in {root} more recently than {AGENT_NAMES[args.target]}.")
        return 0
    print(brief)
    return 0


def cmd_context(args) -> int:
    root = gitinfo.project_root(args.project or os.getcwd())
    h = handoff.prepare(root, args.target, force=True)
    if not h:
        info(f"relay: no agent sessions found for {root}")
        return 1
    print(h.brief)
    info(f"\nrelay: wrote {h.context} and {h.transcript}")
    return 0


def cmd_status(args) -> int:
    data = {"version": 1, "hooks": hooks.status(), "projects": handoff.projects()}
    if args.json:
        emit(data)
        return 0
    on = [AGENT_NAMES[a] for a, v in data["hooks"].items() if v]
    print(f"Auto-handoff hooks: {', '.join(on) if on else 'off'}  (relay hooks install)")
    for p in data["projects"]:
        print(f"\n{p['name']}  {p['path']}")
        for s in p["sessions"]:
            print(f"  {AGENT_NAMES[s['agent']]:<12} {s['updated'][:16].replace('T', ' ')}  {s['title']}")
    return 0


def cmd_sessions(args) -> int:
    root = gitinfo.project_root(args.project or os.getcwd())
    rows = []
    for s in discover(str(root)):
        rows.append({"agent": s.agent, "id": s.id, "title": handoff.title(s), "updated": s.updated,
                     "cwd": s.cwd, "source": s.source, **stats(s.events())})
    if args.json:
        emit(rows)
        return 0
    if not rows:
        info(f"relay: no sessions found for {root}")
    for r in rows:
        print(f"{AGENT_NAMES[r['agent']]:<12} {ago(r['updated']):>8}  {r['messages']:>4} msgs "
              f"{r['tool_calls']:>4} tools  {r['title']}")
    return 0


def cmd_hooks(args) -> int:
    try:
        result = {"install": hooks.install, "uninstall": hooks.uninstall, "status": hooks.status}[args.action]()
    except (ValueError, OSError) as e:
        if args.json:
            emit({"ok": False, "error": str(e)})
        else:
            info(f"relay: {e}")
        return 1
    if args.json:
        emit({"ok": True, "hooks": result})
    else:
        for agent, on in result.items():
            print(f"{AGENT_NAMES[agent]:<12} {'on' if on else 'off'}")
    return 0


def cmd_ui(args) -> int:
    from .web import serve
    serve(args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="relay", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"relay {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    c = sub.add_parser("continue", help="hand the latest session in a project to another agent and open it")
    c.add_argument("agent", choices=AGENTS)
    c.add_argument("--project", help="project directory (default: current directory)")
    c.add_argument("--no-launch", action="store_true", help="prepare the handoff but don't open the agent")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_continue)

    x = sub.add_parser("context", help="print the handoff brief for a project (and write .relay/)")
    x.add_argument("--project")
    x.add_argument("--for", dest="target", choices=AGENTS, help="agent that will receive it")
    x.set_defaults(func=cmd_context)

    pl = sub.add_parser("pull", help="print the handoff for an already-running agent, if another agent was last")
    pl.add_argument("--for", dest="target", choices=AGENTS, required=True)
    pl.add_argument("--project")
    pl.set_defaults(func=cmd_pull)

    s = sub.add_parser("status", help="recent projects and which agent worked on them")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    ss = sub.add_parser("sessions", help="list agent sessions for a project")
    ss.add_argument("--project")
    ss.add_argument("--json", action="store_true")
    ss.set_defaults(func=cmd_sessions)

    h = sub.add_parser("hooks", help="turn automatic handoff on/off in Claude Code, Codex and Cursor")
    h.add_argument("action", choices=["install", "uninstall", "status"])
    h.add_argument("--json", action="store_true")
    h.set_defaults(func=cmd_hooks)

    u = sub.add_parser("ui", help="serve the Context Relay window as a local web page")
    u.add_argument("--port", type=int, default=47821)
    u.set_defaults(func=cmd_ui)

    k = sub.add_parser("hook", help=argparse.SUPPRESS)  # invoked by the agents at session start
    k.add_argument("agent", choices=AGENTS)
    k.set_defaults(func=cmd_hook)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
