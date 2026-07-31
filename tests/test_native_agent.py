import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.model_client import ModelReply, ToolCall
from agent_farm.models import AgentFarmConfig
from agent_farm.native_agent import (
    FINISH_TOOL,
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


class NativeAgentTests(unittest.TestCase):
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
            self.assertEqual((root / "src/ok.txt").read_text(encoding="utf-8"), "ok")
            with self.assertRaisesRegex(NativeAgentError, "outside allowed paths"):
                runtime.execute(
                    ToolCall("two", "write_file", {"path": "other.txt", "content": "no"})
                )
            with self.assertRaisesRegex(NativeAgentError, "forbidden"):
                runtime.execute(
                    ToolCall("three", "write_file", {"path": "src/secret.txt", "content": "no"})
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
            self.assertIn("item.completed", event_types)
            self.assertEqual(event_types[-1], "agent.completed")


if __name__ == "__main__":
    unittest.main()
