"""End-to-end tests: fake Claude Code / Codex / Cursor stores in a temp HOME, real CLI."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def jsonl(path: Path, rows, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    if mtime:
        os.utime(path, (mtime, mtime))


class RelayTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self._tmp.name))
        self.home = base / "home"
        self.home.mkdir()
        self.project = base / "proj"
        self.project.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        (self.project / "app.py").write_text("print('v1')\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "initial")
        (self.project / "app.py").write_text("print('v2')\n")
        self.now = time.time()

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.project, check=True, capture_output=True, text=True).stdout

    def relay(self, *args, stdin=None, expect=0):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX_", "RELAY_"))}
        env.update(HOME=str(self.home), PYTHONPATH=str(ROOT))
        proc = subprocess.run([sys.executable, "-m", "context_relay", *args], cwd=self.project, env=env,
                              input=stdin, capture_output=True, text=True)
        self.assertEqual(proc.returncode, expect, proc.stderr + proc.stdout)
        return proc

    def hook(self, agent, **data):
        payload = {"cwd": str(self.project), "hook_event_name": "SessionStart", "source": "startup", **data}
        out = self.relay("hook", agent, stdin=json.dumps(payload)).stdout.strip()
        if not out:
            return None
        obj = json.loads(out)
        return obj["additional_context"] if agent == "cursor" else obj["hookSpecificOutput"]["additionalContext"]

    # ------------------------------------------------------------ fake agent stores

    def claude_session(self, sid="claude-1", age=600, cwd=None):
        cwd = str(cwd or self.project)
        slug = "".join(c if c.isalnum() else "-" for c in cwd)
        base = {"cwd": cwd, "sessionId": sid, "timestamp": "2026-10-13T10:00:00Z"}
        rows = [
            {**base, "type": "user", "message": {"role": "user", "content":
                "Make the app print v2\n<system-reminder>internal</system-reminder>"}},
            {**base, "type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "Need to edit app.py"},
                {"type": "text", "text": "I'll update app.py."},
                {"type": "tool_use", "id": "t1", "name": "Edit",
                 "input": {"file_path": f"{cwd}/app.py", "old_string": "v1", "new_string": "v2"}},
                {"type": "tool_use", "id": "t2", "name": "TodoWrite", "input": {"todos": [
                    {"content": "Print v2", "status": "completed", "activeForm": "x"},
                    {"content": "Add tests for app", "status": "pending", "activeForm": "y"}]}},
            ]}},
            {**base, "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "File updated"},
                {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "Todos ok"}]}]}},
            {**base, "type": "user", "isSidechain": True, "message": {"role": "user", "content": "SIDECHAIN"}},
            {**base, "type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Done: app prints v2. Next: tests."}]}},
            {"type": "ai-title", "aiTitle": "Print v2", "sessionId": sid},
        ]
        jsonl(self.home / ".claude/projects" / slug / f"{sid}.jsonl", rows, self.now - age)

    def codex_session(self, sid="codex-1", age=300, cwd=None, subagent=False, extra_user=None):
        cwd = str(cwd or self.project)
        meta = {"id": sid, "cwd": cwd, "timestamp": "2026-10-13T11:00:00Z",
                "source": {"subagent": {"other": "guardian"}} if subagent else "cli"}
        msg = lambda role, text: {"type": "response_item", "payload": {
            "type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}}
        rows = [
            {"type": "session_meta", "payload": meta},
            msg("developer", "<permissions instructions>secret</permissions instructions>"),
            msg("user", "<environment_context>\n  <cwd>x</cwd>\n</environment_context>"),
            msg("user", extra_user or "Add a --verbose flag"),
            {"type": "response_item", "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Plan the flag"}]}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c1",
                "input": "*** Begin Patch\n*** Update File: cli.py\n@@\n+verbose = True\n*** End Patch"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c1",
                "output": [{"type": "input_text", "text": "Success. Updated cli.py"}]}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "update_plan", "call_id": "c2",
                "arguments": json.dumps({"plan": [{"step": "Add flag", "status": "completed"},
                                                  {"step": "Document flag", "status": "in_progress"}]})}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c2", "output": "Plan updated"}},
            msg("assistant", "Added --verbose; documenting next."),
        ]
        jsonl(self.home / ".codex/sessions/2026/10/13" / f"rollout-2026-10-13T11-00-00-{sid}.jsonl", rows, self.now - age)

    def cursor_session(self, cid="cursor-1", age=100):
        user_dir = self.home / "Library/Application Support/Cursor/User"
        ws = user_dir / "workspaceStorage/abc123"
        ws.mkdir(parents=True)
        (ws / "workspace.json").write_text(json.dumps({"folder": "file://" + str(self.project).replace(" ", "%20")}))
        updated_ms = int((self.now - age) * 1000)
        con = sqlite3.connect(str(ws / "state.vscdb"))
        con.execute("create table ItemTable (key text unique, value blob)")
        con.execute("insert into ItemTable values (?, ?)", ("composer.composerData", json.dumps({"allComposers": [
            {"composerId": cid, "name": "Refactor logging", "createdAt": updated_ms - 1000, "lastUpdatedAt": updated_ms}]})))
        con.commit()
        con.close()
        (user_dir / "globalStorage").mkdir(parents=True)
        con = sqlite3.connect(str(user_dir / "globalStorage/state.vscdb"))
        con.execute("create table cursorDiskKV (key text unique, value blob)")
        bubbles = [
            ("b1", {"type": 1, "text": "Refactor logging to use structlog"}),
            ("b2", {"type": 2, "text": "", "toolFormerData": {"name": "edit_file", "toolCallId": "x1",
                    "rawArgs": json.dumps({"target_file": "log.py", "code_edit": "import structlog"}),
                    "result": json.dumps({"diff": "+import structlog"}), "status": "completed"}}),
            ("b3", {"type": 2, "text": "Logging now uses structlog.", "thinking": {"text": "consider handlers"}}),
        ]
        con.execute("insert into cursorDiskKV values (?, ?)", (f"composerData:{cid}", json.dumps({
            "composerId": cid, "fullConversationHeadersOnly": [{"bubbleId": b, "type": d["type"]} for b, d in bubbles],
            "todos": [{"content": "Remove old logger", "status": "pending"}]})))
        for b, d in bubbles:
            con.execute("insert into cursorDiskKV values (?, ?)", (f"bubbleId:{cid}:{b}", json.dumps(d)))
        con.commit()
        con.close()


class DiscoveryTests(RelayTestCase):
    def test_sessions_from_all_agents_filtered_to_project(self):
        self.claude_session()
        self.codex_session()
        self.cursor_session()
        self.codex_session("guardian", subagent=True)
        other = self.project.parent / "other"
        other.mkdir()
        self.codex_session("elsewhere", cwd=other)
        rows = json.loads(self.relay("sessions", "--json").stdout)
        self.assertEqual([r["agent"] for r in rows], ["cursor", "codex", "claude"])
        by_agent = {r["agent"]: r for r in rows}
        self.assertEqual(by_agent["claude"]["tool_calls"], 2)
        self.assertEqual(by_agent["claude"]["title"], "Print v2")
        self.assertEqual(by_agent["cursor"]["title"], "Refactor logging")
        self.assertEqual(by_agent["codex"]["title"], "Add a --verbose flag")

    def test_status_json_for_app(self):
        self.claude_session()
        self.codex_session()
        data = json.loads(self.relay("status", "--json").stdout)
        self.assertEqual(data["hooks"], {"claude": False, "codex": False, "cursor": False})
        proj = data["projects"][0]
        self.assertEqual(proj["path"], str(self.project))
        self.assertEqual(proj["last_agent"], "codex")
        self.assertEqual(proj["branch"], "main")
        self.assertEqual(proj["changed_files"], 1)
        self.assertEqual([s["agent"] for s in proj["sessions"]], ["codex", "claude"])


class HookTests(RelayTestCase):
    def test_codex_receives_claude_session_with_everything(self):
        self.claude_session()
        ctx = self.hook("codex", session_id="new")
        self.assertIn("<context-relay>", ctx)
        self.assertIn("continuing from Claude Code", ctx)
        self.assertIn("Make the app print v2", ctx)
        self.assertNotIn("internal", ctx)  # system reminders stripped
        self.assertIn("- [ ] Add tests for app", ctx)
        self.assertIn("- [x] Print v2", ctx)
        self.assertIn("`app.py`", ctx)  # edited file, relative
        self.assertIn("modified `app.py`", ctx)  # live git state
        self.assertIn("Done: app prints v2", ctx)

        transcript = next((self.project / ".relay/transcripts").glob("claude-*.md")).read_text()
        for expected in ("Make the app print v2", "Need to edit app.py", "Tool call: `Edit`", "File updated",
                         "Result: `TodoWrite`", "Todos ok", "Done: app prints v2"):
            self.assertIn(expected, transcript)
        self.assertNotIn("SIDECHAIN", transcript)
        self.assertEqual((self.project / ".relay/.gitignore").read_text().strip().splitlines()[-1], "*")
        self.assertEqual(self.git("status", "--porcelain", "--", ".relay"), "")  # ignored by git

    def test_claude_receives_codex_session(self):
        self.claude_session(age=900)
        self.codex_session(age=60)
        ctx = self.hook("claude", session_id="new")
        self.assertIn("continuing from Codex", ctx)
        self.assertIn("Add a --verbose flag", ctx)
        self.assertNotIn("environment_context", ctx)
        self.assertIn("- [~] Document flag", ctx)
        self.assertIn("`cli.py`", ctx)
        transcript = next((self.project / ".relay/transcripts").glob("codex-*.md")).read_text()
        for expected in ("Plan the flag", "Tool call: `apply_patch`", "Success. Updated cli.py", "Plan updated"):
            self.assertIn(expected, transcript)
        self.assertNotIn("secret", transcript)  # developer/system prompts are not user content

    def test_cursor_round_trip(self):
        self.codex_session(age=600)
        ctx = self.hook("cursor", conversation_id="new", cwd=None, workspace_roots=[str(self.project)])
        self.assertIn("continuing from Codex", ctx)

        self.cursor_session(age=10)
        ctx = self.hook("claude", session_id="new")
        self.assertIn("continuing from Cursor", ctx)
        self.assertIn("Refactor logging to use structlog", ctx)
        self.assertIn("- [ ] Remove old logger", ctx)
        self.assertIn("`log.py`", ctx)
        transcript = next((self.project / ".relay/transcripts").glob("cursor-*.md")).read_text()
        for expected in ("Tool call: `edit_file`", "+import structlog", "consider handlers", "Logging now uses structlog."):
            self.assertIn(expected, transcript)

    def test_no_handoff_when_same_agent_was_last(self):
        self.codex_session(age=900)
        self.claude_session(age=60)
        self.assertIsNone(self.hook("claude", session_id="new"))

    def test_current_session_is_excluded(self):
        self.claude_session(age=900)
        self.codex_session("codex-now", age=5)  # the session the hook is starting for
        ctx = self.hook("codex", session_id="codex-now")
        self.assertIn("continuing from Claude Code", ctx)

    def test_resume_and_compact_are_ignored(self):
        self.claude_session()
        self.assertIsNone(self.hook("codex", source="resume"))
        self.assertIsNone(self.hook("codex", source="compact"))

    def test_previous_injection_is_not_nested(self):
        self.codex_session(age=60, extra_user="<context-relay>\nOLD HANDOFF\n</context-relay>\nNow add docs")
        ctx = self.hook("claude", session_id="new")
        self.assertNotIn("OLD HANDOFF", ctx)
        self.assertIn("Now add docs", ctx)

    def test_old_sessions_are_not_handed_off(self):
        self.claude_session(age=20 * 86400)
        self.assertIsNone(self.hook("codex", session_id="new"))

    def test_hook_never_fails(self):
        out = self.relay("hook", "claude", stdin="not json").stdout
        self.assertEqual(out, "")
        self.assertIn("error", (self.home / ".relay/logs/hook.log").read_text())
        self.assertEqual(self.relay("hook", "codex", stdin="").stdout, "")

    def test_brief_stays_within_hook_limits(self):
        long_msgs = [f"request {i} " + "x" * 800 for i in range(60)]
        cwd = str(self.project)
        slug = "".join(c if c.isalnum() else "-" for c in cwd)
        rows = [{"cwd": cwd, "sessionId": "big", "type": "user", "message": {"role": "user", "content": m}}
                for m in long_msgs]
        jsonl(self.home / ".claude/projects" / slug / "big.jsonl", rows, self.now - 30)
        ctx = self.hook("codex", session_id="new")
        self.assertLessEqual(len(ctx), 9000)
        self.assertIn("request 0 ", ctx)
        self.assertIn("request 59 ", ctx)
        self.assertIn("more message(s) in the transcript", ctx)
        transcript = next((self.project / ".relay/transcripts").glob("claude-*.md")).read_text()
        self.assertEqual(transcript.count("x" * 800), 60)  # transcript keeps everything


class PullTests(RelayTestCase):
    def test_running_codex_pulls_claude_handoff(self):
        self.claude_session(age=900)
        self.codex_session("codex-asking", age=5)  # the Codex conversation running `relay pull`
        out = self.relay("pull", "--for", "codex").stdout
        self.assertIn("continuing from Claude Code", out)
        self.assertTrue((self.project / ".relay/CONTEXT.md").exists())

    def test_no_handoff_when_target_was_last_before_this_chat(self):
        self.claude_session(age=900)
        self.codex_session("codex-earlier", age=300)
        self.codex_session("codex-asking", age=5)
        self.assertIn("No handoff", self.relay("pull", "--for", "codex").stdout)


class HooksInstallTests(RelayTestCase):
    def test_codex_agents_md_block(self):
        agents_md = self.home / ".codex/AGENTS.md"
        agents_md.parent.mkdir(parents=True)
        agents_md.write_text("# My rules\nBe terse.\n")
        self.relay("hooks", "install")
        self.relay("hooks", "install")
        text = agents_md.read_text()
        self.assertTrue(text.startswith("# My rules\nBe terse.\n"))
        self.assertEqual(text.count("<!-- context-relay:start -->"), 1)
        self.assertIn("pull --for codex", text)
        self.relay("hooks", "uninstall")
        self.assertEqual(agents_md.read_text(), "# My rules\nBe terse.\n")

        agents_md.unlink()
        self.relay("hooks", "install")
        self.relay("hooks", "uninstall")
        self.assertFalse(agents_md.exists())  # relay created it, relay removes it

    def test_install_preserves_user_config_and_uninstall_restores(self):
        claude_settings = self.home / ".claude/settings.json"
        claude_settings.parent.mkdir(parents=True)
        user_hook = {"matcher": "", "hooks": [{"type": "command", "command": "echo mine"}]}
        claude_settings.write_text(json.dumps({"model": "opus", "hooks": {"SessionStart": [user_hook]}}))

        status = json.loads(self.relay("hooks", "install", "--json").stdout)
        self.assertEqual(status, {"ok": True, "hooks": {"claude": True, "codex": True, "cursor": True}})
        self.relay("hooks", "install")  # idempotent
        data = json.loads(claude_settings.read_text())
        self.assertEqual(data["model"], "opus")
        self.assertEqual(len(data["hooks"]["SessionStart"]), 2)
        self.assertEqual(data["hooks"]["SessionStart"][0], user_hook)
        self.assertTrue((self.home / ".claude/settings.json.relay-backup").exists())
        codex = json.loads((self.home / ".codex/hooks.json").read_text())
        self.assertEqual(len(codex["hooks"]["SessionStart"]), 1)
        cursor = json.loads((self.home / ".cursor/hooks.json").read_text())
        self.assertEqual(cursor["version"], 1)
        self.assertIn("hook cursor", cursor["hooks"]["sessionStart"][0]["command"])

        # The installed launcher really works as a hook command.
        self.claude_session()
        launcher = self.home / ".relay/bin/context-relay"
        proc = subprocess.run([str(launcher), "hook", "codex"], input=json.dumps({"cwd": str(self.project)}),
                              capture_output=True, text=True, env={"HOME": str(self.home), "PATH": os.environ["PATH"]})
        self.assertIn("continuing from Claude Code", proc.stdout)

        status = json.loads(self.relay("hooks", "uninstall", "--json").stdout)
        self.assertEqual(status["hooks"], {"claude": False, "codex": False, "cursor": False})
        self.assertEqual(json.loads(claude_settings.read_text()), {"model": "opus", "hooks": {"SessionStart": [user_hook]}})
        self.assertNotIn("hooks", json.loads((self.home / ".codex/hooks.json").read_text()))
        self.assertFalse((self.home / ".cursor/hooks.json").exists())

    def test_refuses_to_overwrite_invalid_json(self):
        path = self.home / ".codex/hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text("{ broken")
        out = json.loads(self.relay("hooks", "install", "--json", expect=1).stdout)
        self.assertFalse(out["ok"])
        self.assertEqual(path.read_text(), "{ broken")


class CursorPromptTests(unittest.TestCase):
    def test_deeplink_encodes_prompt(self):
        sys.path.insert(0, str(ROOT))
        from context_relay.launch import cursor_prompt_url
        url = cursor_prompt_url("Continue where Codex left off & read it")
        self.assertTrue(url.startswith("cursor://anysphere.cursor-deeplink/prompt?text="))
        self.assertIn("Continue%20where%20Codex%20left%20off%20%26%20read%20it", url)

    def test_cursor_prompt_is_plain_text(self):
        sys.path.insert(0, str(ROOT))
        from context_relay import handoff
        from context_relay.sources import Session
        h = handoff.Handoff(Session("codex", "x", "/p", 0), Path("/p"), Path("/p/.relay/t.md"), Path("/p/.relay/C.md"), "")
        text = handoff.cursor_prompt(h)
        self.assertIn("Codex", text)
        self.assertFalse(any(c in text for c in "`/\n#"))


class ContinueTests(RelayTestCase):
    def test_continue_prepares_handoff_and_cursor_rule(self):
        self.claude_session()
        out = json.loads(self.relay("continue", "cursor", "--no-launch", "--json").stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["from_agent"], "claude")
        rule = (self.project / ".cursor/rules/relay-handoff.mdc").read_text()
        self.assertTrue(rule.startswith("---\ndescription:"))
        self.assertIn("alwaysApply: true", rule)
        self.assertIn("continuing from Claude Code", rule)
        self.assertTrue(Path(out["context"]).exists() and Path(out["transcript"]).exists())

        # Handing to anyone else removes the stale Cursor rule.
        self.relay("continue", "codex", "--no-launch", "--json")
        self.assertFalse((self.project / ".cursor/rules/relay-handoff.mdc").exists())

    def test_continue_same_agent_is_allowed(self):
        self.claude_session()
        out = json.loads(self.relay("continue", "claude", "--no-launch", "--json").stdout)
        self.assertEqual(out["from_agent"], "claude")

    def test_continue_without_sessions(self):
        out = json.loads(self.relay("continue", "codex", "--no-launch", "--json", expect=1).stdout)
        self.assertFalse(out["ok"])
        self.assertIn("No agent sessions", out["error"])


if __name__ == "__main__":
    unittest.main()
