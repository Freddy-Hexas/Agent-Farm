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


if __name__ == "__main__":
    unittest.main()
