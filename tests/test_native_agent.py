import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.model_client import ModelReply, ToolCall
from agent_farm.models import AgentFarmConfig
from agent_farm.native_agent import (
    FINISH_TOOL,
    NativeAgentCancelled,
    NativeAgentError,
    ToolRuntime,
    WEB_RESEARCH_BUDGET_MESSAGE,
    WEB_RESEARCH_CALL_BUDGET,
    run_native_agent,
)


class FakeSession:
    def __init__(self):
        self.turn = 0
        self.tool_results = []

    def send(self, *, prompt=None, tool_results=None, tools=None):
        self.turn += 1
        self.tool_results.append(tool_results or [])
        if self.turn == 1:
            return ModelReply(
                response_id="one",
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="write-1",
                        name="write_file",
                        arguments={"path": "src/result.txt", "content": "native\n"},
                    )
                ],
            )
        return ModelReply(
            response_id="two",
            text="",
            tool_calls=[
                ToolCall(
                    call_id="finish-1",
                    name="finish",
                    arguments={
                        "summary": "Implemented the native result.",
                        "tests": ["No automated test available"],
                        "notes": [],
                    },
                )
            ],
        )


class ResearchBudgetSession:
    def __init__(self):
        self.turn = 0
        self.prompts = []
        self.tool_names = []

    def send(self, *, prompt=None, tool_results=None, tools=None):
        self.turn += 1
        self.prompts.append(prompt)
        self.tool_names.append({tool["name"] for tool in tools or []})
        if self.turn <= WEB_RESEARCH_CALL_BUDGET:
            return ModelReply(
                response_id=f"research-{self.turn}",
                text="",
                tool_calls=[
                    ToolCall(
                        call_id=f"search-{self.turn}",
                        name="web_search",
                        arguments={"query": f"query {self.turn}", "max_results": 1},
                    )
                ],
            )
        return ModelReply(
            response_id="done",
            text="",
            tool_calls=[
                ToolCall(
                    call_id="finish-budget",
                    name="finish",
                    arguments={"summary": "Research complete.", "tests": [], "notes": []},
                )
            ],
        )


class EmptyOnceSession:
    def __init__(self):
        self.turn = 0
        self.prompts = []

    def send(self, *, prompt=None, tool_results=None, tools=None):
        self.turn += 1
        self.prompts.append(prompt)
        if self.turn == 1:
            return ModelReply(response_id="empty", text="", tool_calls=[])
        return ModelReply(
            response_id="done",
            text="",
            tool_calls=[
                ToolCall(
                    call_id="finish-empty-retry",
                    name="finish",
                    arguments={"summary": "Recovered after an empty response.", "tests": [], "notes": []},
                )
            ],
        )


class AnalysisCompletionSession:
    def __init__(self):
        self.turn = 0
        self.tool_names = []
        self.prompts = []

    def send(self, *, prompt=None, tool_results=None, tools=None):
        self.turn += 1
        self.prompts.append(prompt)
        names = {tool["name"] for tool in tools or []}
        self.tool_names.append(names)
        if names == {"finish"}:
            return ModelReply(
                response_id="analysis-finish",
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="finish-analysis",
                        name="finish",
                        arguments={"summary": "Analysis complete.", "tests": [], "notes": []},
                    )
                ],
            )
        return ModelReply(
            response_id=f"analysis-{self.turn}",
            text="",
            tool_calls=[
                ToolCall(
                    call_id=f"list-{self.turn}",
                    name="list_files",
                    arguments={"path": ".", "pattern": "*", "max_results": 1},
                )
            ],
        )


