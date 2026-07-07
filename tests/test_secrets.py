import tempfile
import unittest
from pathlib import Path

from agent_farm.secrets import load_secrets_env, parse_env_file


class SecretsTests(unittest.TestCase):
    def test_parse_env_file_supports_quotes_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.env"
            path.write_text(
                "# comment\n"
                "export API_KEY='abc123'\n"
                'FEATURES=\"tools\"\n',
                encoding="utf-8",
            )
            values = parse_env_file(path)
        self.assertEqual(values["API_KEY"], "abc123")
        self.assertEqual(values["FEATURES"], "tools")

    def test_missing_secrets_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            values = load_secrets_env(Path(tmp), ".agent-farm/secrets.env")
        self.assertEqual(values, {})


if __name__ == "__main__":
    unittest.main()
