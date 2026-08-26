from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[1] / "agent_farm" / "web"


class _ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.views: set[str] = set()
        self.nav_views: dict[str, dict[str, str | None]] = {}
        self.settings_sections: set[str] = set()
        self.settings_panels: set[str] = set()
        self.buttons: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.ids.add(element_id)
            if element_id.endswith("-view"):
                self.views.add(element_id.removesuffix("-view"))
        if tag == "button" and values.get("data-view"):
            self.nav_views[values["data-view"] or ""] = values
        if tag == "button":
            self.buttons.append(values)
        if values.get("data-settings-section"):
            self.settings_sections.add(values["data-settings-section"] or "")
        if values.get("data-settings-panel"):
            self.settings_panels.add(values["data-settings-panel"] or "")


class WebUIContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        self.javascript = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        self.parser = _ContractParser()
        self.parser.feed(self.html)

    def test_every_navigation_button_targets_a_real_view(self) -> None:
        self.assertEqual(
            set(self.parser.nav_views),
            {"workspace", "history", "profiles", "settings"},
        )
        self.assertTrue(set(self.parser.nav_views).issubset(self.parser.views))
        for attrs in self.parser.nav_views.values():
            self.assertEqual(attrs.get("type"), "button")

    def test_every_settings_navigation_button_targets_a_panel(self) -> None:
        self.assertEqual(self.parser.settings_sections, self.parser.settings_panels)
        self.assertEqual(
            self.parser.settings_sections,
            {"agents", "providers", "safety", "storage", "general"},
        )

    def test_shell_navigation_binds_before_backend_hydration(self) -> None:
        init_body = self.javascript[self.javascript.index("async function init()") :]
        self.assertLess(init_body.index("bindInterface();"), init_body.index("await hydrateApplication();"))
        self.assertIn('window.addEventListener("hashchange", applyLocationRoute)', self.javascript)
        self.assertIn('setView("settings")', self.javascript)

    def test_recovery_controls_exist_for_backend_and_settings_failures(self) -> None:
        self.assertTrue(
            {"app-status-banner", "app-status-retry", "settings-feedback", "settings-retry-button"}
            .issubset(self.parser.ids)
        )

    def test_provider_templates_and_write_only_api_key_controls_are_wired(self) -> None:
        self.assertTrue(
            {"settings-provider-template", "add-provider-button"}.issubset(self.parser.ids)
        )
        self.assertIn("data-provider-secret", self.javascript)
        self.assertIn("provider_secrets", self.javascript)
        self.assertIn("custom-openai-compatible", self.javascript)

    def test_provider_draft_is_shared_with_supervisor_and_worker_routes(self) -> None:
        self.assertIn("function syncProviderDraftToRoutes()", self.javascript)
        self.assertIn("function migrateProviderReferences(config, renamedProviders)", self.javascript)
        self.assertIn("providerSelectOptions(config, supervisorProvider)", self.javascript)
        self.assertIn("providerSelectOptions(config, provider, { includeDefault: true })", self.javascript)
        self.assertIn('event.target.matches("[data-provider-field]")', self.javascript)

    def test_worker_names_are_distinct_from_internal_route_ids(self) -> None:
        self.assertIn('data-profile-field="display_name"', self.javascript)
        self.assertIn('type="hidden" data-profile-field="name"', self.javascript)
        self.assertIn("profile.display_name || profile.name", self.javascript)
        self.assertNotIn("<span>Route name</span>", self.javascript)

    def test_provider_changes_apply_provider_aware_model_defaults(self) -> None:
        self.assertIn("function applyProviderModelDefault(providerSelect)", self.javascript)
        self.assertIn("template.default_model", self.javascript)
        self.assertIn("function loadProviderCatalog(providerId", self.javascript)
        self.assertIn("modelPickerContents(config, providerId", self.javascript)
        self.assertIn("data-refresh-models", self.javascript)
        self.assertIn("settings-supervisor-model-picker", self.html)
        self.assertNotIn("<datalist", self.html)

    def test_compatible_gateways_have_live_catalogs_and_manual_model_ids(self) -> None:
        self.assertIn('template.model_catalog?.mode === "live"', self.javascript)
        self.assertIn("function providerAllowsManualModel(config, providerId)", self.javascript)
        self.assertIn("<datalist id=", self.javascript)
        self.assertIn("Gateway catalog unavailable", self.javascript)

    def test_reasoning_controls_are_model_specific(self) -> None:
        self.assertIn("modelReasoningCapability(config, providerId, modelId)", self.javascript)
        self.assertIn("data-profile-field=\"reasoning_mode\"", self.javascript)
        self.assertIn("data-profile-field=\"reasoning_effort\"", self.javascript)
        self.assertNotIn('options?.reasoning_efforts', self.javascript)

    def test_sidebar_supervisor_summary_reports_readiness_instead_of_a_model_name(self) -> None:
        self.assertIn("supervisor-route-button", self.parser.ids)
        self.assertIn("supervisor-status", self.parser.ids)
        self.assertNotIn('id="supervisor-model"', self.html)
        self.assertIn('"Needs setup"', self.javascript)

    def test_every_visible_button_has_an_interaction_hook(self) -> None:
        delegated_attributes = {
            "data-view",
            "data-settings-section",
            "data-intent-prompt",
            "data-compose-mode",
            "data-decision",
        }
        delegated_classes = {"remove-worker", "details-toggle"}
        for button in self.parser.buttons:
            classes = set((button.get("class") or "").split())
            has_hook = bool(button.get("id")) or bool(delegated_attributes & set(button))
            has_hook = has_hook or bool(delegated_classes & classes)
            self.assertTrue(has_hook, f"Button has no interaction hook: {button}")

    def test_visible_ui_copy_contains_no_chinese_characters(self) -> None:
        self.assertIsNone(re.search(r"[\u3400-\u9fff]", self.html))
        self.assertIsNone(re.search(r"[\u3400-\u9fff]", self.javascript))


if __name__ == "__main__":
    unittest.main()
