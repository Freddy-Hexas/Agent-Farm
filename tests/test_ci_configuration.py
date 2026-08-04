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
        self.assertIn("python -X utf8 -m pytest -q tests", workflow)
        self.assertIn("AgentFarm.Desktop/AgentFarm.Desktop.csproj", workflow)
        self.assertIn("-warnaserror", workflow)
        self.assertIn("microsoft/setup-WinAppCli@v0.1", workflow)
        self.assertIn("-p:WindowsAppSDKSelfContained=true", workflow)
        self.assertIn("-p:SelfContained=true", workflow)
        self.assertIn("scripts\\Invoke-CiCommand.ps1", workflow)
        self.assertIn("scripts\\test_native_ui_ci.ps1", workflow)
        self.assertIn('python -m pip install --upgrade pip -e ".[test]"', workflow)
        self.assertIn("actions/checkout@v6", workflow)
        self.assertIn("actions/setup-python@v6", workflow)
        self.assertIn("actions/setup-dotnet@v5", workflow)
        self.assertIn("dotnet-quality: ga", workflow)
        self.assertIn("windows-2022", workflow)
        self.assertNotIn("windows-latest", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("if: always()", workflow)
        native_ui_script = (ROOT / "scripts" / "test_native_ui.ps1").read_text(encoding="utf-8")
        self.assertIn("::error title=", native_ui_script)
        self.assertIn("Ensure-ExecutionPaneExpanded", native_ui_script)
        self.assertIn("ToggleExecutionPaneButton", native_ui_script)
        ci_ui_script = (ROOT / "scripts" / "test_native_ui_ci.ps1").read_text(encoding="utf-8")
        self.assertIn("test_native_ui.ps1", ci_ui_script)
        self.assertIn("AGENT_FARM_SOURCE_ROOT", ci_ui_script)
        self.assertIn("AGENT_FARM_PYTHON", ci_ui_script)
        self.assertIn("wait-for RepositoryButton", ci_ui_script)
        self.assertIn("Native UI startup", ci_ui_script)

    def test_optional_local_winui_analyzer_does_not_break_clean_build_hosts(self) -> None:
        props = (ROOT / "AgentFarm.Desktop" / "Directory.Build.props").read_text(encoding="utf-8")

        self.assertIn("$(USERPROFILE)", props)
        self.assertIn("AgentFarmWinUIAnalyzerRoot", props)
        self.assertEqual(props.count("Condition=\"Exists("), 2)
        self.assertNotIn("C:\\Users\\xffre", props)


if __name__ == "__main__":
    unittest.main()
