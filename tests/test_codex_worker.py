import unittest
from pathlib import Path

from agent_farm.codex_worker import build_codex_args
from agent_farm.models import AgentFarmConfig


class CodexWorkerArgsTests(unittest.TestCase):
    def test_custom_provider_is_passed_as_codex_config_without_secret_value(self):
        config = AgentFarmConfig(
            worker_model="cheap-model",
            worker_provider="proxy",
            model_providers={
                "proxy": {
                    "name": "Proxy",
                    "base_url": "https://proxy.example.com/v1",
                    "env_key": "PROXY_API_KEY",
                    "wire_api": "responses",
                }
            },
        )
        args = build_codex_args(
            config=config,
            worktree=Path("worktree"),
            final_message_file=Path("final.md"),
            model=None,
        )
        joined = "\n".join(args)
        self.assertIn("--model\ncheap-model", joined)
        self.assertNotIn("--ask-for-approval", args)
        self.assertIn('approval_policy="never"', args)
        self.assertIn('model_provider="proxy"', args)
        self.assertIn('model_providers.proxy.base_url="https://proxy.example.com/v1"', args)
        self.assertIn('model_providers.proxy.env_key="PROXY_API_KEY"', args)
        self.assertNotIn("replace-with-your-api-key", joined)

    def test_local_provider_flags_are_supported(self):
        config = AgentFarmConfig(worker_model="gpt-oss:20b", worker_oss=True, worker_local_provider="ollama")
        args = build_codex_args(
            config=config,
            worktree=Path("worktree"),
            final_message_file=Path("final.md"),
            model=None,
        )
        self.assertIn("--oss", args)
        self.assertEqual(args[args.index("--local-provider") + 1], "ollama")


if __name__ == "__main__":
    unittest.main()