class NativeAgentTests(unittest.TestCase):
    def test_analysis_worker_is_forced_to_finish_after_bounded_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evidence.txt").write_text("evidence\n", encoding="utf-8")
            session = AnalysisCompletionSession()
            result = run_native_agent(
                config=AgentFarmConfig(native_max_turns=12),
                repo_root=root,
                worktree=root,
                prompt="# Worker Task\n\n- No-change analysis: `true`\n",
                system_prompt="System",
                provider="local",
                model="test-model",
                timeout_seconds=None,
                writable=True,
                events_file=root / "events.jsonl",
                terminal_tool=FINISH_TOOL,
                session=session,
            )

            self.assertTrue(result.ok)
            self.assertEqual(session.turn, 8)
            self.assertEqual(session.tool_names[-1], {"finish"})
            self.assertIn("Stop exploring now.", session.prompts[-1])

    def test_empty_model_response_gets_one_continuation_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = EmptyOnceSession()
            result = run_native_agent(
                config=AgentFarmConfig(native_max_turns=4),
                repo_root=root,
                worktree=root,
                prompt="Complete the smoke test.",
                system_prompt="System",
                provider="native",
                model="test-model",
                timeout_seconds=None,
                writable=True,
                events_file=root / "events.jsonl",
                terminal_tool=FINISH_TOOL,
                session=session,
            )

            self.assertTrue(result.ok)
            self.assertEqual(session.turn, 2)
            self.assertEqual(
                session.prompts[1],
                "The previous model response was empty. Continue the task now; "
                "use the available tools or finish with the requested result.",
            )

    def test_web_research_budget_removes_web_tools_and_forces_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = ResearchBudgetSession()
            with patch.object(ToolRuntime, "execute", return_value={"results": []}):
                result = run_native_agent(
                    config=AgentFarmConfig(
                        native_max_turns=20,
                        codex_config_overrides={
                            "sandbox_workspace_write.network_access": True
                        },
                    ),
                    repo_root=root,
                    worktree=root,
                    prompt="Research it",
                    system_prompt="System",
                    provider="deepseek",
                    model="deepseek-v4-flash",
                    timeout_seconds=30,
                    writable=True,
                    events_file=root / "events.jsonl",
                    terminal_tool=FINISH_TOOL,
                    session=session,
                )

            self.assertTrue(result.ok)
            self.assertEqual(session.turn, WEB_RESEARCH_CALL_BUDGET + 1)
            self.assertIn("web_search", session.tool_names[0])
            self.assertNotIn("web_search", session.tool_names[-1])
            self.assertNotIn("fetch_url", session.tool_names[-1])
            self.assertEqual(session.prompts[-1], WEB_RESEARCH_BUDGET_MESSAGE)

    def test_web_tools_require_explicit_network_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            disabled = ToolRuntime(
                worktree=root,
                config=AgentFarmConfig(),
                writable=True,
            )
            enabled = ToolRuntime(
                worktree=root,
                config=AgentFarmConfig(
                    codex_config_overrides={
                        "sandbox_workspace_write.network_access": True
                    }
                ),
                writable=True,
            )
            self.assertNotIn("web_search", {item["name"] for item in disabled.specs()})
            self.assertIn("web_search", {item["name"] for item in enabled.specs()})
            self.assertIn("fetch_url", {item["name"] for item in enabled.specs()})

    def test_tool_runtime_enforces_allowed_and_forbidden_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            runtime = ToolRuntime(
                worktree=root,
                config=AgentFarmConfig(
                    allowed_paths=["src/**"],
                    forbidden_paths=["**/*secret*"],
                ),
                writable=True,
            )
            runtime.execute(
                ToolCall("one", "write_file", {"path": "src/ok.txt", "content": "ok"})
            )
            result = runtime.execute(
                ToolCall("manifest", "read_file", {"path": "src/ok.txt", "start_line": 1, "end_line": 1})
            )
            self.assertEqual(result["capability_manifest"]["capability"], "read")
            self.assertEqual(result["capability_manifest"]["network"], "none")
            self.assertEqual((root / "src/ok.txt").read_text(encoding="utf-8"), "ok")
            with self.assertRaisesRegex(NativeAgentError, "outside allowed paths"):
                runtime.execute(
                    ToolCall("two", "write_file", {"path": "other.txt", "content": "no"})
                )
            with self.assertRaisesRegex(NativeAgentError, "forbidden"):
                runtime.execute(
                    ToolCall("three", "write_file", {"path": "src/secret.txt", "content": "no"})
                )

    def test_repository_search_skips_generated_trees_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "b.py").write_text("needle\n", encoding="utf-8")
            (root / "src" / "a.py").write_text("needle\n", encoding="utf-8")
            for ignored in ("build", "dist", "bin", "obj", "node_modules", ".pytest_cache"):
                directory = root / ignored
                directory.mkdir()
                (directory / "ignored.py").write_text("needle\n", encoding="utf-8")

            runtime = ToolRuntime(worktree=root, config=AgentFarmConfig(), writable=False)
            result = runtime.execute(
                ToolCall(
                    "search",
                    "search_text",
                    {"path": ".", "query": "needle", "max_results": 100},
                )
            )

            self.assertEqual(
                [match["path"] for match in result["matches"]],
                ["src/a.py", "src/b.py"],
            )

    def test_repository_scan_observes_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source.py").write_text("needle\n", encoding="utf-8")
            runtime = ToolRuntime(
                worktree=root,
                config=AgentFarmConfig(),
                writable=False,
                cancel_check=lambda: True,
            )
            with self.assertRaisesRegex(NativeAgentCancelled, "cancelled"):
                runtime.execute(
                    ToolCall(
                        "search",
                        "search_text",
                        {"path": ".", "query": "needle", "max_results": 100},
                    )
                )

    def test_tool_runtime_blocks_symlink_escape_private_network_and_read_only_writes(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            external = Path(outside) / "credential.txt"
            external.write_text("secret")
            try:
                (root / "escape.txt").symlink_to(external)
            except OSError:
                self.skipTest("symlinks are unavailable")
            runtime = ToolRuntime(
                worktree=root,
                config=AgentFarmConfig(
                    allowed_paths=["**"],
                    sandbox="read-only",
                    codex_config_overrides={
                        "sandbox_workspace_write.network_access": True
                    },
                ),
                writable=True,
            )
            with self.assertRaises(ValueError):
                runtime.execute(
                    ToolCall(
                        "read-escape",
                        "read_file",
                        {"path": "escape.txt", "start_line": 1, "end_line": 1},
                    )
                )
            with self.assertRaisesRegex(NativeAgentError, "read-only"):
                runtime.execute(
                    ToolCall("write-readonly", "write_file", {"path": "new.txt", "content": "no"})
                )
            for url in ("http://127.0.0.1/admin", "http://localhost/", "http://169.254.169.254/"):
                with self.assertRaisesRegex(NativeAgentError, "blocked"):
                    runtime.execute(
                        ToolCall("fetch-private", "fetch_url", {"url": url, "max_characters": 1000})
                    )

    def test_native_loop_executes_tools_and_records_typed_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            events = root / "events.jsonl"
            fake = FakeSession()
            result = run_native_agent(
                config=AgentFarmConfig(
                    worker_model="budget-model",
                    worker_provider="local",
                    allowed_paths=["src/**"],
                ),
                repo_root=root,
                worktree=root,
                prompt="Implement it",
                system_prompt="System",
                provider="local",
                model="budget-model",
                timeout_seconds=30,
                writable=True,
                events_file=events,
                terminal_tool=FINISH_TOOL,
                session=fake,
            )

            self.assertTrue(result.ok)
            self.assertEqual((root / "src/result.txt").read_text(encoding="utf-8"), "native\n")
            self.assertTrue(fake.tool_results[1])
            event_types = [
                json.loads(line)["type"] for line in events.read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("item.started", event_types)
            self.assertIn("tool.capability", event_types)
            self.assertIn("item.completed", event_types)
            self.assertEqual(event_types[-1], "agent.completed")

    def test_native_loop_stops_before_model_request_when_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = FakeSession()
            result = run_native_agent(
                config=AgentFarmConfig(),
                repo_root=root,
                worktree=root,
                prompt="Do not run",
                system_prompt="System",
                provider="local",
                model="budget-model",
                timeout_seconds=None,
                writable=False,
                events_file=root / "events.jsonl",
                terminal_tool=FINISH_TOOL,
                session=fake,
                cancel_check=lambda: True,
            )

            self.assertTrue(result.cancelled)
            self.assertFalse(result.ok)
            self.assertEqual(fake.turn, 0)
            event_types = [
                json.loads(line)["type"]
                for line in (root / "events.jsonl").read_text().splitlines()
            ]
            self.assertEqual(event_types[-1], "agent.cancelled")


if __name__ == "__main__":
    unittest.main()
