import tempfile
import unittest
from pathlib import Path

from agent_farm.daemon_runtime import (
    DESCRIPTOR_SCHEMA_VERSION,
    RUNTIME_PROTOCOL_VERSION,
    DaemonLease,
    read_descriptor,
    write_descriptor,
)


class DaemonRuntimeTests(unittest.TestCase):
    def test_repository_lease_is_single_instance_and_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.lock"
            first = DaemonLease(path)
            second = DaemonLease(path)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                first.close()
                self.assertTrue(second.acquire())
            finally:
                first.close()
                second.close()

    def test_descriptor_is_atomic_and_rejects_incompatible_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            write_descriptor(
                path,
                {
                    "pid": 123,
                    "url": "http://127.0.0.1:43123/?native=1",
                    "repository": str(Path(tmp)),
                },
            )

            descriptor = read_descriptor(path)
            self.assertIsNotNone(descriptor)
            self.assertEqual(descriptor["schema_version"], DESCRIPTOR_SCHEMA_VERSION)
            self.assertEqual(descriptor["protocol_version"], RUNTIME_PROTOCOL_VERSION)
            self.assertFalse(list(path.parent.glob("runtime.json.*.tmp")))

            path.write_text('{"schema_version":999,"protocol_version":1}', encoding="utf-8")
            self.assertIsNone(read_descriptor(path))


if __name__ == "__main__":
    unittest.main()
