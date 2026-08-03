from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.model_client import ModelTransportError
from agent_farm.provider_health import ProviderCircuitOpenError, ProviderHealthStore


class ProviderHealthTests(unittest.TestCase):
    def test_opens_circuit_after_threshold_and_success_resets_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProviderHealthStore(
                Path(tmp) / "health.db", failure_threshold=2, cooldown_seconds=60
            )
            error = ModelTransportError("unavailable", retryable=True, status_code=503)
            store.record_failure("provider", "model", error)
            self.assertEqual(store.before_request("provider", "model")["status"], "degraded")
            store.record_failure("provider", "model", error)
            with self.assertRaisesRegex(ProviderCircuitOpenError, "circuit"):
                store.before_request("provider", "model")
            store.record_success("provider", "model")
            self.assertEqual(store.before_request("provider", "model")["status"], "healthy")

    def test_rate_limit_honors_retry_after_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProviderHealthStore(
                Path(tmp) / "health.db", failure_threshold=5, cooldown_seconds=60
            )
            with patch("agent_farm.provider_health.time.time", return_value=100.0):
                store.record_failure(
                    "provider",
                    "model",
                    ModelTransportError(
                        "rate limited",
                        retryable=True,
                        status_code=429,
                        retry_after_seconds=30,
                    ),
                )
            with patch("agent_farm.provider_health.time.time", return_value=110.0):
                with self.assertRaisesRegex(ProviderCircuitOpenError, "rate limited"):
                    store.before_request("provider", "model")
            with patch("agent_farm.provider_health.time.time", return_value=131.0):
                self.assertEqual(
                    store.before_request("provider", "model")["status"], "degraded"
                )


if __name__ == "__main__":
    unittest.main()
