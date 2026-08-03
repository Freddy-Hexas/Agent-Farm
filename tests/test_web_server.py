import json
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_farm.plans import WorkerPlan
from agent_farm.web_server import (
    ConsoleHTTPServer,
    ConsoleState,
    WebConsoleError,
    _local_endpoint_reachable,
    serve_console,
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class WebConsoleTests(unittest.TestCase):
    def test_http_correlation_and_diagnostic_export_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            server = ConsoleHTTPServer(("127.0.0.1", 0), state, serve_assets=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urlopen(
                    Request(base + "/api/health", headers={"X-Correlation-ID": "desktop-test-123"}),
                    timeout=2,
                ) as response:
                    health = json.loads(response.read())
                    self.assertEqual(response.headers["X-Correlation-ID"], "desktop-test-123")
                self.assertEqual(health["status"], "ok")

                request = Request(
                    base + "/api/diagnostics/export",
                    data=b"{}",
                    headers={
                        "Content-Type": "application/json",
                        "X-Correlation-ID": "diagnostic-test-456",
                    },
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    bundle = json.loads(response.read())
                    self.assertEqual(response.status, 201)
                    self.assertEqual(response.headers["X-Correlation-ID"], "diagnostic-test-456")
                self.assertTrue(Path(bundle["path"]).is_file())
            finally:
                server.shutdown()
                server.server_close()
                state.close()
                thread.join(timeout=2)

    def test_change_control_http_contract_exposes_review_apply_merge_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            server = ConsoleHTTPServer(("127.0.0.1", 0), state, serve_assets=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            change_set = {"schema_version": 1, "farm_id": "farm-one", "worker_id": "one"}
            try:
                with (
                    patch.object(state, "change_sets", return_value=[change_set]),
                    patch.object(state, "apply_candidate", return_value={"status": "VERIFIED"}) as apply,
                    patch.object(state, "merge_candidate", return_value={"status": "MERGED"}) as merge,
                    patch.object(state, "rollback_candidate", return_value={"status": "ROLLED_BACK"}) as rollback,
                ):
                    with urlopen(base + "/api/farms/farm-one/changesets", timeout=2) as response:
                        payload = json.loads(response.read())
                    self.assertEqual(payload["change_sets"], [change_set])

                    def post(path, payload):
                        request = Request(
                            base + path,
                            data=json.dumps(payload).encode(),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(request, timeout=2) as response:
                            return json.loads(response.read())

                    self.assertEqual(
                        post("/api/farms/farm-one/apply", {"worker_id": "one"})["status"],
                        "VERIFIED",
                    )
                    self.assertEqual(post("/api/farms/farm-one/merge", {})["status"], "MERGED")
                    self.assertEqual(
                        post(
                            "/api/farms/farm-one/rollback",
                            {"checkpoint_id": "checkpoint-one", "force": False},
                        )["status"],
                        "ROLLED_BACK",
                    )
                    apply.assert_called_once_with("farm-one", "one")
                    merge.assert_called_once_with("farm-one")
                    rollback.assert_called_once_with(
                        "farm-one", "checkpoint-one", force=False
                    )
            finally:
                server.shutdown()
                server.server_close()
                state.close()
                thread.join(timeout=2)

    def test_protocol_initialization_negotiates_before_business_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            server = ConsoleHTTPServer(("127.0.0.1", 0), state, serve_assets=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                request = Request(
                    base + "/api/protocol/initialize",
                    data=json.dumps(
                        {
                            "client_name": "test",
                            "client_version": "1",
                            "protocol_versions": [1],
                            "capabilities": ["typed-messages.v1", "approvals.v1"],
                            "required_capabilities": ["typed-messages.v1"],
                        }
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    initialized = json.loads(response.read())
                self.assertEqual(initialized["protocol_version"], 1)
                self.assertIn("typed-messages.v1", initialized["enabled_capabilities"])
                self.assertEqual(
                    set(initialized["message_schemas"]),
                    {"thread", "turn", "item", "worker", "tool", "diff", "approval", "usage"},
                )

                incompatible = Request(
                    base + "/api/protocol/initialize",
                    data=json.dumps({"protocol_versions": [99]}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as failure:
                    urlopen(incompatible, timeout=2)
                self.assertEqual(failure.exception.code, 409)
            finally:
                server.shutdown()
                server.server_close()
                state.close()
                thread.join(timeout=2)

    def test_farm_job_cancellation_reaches_runtime_and_persists_terminal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            started = threading.Event()

            def running_farm(**kwargs):
                started.set()
                deadline = time.monotonic() + 2
                while not kwargs["cancel_check"]() and time.monotonic() < deadline:
                    time.sleep(0.01)
                raise RuntimeError("cancel observed")

            plan = {
                "task_id": "cancel-test",
                "workers": [
                    {
                        "id": "impl",
                        "role": "implementation",
                        "profile": "cheap",
                        "goal": "Wait for cancellation.",
                        "allowed_paths": ["src/**"],
                    }
                ],
            }
            try:
                with patch("agent_farm.web_server.run_farm", side_effect=running_farm):
                    job = state.jobs.submit(plan)
                    self.assertTrue(started.wait(timeout=2))
                    response = state.jobs.cancel(job["job_id"])
                    self.assertEqual(response["status"], "CANCELLING")
                    deadline = time.monotonic() + 2
                    while (
                        state.jobs.get(job["job_id"])["status"] != "CANCELLED"
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                stored = state.jobs.get(job["job_id"])
                self.assertEqual(stored["status"], "CANCELLED")
                self.assertEqual(stored["error"]["type"], "Cancelled")
            finally:
                state.close()

    def test_worker_cancellation_targets_only_the_selected_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            started = threading.Event()
            observed: list[tuple[bool, bool]] = []

            def running_farm(**kwargs):
                started.set()
                checks = kwargs["worker_cancel_checks"]
                deadline = time.monotonic() + 2
                while not checks["one"]() and time.monotonic() < deadline:
                    time.sleep(0.01)
                observed.append((checks["one"](), checks["two"]()))
                return {"farm_id": "farm-worker-cancel", "status": "REVISION_REQUESTED"}

            plan = {
                "task_id": "worker-cancel-test",
                "workers": [
                    {"id": worker_id, "role": worker_id, "profile": "cheap", "goal": "Wait.", "allowed_paths": ["src/**"]}
                    for worker_id in ("one", "two")
                ],
            }
            try:
                with patch("agent_farm.web_server.run_farm", side_effect=running_farm):
                    job = state.jobs.submit(plan)
                    self.assertTrue(started.wait(timeout=2))
                    state.jobs.cancel(job["job_id"], worker_id="one")
                    deadline = time.monotonic() + 2
                    while (
                        state.jobs.get(job["job_id"])["status"] not in {"COMPLETED", "FAILED"}
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                self.assertEqual(observed, [(True, False)])
                self.assertEqual(state.jobs.get(job["job_id"])["status"], "COMPLETED")
            finally:
                state.close()

    def test_terminal_worker_can_be_retried_as_a_recovery_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            submitted_plans = []

            def complete_farm(**kwargs):
                submitted_plans.append(json.loads(kwargs["plan_file"].read_text(encoding="utf-8")))
                return {"farm_id": f"farm-{len(submitted_plans)}", "status": "REVISION_REQUESTED"}

            plan = {
                "task_id": "retry-test",
                "workers": [
                    {"id": "one", "role": "one", "profile": "cheap", "goal": "One.", "allowed_paths": ["src/one"]},
                    {
                        "id": "two",
                        "role": "two",
                        "profile": "cheap",
                        "goal": "Two.",
                        "allowed_paths": ["src/two"],
                        "depends_on": ["one"],
                    },
                ],
            }
            try:
                with patch("agent_farm.web_server.run_farm", side_effect=complete_farm):
                    original = state.jobs.submit(plan)
                    deadline = time.monotonic() + 2
                    while state.jobs.get(original["job_id"])["status"] != "COMPLETED" and time.monotonic() < deadline:
                        time.sleep(0.01)
                    retried = state.jobs.retry(original["job_id"], worker_id="two")
                    deadline = time.monotonic() + 2
                    while state.jobs.get(retried["job_id"])["status"] != "COMPLETED" and time.monotonic() < deadline:
                        time.sleep(0.01)

                self.assertEqual(retried["retry_of"], original["job_id"])
                self.assertEqual(retried["retry_worker_id"], "two")
                self.assertEqual(len(submitted_plans[1]["workers"]), 1)
                self.assertEqual(submitted_plans[1]["workers"][0]["id"], "two")
                self.assertEqual(submitted_plans[1]["workers"][0]["depends_on"], [])
            finally:
                state.close()

    def test_http_approval_decision_unblocks_a_waiting_runtime_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            server = ConsoleHTTPServer(("127.0.0.1", 0), state, serve_assets=False)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            decisions: list[str] = []
            waiter = threading.Thread(
                target=lambda: decisions.append(
                    state.approvals.request(
                        job_kind="farm",
                        job_id="farm-approval-test",
                        request={
                            "kind": "command",
                            "scope": "commands",
                            "tool_name": "run_command",
                            "title": "Allow command?",
                            "description": "pytest -q",
                            "details": {"argv": ["pytest", "-q"], "cwd": "."},
                        },
                    )
                )
            )
            waiter.start()
            try:
                deadline = time.monotonic() + 2
                pending = []
                while not pending and time.monotonic() < deadline:
                    with urlopen(base + "/api/approvals?status=pending", timeout=2) as response:
                        pending = json.loads(response.read())["approvals"]
                    if not pending:
                        time.sleep(0.01)
                self.assertEqual(len(pending), 1)
                request = Request(
                    base + f"/api/approvals/{pending[0]['approval_id']}/decision",
                    data=json.dumps({"decision": "allow_once"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    resolved = json.loads(response.read())
                waiter.join(timeout=2)
                self.assertEqual(resolved["decision"], "allow_once")
                self.assertEqual(decisions, ["allow_once"])
            finally:
                server.shutdown()
                server.server_close()
                state.close()
                server_thread.join(timeout=2)

    def test_provider_model_catalog_is_cached_and_can_be_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            catalog = {
                "provider_id": "private-provider",
                "template_id": "openai",
                "source": "live",
                "models": [{"id": "model-a", "name": "Model A", "reasoning": {}}],
                "model_count": 1,
            }
            try:
                with patch("agent_farm.web_server.discover_provider_models", return_value=catalog) as discover:
                    self.assertFalse(state.provider_models("private-provider").get("cached", False))
                    self.assertTrue(state.provider_models("private-provider")["cached"])
                    state.provider_models("private-provider", refresh=True)
                    self.assertEqual(discover.call_count, 2)
            finally:
                state.close()

    def test_runtime_health_probes_loopback_without_outbound_requests(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertTrue(_local_endpoint_reachable(f"http://127.0.0.1:{port}/v1"))
        self.assertFalse(_local_endpoint_reachable(f"http://127.0.0.1:{port}/v1"))
        self.assertIsNone(_local_endpoint_reachable("https://api.example.com/v1"))

    def _repo(self, root: Path) -> None:
        git(root, "init")
        (root / "README.md").write_text("base\n", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init")
        (root / "agent-farm.local.json").write_text(
            json.dumps(
                {
                    "default_worker_profile": "cheap",
                    "worker_profiles": {
                        "cheap": {
                            "display_name": "Economy Worker",
                            "model": "budget-model",
                            "provider": "private-provider",
                            "secrets_env": ".agent-farm/secrets.env",
                        }
                    },
                    "model_providers": {
                        "private-provider": {
                            "name": "Private endpoint",
                            "base_url": "https://secret.example/v1",
                            "env_key": "VERY_SECRET_KEY",
                            "wire_api": "responses",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_bootstrap_exposes_profile_metadata_but_not_provider_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            try:
                payload = state.bootstrap()
            finally:
                state.close()

        encoded = json.dumps(payload)
        self.assertEqual(payload["profiles"][0]["model"], "budget-model")
        self.assertEqual(payload["profiles"][0]["display_name"], "Economy Worker")
        self.assertFalse(payload["supervisor"]["ready"])
        self.assertEqual(payload["profiles"][0]["provider_name"], "Private endpoint")
        self.assertNotIn("secret.example", encoded)
        self.assertNotIn("VERY_SECRET_KEY", encoded)
        self.assertNotIn("secrets.env", encoded)

    def test_unknown_supervisor_provider_is_not_reported_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            config_path = root / "agent-farm.local.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["supervisor_model"] = "expensive-model"
            config["supervisor_provider"] = "removed-provider"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            state = ConsoleState(root, None)
            try:
                self.assertFalse(state.settings()["runtime"]["native_ready"])
                self.assertFalse(state.bootstrap()["supervisor"]["ready"])
            finally:
                state.close()

    def test_http_serves_console_rejects_traversal_and_runs_validated_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            server = ConsoleHTTPServer(("127.0.0.1", 0), state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            plan = {
                "task_id": "web-test",
                "workers": [
                    {
                        "id": "impl",
                        "role": "implementation",
                        "profile": "cheap",
                        "goal": "Implement the scoped change.",
                        "allowed_paths": ["src/**"],
                    }
                ],
            }
            try:
                with urlopen(base + "/", timeout=2) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("Agent Farm", html)
                with self.assertRaises(HTTPError) as missing:
                    urlopen(base + "/../../agent-farm.local.json", timeout=2)
                self.assertEqual(missing.exception.code, 404)

                with patch(
                    "agent_farm.web_server.run_farm",
                    return_value={"farm_id": "20260728T000000Z-web-test"},
                ) as mocked:
                    request = Request(
                        base + "/api/farms",
                        data=json.dumps(plan).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=2) as response:
                        job = json.loads(response.read())
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        current = state.jobs.get(job["job_id"])
                        if current["status"] == "COMPLETED":
                            break
                        time.sleep(0.02)
                    self.assertEqual(current["status"], "COMPLETED")
                    mocked.assert_called_once()

                cross_origin = Request(
                    base + "/api/farms",
                    data=json.dumps(plan).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as forbidden:
                    urlopen(cross_origin, timeout=2)
                self.assertEqual(forbidden.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                state.close()
                thread.join(timeout=2)

    def test_refuses_non_loopback_bind(self):
        with self.assertRaisesRegex(WebConsoleError, "loopback"):
            serve_console(repo=Path.cwd(), host="0.0.0.0", open_browser=False)

    def test_supervisor_plan_job_returns_validated_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            plan = WorkerPlan.from_dict(
                {
                    "task_id": "planned-task",
                    "base_ref": "HEAD",
                    "max_parallel": 1,
                    "workers": [
                        {
                            "id": "implementation",
                            "role": "Implementation",
                            "profile": "cheap",
                            "goal": "Implement a safe scoped change.",
                            "allowed_paths": ["src/**"],
                        }
                    ],
                }
            )
            try:
                with patch("agent_farm.web_server.draft_worker_plan", return_value=plan):
                    job = state.plan_jobs.submit(
                        {
                            "request": "Implement the feature.",
                            "base_ref": "HEAD",
                            "worker_count": 2,
                        }
                    )
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        current = state.plan_jobs.get(job["job_id"])
                        if current["status"] == "COMPLETED":
                            break
                        time.sleep(0.02)
                self.assertEqual(current["status"], "COMPLETED")
                self.assertEqual(current["plan"]["workers"][0]["profile"], "cheap")
            finally:
                state.close()

    def test_live_job_events_are_ordered_and_keep_workers_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            plan = WorkerPlan.from_dict(
                {
                    "task_id": "live-events",
                    "base_ref": "HEAD",
                    "workers": [
                        {
                            "id": "research",
                            "role": "Research",
                            "profile": "cheap",
                            "goal": "Research the task.",
                            "allowed_paths": ["src/**"],
                        }
                    ],
                }
            )

            def fake_run_farm(*, event_callback, **kwargs):
                event_callback(
                    {
                        "type": "model.output.delta",
                        "agent_id": "research",
                        "agent_kind": "worker",
                        "display_name": "Research",
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "delta": "Worker output",
                    }
                )
                event_callback(
                    {
                        "type": "agent.completed",
                        "agent_id": "supervisor-synthesis",
                        "agent_kind": "supervisor",
                        "display_name": "Synthesis Supervisor",
                        "provider": "openai",
                        "model": "gpt-5",
                    }
                )
                return {"farm_id": "farm-live-events"}

            try:
                with patch("agent_farm.web_server.run_farm", side_effect=fake_run_farm):
                    job = state.jobs.submit(plan.to_json())
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        current = state.jobs.get(job["job_id"])
                        if current["status"] == "COMPLETED":
                            break
                        time.sleep(0.02)

                batch = state.jobs.events(job["job_id"], after=0)
                self.assertEqual([event["sequence"] for event in batch["events"]], [1, 2])
                self.assertEqual(batch["events"][0]["agent_id"], "research")
                self.assertEqual(batch["events"][1]["agent_id"], "supervisor-synthesis")
                self.assertEqual(batch["next_sequence"], 2)
                self.assertEqual(state.jobs.events(job["job_id"], after=2)["events"], [])
            finally:
                state.close()

    def test_job_event_stream_pushes_versioned_sse_envelopes_and_closes_at_terminal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            server = ConsoleHTTPServer(("127.0.0.1", 0), state)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            plan = WorkerPlan.from_dict(
                {
                    "task_id": "sse-events",
                    "base_ref": "HEAD",
                    "workers": [
                        {
                            "id": "research",
                            "role": "Research",
                            "profile": "cheap",
                            "goal": "Stream the result.",
                            "allowed_paths": ["reports/**"],
                        }
                    ],
                }
            )

            def fake_run_farm(*, event_callback, **kwargs):
                event_callback(
                    {
                        "type": "model.output.delta",
                        "agent_id": "research",
                        "agent_kind": "worker",
                        "display_name": "Research",
                        "delta": "live output",
                    }
                )
                event_callback(
                    {
                        "type": "agent.completed",
                        "agent_id": "research",
                        "agent_kind": "worker",
                        "display_name": "Research",
                    }
                )
                return {"farm_id": "farm-sse-events"}

            try:
                with patch("agent_farm.web_server.run_farm", side_effect=fake_run_farm):
                    job = state.jobs.submit(plan.to_json())
                    url = (
                        f"http://127.0.0.1:{server.server_address[1]}"
                        f"/api/jobs/{job['job_id']}/stream?after=0"
                    )
                    with urlopen(url, timeout=3) as response:
                        self.assertEqual(response.headers.get_content_type(), "text/event-stream")
                        frames = response.read().decode("utf-8")

                data_lines = [
                    line.removeprefix("data: ")
                    for line in frames.splitlines()
                    if line.startswith("data: ")
                ]
                envelopes = [json.loads(line) for line in data_lines]
                self.assertEqual([item["sequence"] for item in envelopes], [1, 2])
                self.assertEqual([item["event"]["type"] for item in envelopes], [
                    "model.output.delta",
                    "agent.completed",
                ])
                self.assertTrue(all(item["protocol_version"] == 1 for item in envelopes))
                self.assertTrue(all(item["stream"] == "farm" for item in envelopes))
            finally:
                server.shutdown()
                server.server_close()
                state.close()
                server_thread.join(timeout=2)

    def test_settings_save_redacts_secrets_and_updates_new_run_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            config_path = root / "agent-farm.local.json"
            local_config = json.loads(config_path.read_text(encoding="utf-8"))
            local_config["model_providers"]["private-provider"]["http_headers"] = {
                "Authorization": "literal-secret-must-not-leak"
            }
            config_path.write_text(json.dumps(local_config), encoding="utf-8")
            secrets_path = root / ".agent-farm" / "secrets.env"
            secrets_path.parent.mkdir(parents=True)
            secrets_path.write_text("VERY_SECRET_KEY=actual-secret-value\n", encoding="utf-8")

            state = ConsoleState(root, None)
            try:
                settings = state.settings()
                encoded = json.dumps(settings)
                self.assertNotIn("literal-secret-must-not-leak", encoded)
                self.assertNotIn("actual-secret-value", encoded)
                self.assertNotIn("http_headers", encoded)
                self.assertTrue(
                    settings["provider_status"]["private-provider"]["credential_configured"]
                )

                settings["config"]["supervisor_model"] = "expensive-model"
                settings["config"]["supervisor_provider"] = "private-provider"
                settings["config"]["worker_profiles"]["cheap"][
                    "codex_config_overrides"
                ] = {"model_reasoning_effort": "low"}
                saved = state.save_settings({"config": settings["config"]})
                persisted = json.loads(config_path.read_text(encoding="utf-8"))

                self.assertEqual(saved["config"]["supervisor_model"], "expensive-model")
                self.assertEqual(state.bootstrap()["supervisor"]["model"], "expensive-model")
                self.assertTrue(state.bootstrap()["supervisor"]["ready"])
                self.assertEqual(
                    persisted["model_providers"]["private-provider"]["http_headers"],
                    {"Authorization": "literal-secret-must-not-leak"},
                )
            finally:
                state.close()

    def test_settings_exposes_templates_and_saves_api_key_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            try:
                settings = state.settings()
                template_ids = {template["id"] for template in settings["provider_templates"]}
                self.assertIn("custom-openai-compatible", template_ids)
                self.assertIn("deepseek", template_ids)

                secret = "new-secret-must-never-be-returned"
                saved = state.save_settings(
                    {
                        "config": settings["config"],
                        "provider_secrets": {"private-provider": secret},
                    }
                )

                self.assertTrue(
                    saved["provider_status"]["private-provider"]["credential_configured"]
                )
                self.assertNotIn(secret, json.dumps(saved))
                self.assertEqual(
                    (root / ".agent-farm" / "secrets.env").read_text(encoding="utf-8").split("=", 1)[1].strip(),
                    secret,
                )

                state.save_settings({"config": saved["config"], "provider_secrets": {}})
                self.assertEqual(
                    (root / ".agent-farm" / "secrets.env").read_text(encoding="utf-8").split("=", 1)[1].strip(),
                    secret,
                )
            finally:
                state.close()

    def test_settings_rejects_secret_for_unknown_or_non_authenticated_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            try:
                settings = state.settings()
                with self.assertRaisesRegex(ValueError, "unknown provider"):
                    state.save_settings(
                        {
                            "config": settings["config"],
                            "provider_secrets": {"missing": "secret"},
                        }
                    )
                settings["config"]["model_providers"]["local"] = {
                    "name": "Local",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "wire_api": "chat",
                }
                with self.assertRaisesRegex(ValueError, "does not use an API key"):
                    state.save_settings(
                        {
                            "config": settings["config"],
                            "provider_secrets": {"local": "secret"},
                        }
                    )
            finally:
                state.close()

    def test_settings_migrates_legacy_worker_fields_into_an_editable_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init")
            (root / "README.md").write_text("base\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init")
            (root / "agent-farm.local.json").write_text(
                json.dumps({"worker_model": "budget-model", "worker_provider": "ollama"}),
                encoding="utf-8",
            )

            state = ConsoleState(root, None)
            try:
                settings = state.settings()
                self.assertTrue(settings["migration_required"])
                self.assertEqual(settings["config"]["default_worker_profile"], "default")
                self.assertEqual(
                    settings["config"]["worker_profiles"]["default"]["model"],
                    "budget-model",
                )
                self.assertEqual(
                    settings["config"]["worker_profiles"]["default"]["display_name"],
                    "Default Worker",
                )

                saved = state.save_settings({"config": settings["config"]})
                self.assertFalse(saved["migration_required"])
                self.assertEqual(
                    state.config.worker_profiles["default"]["provider"],
                    "ollama",
                )
            finally:
                state.close()

    def test_settings_http_endpoint_persists_validated_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            server = ConsoleHTTPServer(("127.0.0.1", 0), state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urlopen(base + "/api/settings", timeout=2) as response:
                    settings = json.loads(response.read())
                settings["config"]["max_parallel_workers"] = 2
                request = Request(
                    base + "/api/settings",
                    data=json.dumps({"config": settings["config"]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    saved = json.loads(response.read())
                self.assertEqual(saved["config"]["max_parallel_workers"], 2)
                self.assertEqual(state.bootstrap()["limits"]["max_parallel_workers"], 2)
            finally:
                server.shutdown()
                server.server_close()
                state.close()
                thread.join(timeout=2)

    def test_attachment_http_endpoint_feeds_supervisor_context_and_thread_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            source = root / "market-notes.md"
            source.write_text("NAND contract prices increased.", encoding="utf-8")
            state = ConsoleState(root, None)
            server = ConsoleHTTPServer(("127.0.0.1", 0), state)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            plan = WorkerPlan.from_dict(
                {
                    "task_id": "attachment-plan",
                    "base_ref": "HEAD",
                    "workers": [
                        {
                            "id": "research",
                            "role": "Research",
                            "profile": "cheap",
                            "goal": "Analyze the supplied notes.",
                            "allowed_paths": ["reports/**"],
                        }
                    ],
                }
            )
            captured = {}

            def fake_draft(**kwargs):
                captured.update(kwargs)
                return plan

            try:
                request = Request(
                    base + "/api/attachments",
                    data=json.dumps({"local_path": str(source)}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    attachment = json.loads(response.read())
                    self.assertEqual(response.status, 201)

                thread_document = state.create_thread({"title": "Attached analysis"})
                with patch("agent_farm.web_server.draft_worker_plan", side_effect=fake_draft):
                    job = state.plan_jobs.submit(
                        {
                            "request": "Analyze the attached market notes.",
                            "task_id": "attachment-plan",
                            "thread_id": thread_document["thread_id"],
                            "attachments": [attachment["id"]],
                        }
                    )
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        current = state.plan_jobs.get(job["job_id"])
                        if current["status"] == "COMPLETED":
                            break
                        time.sleep(0.02)

                self.assertEqual(current["status"], "COMPLETED")
                self.assertIn("NAND contract prices increased", captured["attachment_context"])
                self.assertEqual(captured["model_attachments"], [])
                saved = state.threads.read(thread_document["thread_id"])
                user_message = saved["turns"][0]["items"][0]
                self.assertEqual(user_message["payload"]["attachments"][0]["name"], source.name)

                delete = Request(
                    base + f"/api/attachments/{attachment['id']}", method="DELETE"
                )
                with urlopen(delete, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                with self.assertRaises(FileNotFoundError):
                    state.attachments.resolve([attachment["id"]])
            finally:
                server.shutdown()
                server.server_close()
                state.close()
                server_thread.join(timeout=2)

    def test_supervisor_plan_is_persisted_in_thread_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            thread = state.create_thread({"title": "Persistent planning"})
            plan = WorkerPlan.from_dict(
                {
                    "task_id": "persistent-plan",
                    "base_ref": "HEAD",
                    "max_parallel": 1,
                    "workers": [
                        {
                            "id": "implementation",
                            "role": "Implementation",
                            "profile": "cheap",
                            "goal": "Implement the scoped change.",
                            "allowed_paths": ["src/**"],
                        }
                    ],
                }
            )
            try:
                with patch("agent_farm.web_server.draft_worker_plan", return_value=plan):
                    job = state.plan_jobs.submit(
                        {
                            "request": "Implement the persistent thread layer.",
                            "task_id": "persistent-plan",
                            "base_ref": "HEAD",
                            "worker_count": 1,
                            "thread_id": thread["thread_id"],
                        }
                    )
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        current = state.plan_jobs.get(job["job_id"])
                        if current["status"] == "COMPLETED":
                            break
                        time.sleep(0.02)

                saved = state.threads.read(thread["thread_id"])
                turn = saved["turns"][0]
                plan_item = next(item for item in turn["items"] if item["type"] == "supervisor_plan")
                self.assertEqual(turn["status"], "awaiting_confirmation")
                self.assertEqual(plan_item["payload"]["plan"]["task_id"], "persistent-plan")
                self.assertEqual(current["thread_id"], thread["thread_id"])
                self.assertEqual(current["turn_id"], turn["turn_id"])
            finally:
                state.close()

    def test_farm_job_updates_existing_thread_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            thread = state.create_thread({"title": "Persistent Farm"})
            turn = state.threads.start_turn(thread["thread_id"], "Run one Worker.")
            plan = WorkerPlan.from_dict(
                {
                    "task_id": "persistent-farm",
                    "base_ref": "HEAD",
                    "workers": [
                        {
                            "id": "implementation",
                            "role": "Implementation",
                            "profile": "cheap",
                            "goal": "Implement the scoped change.",
                            "allowed_paths": ["src/**"],
                        }
                    ],
                }
            )
            try:
                with patch(
                    "agent_farm.web_server.run_farm",
                    return_value={"farm_id": "20260729T000000Z-persistent-farm"},
                ):
                    job = state.jobs.submit(
                        plan.to_json(),
                        thread_id=thread["thread_id"],
                        turn_id=turn["turn_id"],
                    )
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        current = state.jobs.get(job["job_id"])
                        if current["status"] == "COMPLETED":
                            break
                        time.sleep(0.02)

                saved = state.threads.read(thread["thread_id"])
                farm_item = next(
                    item for item in saved["turns"][0]["items"] if item["type"] == "farm_run"
                )
                self.assertEqual(saved["turns"][0]["status"], "awaiting_review")
                self.assertEqual(
                    farm_item["payload"]["farm_id"],
                    "20260729T000000Z-persistent-farm",
                )
            finally:
                state.close()

    def test_thread_http_endpoints_create_read_and_list_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            server = ConsoleHTTPServer(("127.0.0.1", 0), state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                request = Request(
                    base + "/api/threads",
                    data=json.dumps({"title": "HTTP thread"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    created = json.loads(response.read())
                state.threads.start_turn(created["thread_id"], "Persist this turn.")
                with urlopen(base + f"/api/threads/{created['thread_id']}", timeout=2) as response:
                    saved = json.loads(response.read())
                with urlopen(
                    base + f"/api/threads/{created['thread_id']}/events?after=1", timeout=2
                ) as response:
                    events = json.loads(response.read())["events"]

                self.assertEqual(saved["title"], "HTTP thread")
                self.assertEqual(saved["turns"][0]["items"][0]["type"], "user_message")
                self.assertTrue(events)
                self.assertTrue(all(event["sequence"] > 1 for event in events))
            finally:
                server.shutdown()
                server.server_close()
                state.close()
                thread.join(timeout=2)

    def test_runtime_restart_persists_jobs_and_reconciles_interrupted_thread_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            thread = state.create_thread({"title": "Interrupted Farm"})
            turn = state.threads.start_turn(thread["thread_id"], "Run durable work.")
            item = state.threads.add_item(
                thread["thread_id"],
                turn["turn_id"],
                "farm_run",
                status="running",
                payload={"task_id": "durable-farm", "farm_id": None},
            )
            state.threads.update_turn(thread["thread_id"], turn["turn_id"], "running")
            state.runtime_store.create_job(
                "farm",
                {
                    "job_id": "interrupted-job",
                    "task_id": "durable-farm",
                    "status": "RUNNING",
                    "created_at": "2026-08-02T01:00:00+00:00",
                    "started_at": "2026-08-02T01:00:01+00:00",
                    "finished_at": None,
                    "farm_id": None,
                    "thread_id": thread["thread_id"],
                    "turn_id": turn["turn_id"],
                    "item_id": item["item_id"],
                    "attachment_ids": [],
                    "attachments": [],
                    "error": None,
                },
            )
            state.runtime_store.append_event(
                "farm",
                "interrupted-job",
                {"type": "worker.started", "timestamp": "2026-08-02T01:00:02+00:00"},
            )
            state.close()

            reopened = ConsoleState(root, None)
            try:
                job = reopened.jobs.get("interrupted-job")
                batch = reopened.jobs.events("interrupted-job")
                saved_thread = reopened.threads.read(thread["thread_id"])
                saved_turn = saved_thread["turns"][0]
                saved_item = next(
                    candidate
                    for candidate in saved_turn["items"]
                    if candidate["item_id"] == item["item_id"]
                )

                self.assertEqual(job["status"], "INTERRUPTED")
                self.assertEqual(batch["events"][0]["type"], "worker.started")
                self.assertEqual(batch["events"][-1]["type"], "runtime.interrupted")
                self.assertEqual(saved_thread["status"], "interrupted")
                self.assertEqual(saved_turn["status"], "interrupted")
                self.assertIsNotNone(saved_turn["completed_at"])
                self.assertEqual(saved_item["status"], "interrupted")
                self.assertEqual(saved_item["payload"]["error"]["type"], "RuntimeInterrupted")
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
