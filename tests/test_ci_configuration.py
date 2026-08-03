import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContinuousIntegrationConfigurationTests(unittest.TestCase):
    def test_ci_runs_python_native_build_and_real_native_ui_automation(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        for job in ("python-tests:", "native-build:", "native-ui:"):
            self.assertIn(job, workflow)
        self.assertIn("python -m pytest -q", workflow)
        self.assertIn("AgentFarm.Desktop/AgentFarm.Desktop.csproj", workflow)
        self.assertIn("-warnaserror", workflow)
        self.assertIn("Microsoft.WinAppCli", workflow)
        self.assertIn("scripts\\test_native_ui.ps1", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("if: always()", workflow)


if __name__ == "__main__":
    unittest.main()
