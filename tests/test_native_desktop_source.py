import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "AgentFarm.Desktop"


class NativeDesktopSourceTests(unittest.TestCase):
    def test_desktop_workspace_does_not_host_html_or_webview(self):
        sources = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in [
                DESKTOP / "MainPage.xaml",
                DESKTOP / "MainPage.xaml.cs",
                DESKTOP / "MainWindow.xaml",
                DESKTOP / "MainWindow.xaml.cs",
            ]
        )
        for forbidden in (
            "<WebView2",
            "AgentWebView",
            "CoreWebView2",
            "EnsureCoreWebView2Async",
            "NavigationCompleted",
        ):
            self.assertNotIn(forbidden, sources)

    def test_native_workspace_exposes_product_controls_and_json_client(self):
        xaml = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in (
                DESKTOP / "MainPage.xaml",
                DESKTOP / "Views" / "WorkspaceSurface.xaml",
                DESKTOP / "Views" / "ComposerSurface.xaml",
                DESKTOP / "Views" / "RunsSurface.xaml",
                DESKTOP / "Views" / "SettingsSurface.xaml",
                DESKTOP / "Views" / "ProviderSurface.xaml",
            )
        )
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )
        for control in (
            "TaskPrompt",
            "ThreadList",
            "RunsList",
            "SupervisorProviderCombo",
            "WorkerProviderCombo",
            "ProviderApiKeyBox",
            "SaveSettingsButton",
        ):
            self.assertIn(f'AutomationProperties.AutomationId="{control}"', xaml)
        self.assertIn('GetBootstrapAsync', client)
        self.assertIn('CreatePlanAsync', client)
        self.assertIn('StartFarmAsync', client)
        self.assertIn('SaveSettingsAsync', client)

    def test_native_activity_timeline_is_a_typed_surface(self):
        page_xaml = (DESKTOP / "MainPage.xaml").read_text(encoding="utf-8-sig")
        timeline_xaml = (DESKTOP / "Views" / "ActivityTimelineSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        timeline_code = (
            DESKTOP / "Views" / "ActivityTimelineSurface.xaml.cs"
        ).read_text(encoding="utf-8-sig")
        models = (DESKTOP / "Models" / "DesktopModels.cs").read_text(
            encoding="utf-8-sig"
        )

        workspace_xaml = (DESKTOP / "Views" / "WorkspaceSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("<views:WorkspaceSurface", page_xaml)
        self.assertIn("<views:ActivityTimelineSurface", workspace_xaml)
        self.assertNotIn('x:Key="TimelineTemplate"', page_xaml)
        self.assertIn('AutomationProperties.AutomationId="ActivityTimeline"', timeline_xaml)
        self.assertIn("ObservableCollection<TimelineEntry>", timeline_code)
        self.assertIn("CollectionChanged += OnCollectionChanged", timeline_code)
        self.assertIn("enum TimelineActivityActor", models)
        self.assertIn("enum TimelineActivityState", models)
        self.assertIn("ParseState", models)

    def test_native_theme_resources_cover_light_dark_and_high_contrast(self):
        app_xaml = (DESKTOP / "App.xaml").read_text(encoding="utf-8-sig")
        colors = (DESKTOP / "Themes" / "ColorResources.xaml").read_text(
            encoding="utf-8-sig"
        )
        page_xaml = (DESKTOP / "MainPage.xaml").read_text(encoding="utf-8-sig")

        self.assertIn('Source="Themes/ColorResources.xaml"', app_xaml)
        for theme in ("Light", "Dark", "HighContrast"):
            self.assertIn(f'x:Key="{theme}"', colors)
        self.assertIn("SystemColorWindowColorBrush", colors)
        self.assertIn("SystemColorWindowTextColorBrush", colors)
        self.assertNotIn("{StaticResource MutedTextBrush}", page_xaml)
        self.assertNotRegex(page_xaml, r'#[0-9A-Fa-f]{6,8}')

    def test_native_runtime_is_daemon_backed_and_recovers_after_health_failure(self):
        runtime = (DESKTOP / "Services" / "AgentRuntimeProcess.cs").read_text(
            encoding="utf-8-sig"
        )
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")

        self.assertIn('startInfo.ArgumentList.Add("--daemon")', runtime)
        self.assertIn('Path.Combine(repositoryRoot, ".agent-farm", "runtime.json")', runtime)
        self.assertIn("AGENT_FARM_RUNTIME_FINGERPRINT", runtime)
        self.assertIn("RequestRuntimeStopAsync", runtime)
        self.assertIn("WaitForRuntimeHandoffAsync", runtime)
        self.assertIn("staleDescriptor.Value.Pid", runtime)
        self.assertIn("ExpectedRuntimeFingerprint", runtime)
        self.assertIn('GetAsync<RuntimeHealth>("api/health"', client)
        self.assertIn("MonitorRuntimeAsync", page)
        self.assertIn("RecoverRuntimeAsync", page)
        self.assertIn("The repository daemon deliberately outlives the desktop window", runtime)

    def test_native_runtime_negotiates_protocol_capabilities_before_bootstrap(self):
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        models = (DESKTOP / "Models" / "DesktopModels.cs").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn("InitializeProtocolAsync", client)
        self.assertLess(page.index("InitializeProtocolAsync"), page.index("GetBootstrapAsync"))
        self.assertIn("RequiredProtocolCapabilities", page)
        self.assertIn("ProtocolInitializeRequest", models)
        self.assertIn("ProtocolInitializeResponse", models)

    def test_native_workspace_has_bounded_resizable_side_panes(self):
        xaml = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in (
                DESKTOP / "MainPage.xaml",
                DESKTOP / "Views" / "WorkspaceSurface.xaml",
            )
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        project = (DESKTOP / "AgentFarm.Desktop.csproj").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("CommunityToolkit.WinUI.Controls.Sizers", project)
        self.assertIn('x:Name="LeftPaneSplitter"', xaml)
        self.assertIn('x:Name="ExecutionPaneSplitter"', xaml)
        self.assertIn('x:Name="LeftPaneColumn"', xaml)
        self.assertIn('x:Name="ExecutionPaneColumn"', xaml)
        self.assertIn('Property="ResizeBehavior" Value="PreviousAndNext"', xaml)
        self.assertIn('x:Name="ToggleNavigationPaneButton"', xaml)
        self.assertIn('AutomationProperties.AutomationId="ToggleExecutionPaneButton"', xaml)
        self.assertIn("NavigationCollapseBreakpoint", page)
        self.assertIn("ExecutionCollapseBreakpoint", page)
        self.assertIn("SetNavigationPaneCollapsed", page)
        self.assertIn("SetExecutionPaneCollapsed", page)

    def test_native_thread_lifecycle_has_search_and_all_management_actions(self):
        xaml = (DESKTOP / "MainPage.xaml").read_text(encoding="utf-8-sig")
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn('AutomationProperties.AutomationId="ThreadSearchBox"', xaml)
        for handler in (
            "OnResumeThread",
            "OnRenameThread",
            "OnForkThread",
            "OnArchiveThread",
            "OnDeleteThread",
        ):
            self.assertIn(handler, xaml)
            self.assertIn(handler, page)
        for method in (
            "RenameThreadAsync",
            "ArchiveThreadAsync",
            "ForkThreadAsync",
            "DeleteThreadAsync",
        ):
            self.assertIn(method, client)

    def test_frozen_native_backend_excludes_browser_assets(self):
        spec = (ROOT / "packaging" / "AgentFarmBackend.spec").read_text(
            encoding="utf-8-sig"
        )
        desktop_server = (ROOT / "agent_farm" / "desktop_server.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("datas=[]", spec)
        self.assertNotIn("collect_data_files", spec)
        self.assertIn("serve_assets=False", desktop_server)

    def test_native_json_posts_include_content_length(self):
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )
        self.assertNotIn("PostAsJsonAsync", client)
        self.assertIn("JsonSerializer.SerializeToUtf8Bytes", client)
        self.assertIn("content.Headers.ContentLength = payload.LongLength", client)

    def test_desktop_and_native_models_have_no_inference_deadline(self):
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )
        xaml = (DESKTOP / "Views" / "SettingsSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        supervisor = (ROOT / "agent_farm" / "supervisor.py").read_text(
            encoding="utf-8-sig"
        )
        native_agent = (ROOT / "agent_farm" / "native_agent.py").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn("System.Threading.Timeout.InfiniteTimeSpan", client)
        self.assertNotIn("TimeSpan.FromSeconds(35)", client)
        self.assertIn("Model inference has no time limit", xaml)
        self.assertNotIn('Header="Timeout (seconds)"', xaml)
        self.assertNotIn("timeout_seconds=config.supervisor_timeout_seconds", supervisor)
        self.assertIn("timeout_seconds=None", native_agent)

    def test_farm_submission_uses_a_wire_only_worker_payload(self):
        models = (DESKTOP / "Models" / "DesktopModels.cs").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        self.assertIn("public FarmPlanPayload Plan", models)
        self.assertIn("FarmWorkerPayload.FromPlanItem", models)
        self.assertIn("[JsonIgnore]\n    public string RouteLabel", models)
        self.assertNotIn("RouteLabel = worker.RouteLabel", models)
        self.assertIn("FarmSubmission.FromPlan(_currentPlan", page)

    def test_native_workspace_renders_incremental_agent_output(self):
        xaml = (DESKTOP / "Views" / "ExecutionSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn('AutomationProperties.AutomationId="LiveOutputList"', xaml)
        self.assertIn('x:Key="LiveAgentTemplate"', xaml)
        self.assertIn("GetPlanJobEventsAsync", client)
        self.assertIn("GetFarmJobEventsAsync", client)
        self.assertIn("StreamPlanJobEventsAsync", client)
        self.assertIn("StreamFarmJobEventsAsync", client)
        self.assertIn("HttpCompletionOption.ResponseHeadersRead", client)
        self.assertNotIn("TimeSpan.FromMilliseconds(350)", page)
        self.assertIn('case "model.output.delta"', page)
        self.assertIn("agent.AppendOutput(jobEvent.Delta)", page)

    def test_native_workspace_exposes_blocking_runtime_approvals(self):
        xaml = (DESKTOP / "Views" / "ExecutionSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn('AutomationProperties.AutomationId="ApprovalList"', xaml)
        self.assertIn('x:Key="ApprovalTemplate"', xaml)
        self.assertIn('Content="Allow once"', xaml)
        self.assertIn('Content="Allow for run"', xaml)
        self.assertIn('Content="Deny"', xaml)
        self.assertIn('Content="Cancel"', xaml)
        self.assertIn('case "approval.requested"', page)
        self.assertIn("ResolveApprovalAsync", client)
        self.assertIn("GetPendingApprovalsAsync", client)

    def test_native_workspace_can_cancel_active_planning_farms_and_workers(self):
        xaml = (DESKTOP / "Views" / "ExecutionSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn('AutomationProperties.AutomationId="CancelRunButton"', xaml)
        self.assertIn('AutomationProperties.Name="Cancel this Worker"', xaml)
        self.assertIn("OnCancelRun", page)
        self.assertIn("OnExecutionWorkerCancelRequested", page)
        self.assertIn("CancelPlanAsync", client)
        self.assertIn("CancelFarmAsync", client)
        self.assertIn("CancelWorkerAsync", client)

    def test_native_worker_dag_has_progress_retry_cancel_and_recovery_controls(self):
        xaml = (DESKTOP / "Views" / "ExecutionSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        models = (DESKTOP / "Models" / "DesktopModels.cs").read_text(
            encoding="utf-8-sig"
        )
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )
        plans = (ROOT / "agent_farm" / "plans.py").read_text(encoding="utf-8-sig")
        farm = (ROOT / "agent_farm" / "farm.py").read_text(encoding="utf-8-sig")

        self.assertIn("DependencyLabel", xaml)
        self.assertIn("<ProgressBar", xaml)
        self.assertIn('AutomationProperties.Name="Retry this Worker"', xaml)
        self.assertIn('AutomationProperties.Name="Cancel this Worker"', xaml)
        self.assertIn("RetryWorkerAsync", client)
        self.assertIn("OnExecutionWorkerRetryRequested", page)
        self.assertIn('[JsonPropertyName("depends_on")]', models)
        self.assertIn("Worker dependency graph contains a cycle", plans)
        self.assertIn('"type": "worker.blocked"', farm)
        self.assertIn("FIRST_COMPLETED", farm)

    def test_native_workspace_reviews_applies_merges_and_rolls_back_candidates(self):
        xaml = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in (
                DESKTOP / "Views" / "RunsSurface.xaml",
                DESKTOP / "Views" / "ReviewSurface.xaml",
            )
        )
        settings_xaml = (DESKTOP / "Views" / "SettingsSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        models = (DESKTOP / "Models" / "DesktopModels.cs").read_text(
            encoding="utf-8-sig"
        )
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )

        for control in (
            "CandidateACombo",
            "CandidateBCombo",
            "ReviewDiffA",
            "ReviewDiffB",
            "CheckpointCombo",
            "ApplyCandidateButton",
            "MergeCandidateButton",
            "RollbackCandidateButton",
        ):
            self.assertIn(f'AutomationProperties.AutomationId="{control}"', xaml)
        self.assertIn('x:Name="ReviewDecisionSummary"', xaml)
        self.assertIn('x:Name="ReviewCostSummary"', xaml)
        self.assertIn("EvidenceSummary", models)
        self.assertIn("FormatUsageBucket", page)
        self.assertIn("FormatEconomics", page)
        for control in (
            "FarmBudgetBox",
            "MonthlyBudgetBox",
            "BudgetPolicyCombo",
            "BudgetWarningRatioBox",
            "WorkerBudgetBox",
            "WorkerCapabilityCombo",
        ):
            self.assertIn(
                f'AutomationProperties.AutomationId="{control}"', settings_xaml
            )
        self.assertIn("LoadRunReviewAsync", page)
        self.assertIn("ConfirmReviewActionAsync", page)
        self.assertIn("ApplyCandidateAsync", client)
        self.assertIn("MergeCandidateAsync", client)
        self.assertIn("RollbackCandidateAsync", client)

    def test_native_composer_supports_real_file_attachments(self):
        page_xaml = (DESKTOP / "MainPage.xaml").read_text(encoding="utf-8-sig")
        xaml = (DESKTOP / "Views" / "ComposerSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        composer = (DESKTOP / "Views" / "ComposerSurface.xaml.cs").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")
        client = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(
            encoding="utf-8-sig"
        )
        models = (DESKTOP / "Models" / "DesktopModels.cs").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn('AutomationProperties.AutomationId="AttachFilesButton"', xaml)
        self.assertIn('AutomationProperties.AutomationId="AttachmentTray"', xaml)
        workspace_xaml = (DESKTOP / "Views" / "WorkspaceSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("<views:WorkspaceSurface", page_xaml)
        self.assertIn("<views:ComposerSurface", workspace_xaml)
        self.assertNotIn('x:Name="TaskPrompt"', page_xaml)
        self.assertIn('DragOver="OnDragOver"', xaml)
        self.assertIn('Drop="OnDrop"', xaml)
        self.assertIn("FilesDropped", composer)
        self.assertIn("AttachmentRemovalRequested", composer)
        self.assertIn("PickMultipleFilesAsync", page)
        self.assertIn("AddAttachmentAsync", client)
        self.assertIn("RemoveAttachmentAsync", client)
        self.assertIn('[JsonPropertyName("attachments")]', models)
        self.assertIn("Attachments = ViewModel.Attachments.Select", page)

    def test_main_page_composes_dedicated_product_surfaces(self):
        page = (DESKTOP / "MainPage.xaml").read_text(encoding="utf-8-sig")
        workspace = (DESKTOP / "Views" / "WorkspaceSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        expected = (
            "WorkspaceSurface",
            "ActivityTimelineSurface",
            "ComposerSurface",
            "ExecutionSurface",
            "ReviewSurface",
            "RunsSurface",
            "SettingsSurface",
            "ProviderSurface",
        )
        surface_sources = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in (DESKTOP / "Views").glob("*.xaml")
        )
        for surface in expected:
            self.assertIn(f"x:Class=\"AgentFarm_Desktop.Views.{surface}\"", surface_sources)
        self.assertIn("<views:WorkspaceSurface", page)
        self.assertIn("<views:RunsSurface", page)
        self.assertIn("<views:SettingsSurface", page)
        self.assertIn("<views:ActivityTimelineSurface", workspace)
        self.assertLess(page.count("\n"), 400)

    def test_native_commands_and_state_transitions_use_testable_mvvm_core(self):
        page_xaml = (DESKTOP / "MainPage.xaml").read_text(encoding="utf-8-sig")
        execution_xaml = (DESKTOP / "Views" / "ExecutionSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        review_xaml = (DESKTOP / "Views" / "ReviewSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        state = (DESKTOP / "ViewModels" / "WorkspaceViewModels.cs").read_text(
            encoding="utf-8-sig"
        )
        core_project = (ROOT / "AgentFarm.Core" / "AgentFarm.Core.csproj").read_text(
            encoding="utf-8-sig"
        )
        state_tests = (
            ROOT / "AgentFarm.Desktop.StateTests" / "Program.cs"
        ).read_text(encoding="utf-8-sig")

        self.assertIn('Command="{x:Bind ViewModel.ShowWorkspaceCommand}"', page_xaml)
        self.assertIn('Command="{x:Bind ViewModel.RequestStartCommand}"', execution_xaml)
        self.assertIn('Command="{x:Bind ViewModel.RequestApplyCommand}"', review_xaml)
        self.assertIn("class ShellViewModel", state)
        self.assertIn("class ExecutionViewModel", state)
        self.assertIn("class ReviewViewModel", state)
        self.assertIn("class SettingsViewModel", state)
        self.assertIn("AgentFarm.Core", str(ROOT / "AgentFarm.Core"))
        self.assertIn("WorkspaceViewModels.cs", core_project)
        self.assertIn("MVVM state tests passed", state_tests)

    def test_native_surfaces_use_shared_fluent_design_tokens(self):
        app_xaml = (DESKTOP / "App.xaml").read_text(encoding="utf-8-sig")
        tokens = (DESKTOP / "Themes" / "DesignTokens.xaml").read_text(
            encoding="utf-8-sig"
        )
        surfaces = [DESKTOP / "MainPage.xaml", *sorted((DESKTOP / "Views").glob("*.xaml"))]
        source = "\n".join(path.read_text(encoding="utf-8-sig") for path in surfaces)

        self.assertIn('Source="Themes/DesignTokens.xaml"', app_xaml)
        self.assertIn('x:Key="BodyFontSize"', tokens)
        self.assertIn('x:Key="CardCornerRadius"', tokens)
        self.assertNotRegex(source, r'FontSize="\d')
        self.assertNotRegex(source, r'CornerRadius="\d')
        self.assertNotRegex(source, r'Property="FontSize" Value="\d')
        self.assertNotRegex(source, r'Property="CornerRadius" Value="\d')
        self.assertNotRegex(source, r'#[0-9A-Fa-f]{6,8}')

    def test_native_ui_strings_are_backed_by_localization_resources(self):
        resources = (DESKTOP / "Strings" / "en-US" / "Resources.resw").read_text(
            encoding="utf-8-sig"
        )
        xaml = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in [DESKTOP / "MainPage.xaml", *sorted((DESKTOP / "Views").glob("*.xaml"))]
        )

        self.assertGreaterEqual(xaml.count("x:Uid="), 65)
        for resource in (
            "NewTaskLabel.Text",
            "TaskPrompt.PlaceholderText",
            "SupervisorProvider.Header",
            "ProviderApiKey.Header",
            "RunsEmpty.Text",
            "ReconnectRuntime.Content",
        ):
            self.assertIn(f'name="{resource}"', resources)

    def test_native_keyboard_focus_and_automation_contract_is_explicit(self):
        xaml = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in [DESKTOP / "MainPage.xaml", *sorted((DESKTOP / "Views").glob("*.xaml"))]
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")

        self.assertIn('Key="N" Modifiers="Control"', xaml)
        self.assertIn('Key="F" Modifiers="Control"', xaml)
        self.assertIn('Key="Left" Modifiers="Menu"', xaml)
        self.assertIn('Key="F6"', xaml)
        self.assertIn('AutomationProperties.AutomationId="SettingsBackButton"', xaml)
        self.assertIn("OnBackAccelerator", page)
        self.assertIn("OnNextRegionAccelerator", page)
        self.assertIn("FocusActiveSurface", page)
        for control in (
            "ThreadSearchBox",
            "TaskPrompt",
            "ExecutionSelector",
            "ProviderApiKeyBox",
            "ReviewDiffA",
            "RuntimeStateBar",
        ):
            self.assertIn(f'AutomationProperties.AutomationId="{control}"', xaml)
        self.assertIn('AutomationProperties.Name="Provider API key"', xaml)

    def test_native_notifications_and_runtime_recovery_states_are_visible(self):
        workspace = (DESKTOP / "Views" / "WorkspaceSurface.xaml").read_text(
            encoding="utf-8-sig"
        )
        notifications = (DESKTOP / "Services" / "NotificationService.cs").read_text(
            encoding="utf-8-sig"
        )
        state = (DESKTOP / "ViewModels" / "WorkspaceViewModels.cs").read_text(
            encoding="utf-8-sig"
        )
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8-sig")

        self.assertIn('AutomationProperties.AutomationId="NotificationCenterButton"', workspace)
        self.assertIn('AutomationProperties.AutomationId="NotificationQueue"', workspace)
        self.assertIn('AutomationProperties.AutomationId="RuntimeStateBar"', workspace)
        self.assertIn("AppNotificationManager.Default.Show", notifications)
        for state_name in ("Loading", "Ready", "Degraded", "Offline", "Recovering"):
            self.assertIn(state_name, state)
        self.assertIn("OnRuntimeRecoveryRequested", page)
        self.assertIn("_notifications.Enqueue", page)

    def test_native_diagnostics_are_correlated_and_user_exportable(self):
        api = (DESKTOP / "Services" / "AgentFarmApiClient.cs").read_text(encoding="utf-8")
        settings = (DESKTOP / "Views" / "SettingsSurface.xaml").read_text(encoding="utf-8")
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8")
        models = (DESKTOP / "Models" / "DesktopModels.cs").read_text(encoding="utf-8")
        app = (DESKTOP / "App.xaml.cs").read_text(encoding="utf-8")

        self.assertIn('"X-Correlation-ID"', api)
        self.assertIn("ExportDiagnosticsAsync", api)
        self.assertIn('AutomationProperties.AutomationId="ExportDiagnosticsButton"', settings)
        self.assertIn('AutomationProperties.AutomationId="DiagnosticBundlePath"', settings)
        self.assertIn("OnDiagnosticsExportRequested", page)
        self.assertIn("RecoveryReport", models)
        self.assertIn("desktop-events.jsonl", app)
        self.assertIn("AddDesktopDiagnosticsToBundle", app)
        self.assertIn("App.AddDesktopDiagnosticsToBundle", page)

    def test_native_update_flow_is_channel_aware_and_checksum_verified(self):
        service = (DESKTOP / "Services" / "UpdateService.cs").read_text(encoding="utf-8")
        settings = (DESKTOP / "Views" / "SettingsSurface.xaml").read_text(encoding="utf-8")
        page = (DESKTOP / "MainPage.xaml.cs").read_text(encoding="utf-8")
        policy = (ROOT / "AgentFarm.Core" / "UpdatePolicy.cs").read_text(encoding="utf-8")

        self.assertIn("api.github.com/repos/Freddy-Hexas/Agent-Farm/releases", service)
        self.assertIn("SHA256.HashDataAsync", service)
        self.assertIn("Launcher.LaunchFileAsync", service)
        self.assertIn("IsApprovedReleaseUri", policy)
        self.assertIn("AutomaticCheckCadence", policy)
        for control in (
            "ReleaseChannelCombo",
            "CheckUpdatesButton",
            "InstallUpdateButton",
            "UpdateStatusText",
        ):
            self.assertIn(f'AutomationProperties.AutomationId="{control}"', settings)
        self.assertIn("CheckForUpdatesAsync(manual: false)", page)


if __name__ == "__main__":
    unittest.main()
