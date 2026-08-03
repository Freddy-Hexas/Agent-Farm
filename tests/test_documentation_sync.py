from __future__ import annotations

import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_versions_and_release_links_are_synchronized() -> None:
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    package_manifest = (ROOT / "AgentFarm.Desktop" / "Package.appxmanifest").read_text(
        encoding="utf-8-sig"
    )
    package_version = re.search(r'<Identity[\s\S]*?Version="([^"]+)"', package_manifest)
    assert package_version is not None
    assert package_version.group(1) == project_version

    init_source = (ROOT / "agent_farm" / "__init__.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f'__version__ = "{project_version}"' in init_source
    assert f"version-{project_version}-blue" in readme
    assert f"releases/v{project_version}/AgentFarm-Native-x64.msix" in readme


def test_maintainer_document_set_and_readme_links_are_complete() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    expected = {
        "ARCHITECTURE.md": "Native WinUI client",
        "PROTOCOL.md": "model-deltas.v1",
        "RELEASING.md": "production-signing",
        "SECURITY.md": "Gitleaks",
        "LIMITATIONS.md": "Windows x64",
    }
    for name, marker in expected.items():
        document = ROOT / "docs" / name
        assert document.is_file()
        assert marker in document.read_text(encoding="utf-8")
        assert f"docs/{name}" in readme

    assert "96 automated tests" not in readme
    assert "Production code signing and automatic update delivery are not yet implemented" not in readme


def test_release_and_protocol_docs_match_checked_in_contracts() -> None:
    release_doc = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    protocol_doc = (ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
    release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    protocol_source = (ROOT / "agent_farm" / "protocol.py").read_text(encoding="utf-8")

    for secret in (
        "AGENT_FARM_SIGNING_PFX_BASE64",
        "AGENT_FARM_SIGNING_PASSWORD",
        "AGENT_FARM_PUBLISHER",
    ):
        assert secret in release_doc
        assert secret in release_workflow
    for capability in re.findall(r'"([a-z-]+\.v1)"', protocol_source):
        assert capability in protocol_doc
