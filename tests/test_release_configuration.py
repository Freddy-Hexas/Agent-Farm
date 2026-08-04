import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReleaseConfigurationTests(unittest.TestCase):
    def test_channel_policy_is_complete_and_generates_windows_update_contracts(self) -> None:
        policy = json.loads((ROOT / "packaging" / "release_channels.json").read_text())
        self.assertEqual(policy["schema_version"], 1)
        self.assertEqual(set(policy["channels"]), {"stable", "preview"})
        self.assertFalse(policy["channels"]["stable"]["prerelease"])
        self.assertTrue(policy["channels"]["preview"]["prerelease"])
        for channel, data in policy["channels"].items():
            self.assertIn(channel, data["appinstaller_asset"])
            self.assertGreater(data["hours_between_update_checks"], 0)

        script = (ROOT / "scripts" / "package_native_release.ps1").read_text()
        build_script = (ROOT / "scripts" / "build_native_windows.ps1").read_text()
        project = (ROOT / "AgentFarm.Desktop" / "AgentFarm.Desktop.csproj").read_text()
        self.assertIn("<WindowsAppSDKSelfContained", project)
        self.assertIn("<SelfContained", project)
        for contract in (
            "winapp package",
            "--executable AgentFarm.Desktop.exe",
            "winapp sign",
            "--timestamp",
            "TimeStamperCertificate",
            "AutomaticBackgroundTask",
            "HoursBetweenUpdateChecks",
            "$versionParts[0] + 1",
            "reject self-signed",
            "SHA256SUMS.txt",
            "test_frozen_backend.ps1",
        ):
            self.assertIn(contract, script)
        self.assertIn('"AgentFarm.Desktop\\Assets"', build_script)
        self.assertIn('"Assets"', build_script)
        self.assertIn('"AppIcon.ico"', build_script)
        self.assertIn('"AppX\\Backend"', build_script)
        self.assertIn('"AppX\\Assets"', build_script)

    def test_release_workflow_requires_protected_signing_inputs(self) -> None:
        workflow_path = ROOT / ".github" / "workflows" / "release.yml"
        workflow = yaml.safe_load(workflow_path.read_text())
        text = workflow_path.read_text()
        self.assertIn("workflow_dispatch", workflow[True])
        self.assertIn("production-signing", text)
        self.assertIn("AGENT_FARM_SIGNING_PFX_BASE64", text)
        self.assertIn("AGENT_FARM_SIGNING_PASSWORD", text)
        self.assertIn("AGENT_FARM_PUBLISHER", text)
        self.assertIn("package_native_release.ps1", text)
        self.assertIn("Add-AppxPackage", text)

    def test_security_workflow_covers_code_dependencies_and_secrets(self) -> None:
        path = ROOT / ".github" / "workflows" / "security.yml"
        workflow = yaml.safe_load(path.read_text())
        self.assertEqual(set(workflow["jobs"]), {"codeql", "dependencies", "secrets"})
        text = path.read_text()
        self.assertIn("pip-audit", text)
        self.assertIn("--vulnerability-service osv", text)
        runtime_requirements = (ROOT / "packaging" / "runtime-requirements.txt").read_text()
        self.assertRegex(runtime_requirements, r"(?m)^pypdf==\d+\.\d+\.\d+$")
        self.assertIn("--vulnerable", text)
        self.assertIn("gitleaks/gitleaks-action", text)
        self.assertIn("github/codeql-action/analyze@v4", text)
        self.assertIn("actions/checkout@v6", text)
        self.assertIn("actions/setup-python@v6", text)
        self.assertIn("actions/setup-dotnet@v5", text)
        self.assertIn("dotnet-quality: ga", text)
        self.assertIn("windows-2022", text)
        self.assertIn("dotnet package list", text)
        self.assertIn('"vulnerabilities"\\s*:', text)

    def test_source_manifest_has_upgrade_stable_identity(self) -> None:
        root = ET.parse(ROOT / "AgentFarm.Desktop" / "Package.appxmanifest").getroot()
        identity = next(element for element in root if element.tag.endswith("Identity"))
        self.assertEqual(identity.attrib["Name"], "AgentFarm.Desktop")
        self.assertEqual(identity.attrib["Publisher"], "CN=Agent Farm")
        self.assertRegex(identity.attrib["Version"], r"^\d+\.\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
