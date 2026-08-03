from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent_farm.approvals import ApprovalBroker, ApprovalError
from agent_farm.model_client import ToolCall
from agent_farm.models import AgentFarmConfig
from agent_farm.native_agent import NativeAgentError, ToolRuntime


class ApprovalBrokerTests(unittest.TestCase):
    def test_request_blocks_until_allow_once_and_emits_lifecycle(self):
        broker = ApprovalBroker()
        events: list[dict] = []
        result: list[str] = []

        thread = threading.Thread(
            target=lambda: result.append(
                broker.request(
                    job_kind="farm",
                    job_id="farm-1",
                    request={
                        "kind": "file_write",
                        "scope": "filesystem",
                        "tool_name": "write_file",
                        "title": "Allow write?",
                        "description": "Write src/result.txt",
                        "details": {"path": "src/result.txt"},
                    },
                    event_callback=events.append,
                )
            )
        )
        thread.start()
        deadline = time.monotonic() + 2
        while not broker.list(status="pending") and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertTrue(thread.is_alive(), "the protected action did not pause")
        pending = broker.list(status="pending")
        self.assertEqual(len(pending), 1)
        broker.respond(pending[0]["approval_id"], "allow_once")
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["allow_once"])
        self.assertEqual(
            [event["type"] for event in events],
            ["approval.requested", "approval.resolved"],
        )

    def test_allow_session_grants_matching_scope_without_a_second_prompt(self):
        broker = ApprovalBroker()
        first: list[str] = []
        thread = threading.Thread(
            target=lambda: first.append(
                broker.request(
                    job_kind="farm",
                    job_id="farm-1",
                    request={"scope": "network", "kind": "network"},
                )
            )
        )
        thread.start()
        deadline = time.monotonic() + 2
        while not broker.list(status="pending") and time.monotonic() < deadline:
            time.sleep(0.01)
        pending = broker.list(status="pending")
        broker.respond(pending[0]["approval_id"], "allow_session")
        thread.join(timeout=2)

        second = broker.request(
            job_kind="farm",
            job_id="farm-1",
            request={"scope": "network", "kind": "network"},
        )
        self.assertEqual(first, ["allow_session"])
        self.assertEqual(second, "allow_session")
        self.assertEqual(len(broker.list()), 1)

    def test_close_cancels_waiting_requests(self):
        broker = ApprovalBroker()
        result: list[str] = []
        thread = threading.Thread(
            target=lambda: result.append(
                broker.request(
                    job_kind="plan",
                    job_id="plan-1",
                    request={"scope": "network", "kind": "network"},
                )
            )
        )
        thread.start()
        deadline = time.monotonic() + 2
        while not broker.list(status="pending") and time.monotonic() < deadline:
            time.sleep(0.01)
        broker.close()
        thread.join(timeout=2)
        self.assertEqual(result, ["cancel"])
        with self.assertRaises(ApprovalError):
            broker.request(
                job_kind="plan",
                job_id="plan-2",
                request={"scope": "network", "kind": "network"},
            )


class ToolApprovalTests(unittest.TestCase):
    def test_on_request_denial_prevents_file_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            requests: list[dict] = []
            runtime = ToolRuntime(
                worktree=root,
                config=AgentFarmConfig(
                    allowed_paths=["src/**"],
                    approval_policy="on-request",
                ),
                writable=True,
                approval_callback=lambda request: requests.append(request) or "deny",
            )

            with self.assertRaisesRegex(NativeAgentError, "User denied write_file"):
                runtime.execute(
                    ToolCall(
                        "write-1",
                        "write_file",
                        {"path": "src/result.txt", "content": "blocked"},
                    )
                )
            self.assertFalse((root / "src/result.txt").exists())
            self.assertEqual(requests[0]["kind"], "file_write")
            self.assertEqual(requests[0]["details"]["path"], "src/result.txt")

    def test_on_request_allow_executes_file_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            runtime = ToolRuntime(
                worktree=root,
                config=AgentFarmConfig(
                    allowed_paths=["src/**"],
                    approval_policy="on-request",
                ),
                writable=True,
                approval_callback=lambda request: "allow_once",
            )
            runtime.execute(
                ToolCall(
                    "write-1",
                    "write_file",
                    {"path": "src/result.txt", "content": "allowed"},
                )
            )
            self.assertEqual((root / "src/result.txt").read_text(), "allowed")


if __name__ == "__main__":
    unittest.main()
