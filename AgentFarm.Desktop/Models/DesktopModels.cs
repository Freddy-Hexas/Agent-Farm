using CommunityToolkit.Mvvm.ComponentModel;
using System.Collections.ObjectModel;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace AgentFarm_Desktop.Models;

public sealed class RuntimeHealth
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("protocol_version")]
    public int ProtocolVersion { get; set; }

    [JsonPropertyName("pid")]
    public int ProcessId { get; set; }

    [JsonPropertyName("repository")]
    public string Repository { get; set; } = string.Empty;

    [JsonPropertyName("started_at")]
    public string StartedAt { get; set; } = string.Empty;

    [JsonPropertyName("runtime_fingerprint")]
    public string RuntimeFingerprint { get; set; } = string.Empty;

    [JsonPropertyName("recovery")]
    public RecoveryReport? Recovery { get; set; }
}

public sealed class ProtocolInitializeRequest
{
    [JsonPropertyName("client_name")]
    public string ClientName { get; set; } = "AgentFarm.Desktop";

    [JsonPropertyName("client_version")]
    public string ClientVersion { get; set; } = "0.4";

    [JsonPropertyName("protocol_versions")]
    public List<int> ProtocolVersions { get; set; } = [1];

    [JsonPropertyName("capabilities")]
    public List<string> Capabilities { get; set; } = [];

    [JsonPropertyName("required_capabilities")]
    public List<string> RequiredCapabilities { get; set; } = [];
}

public sealed class ProtocolInitializeResponse
{
    [JsonPropertyName("session_id")]
    public string SessionId { get; set; } = string.Empty;

    [JsonPropertyName("protocol_version")]
    public int ProtocolVersion { get; set; }

    [JsonPropertyName("server_capabilities")]
    public List<string> ServerCapabilities { get; set; } = [];

    [JsonPropertyName("enabled_capabilities")]
    public List<string> EnabledCapabilities { get; set; } = [];

    [JsonPropertyName("message_schemas")]
    public Dictionary<string, ProtocolSchemaReference> MessageSchemas { get; set; } = [];
}

public sealed class ProtocolSchemaReference
{
    [JsonPropertyName("version")]
    public int Version { get; set; }

    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;
}

public sealed class BootstrapResponse
{
    [JsonPropertyName("app")]
    public AppMetadata App { get; set; } = new();

    [JsonPropertyName("repository")]
    public RepositoryMetadata Repository { get; set; } = new();

    [JsonPropertyName("limits")]
    public RuntimeLimits Limits { get; set; } = new();

    [JsonPropertyName("defaults")]
    public RuntimeDefaults Defaults { get; set; } = new();

    [JsonPropertyName("supervisor")]
    public SupervisorMetadata Supervisor { get; set; } = new();

    [JsonPropertyName("profiles")]
    public List<WorkerProfileSummary> Profiles { get; set; } = [];

    [JsonPropertyName("threads")]
    public List<ThreadSummary> Threads { get; set; } = [];

    [JsonPropertyName("farms")]
    public List<FarmSummary> Farms { get; set; } = [];

    [JsonPropertyName("recovery")]
    public RecoveryReport? Recovery { get; set; }
}

public sealed class RecoveryReport
{
    [JsonPropertyName("detected")]
    public bool Detected { get; set; }

    [JsonPropertyName("previous_session_id")]
    public string? PreviousSessionId { get; set; }

    [JsonPropertyName("previous_started_at")]
    public string? PreviousStartedAt { get; set; }

    [JsonPropertyName("interrupted_jobs")]
    public int InterruptedJobs { get; set; }

    [JsonPropertyName("message")]
    public string Message { get; set; } = string.Empty;
}

public sealed class DiagnosticBundleResponse
{
    [JsonPropertyName("path")]
    public string Path { get; set; } = string.Empty;

    [JsonPropertyName("size_bytes")]
    public long SizeBytes { get; set; }

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; set; } = string.Empty;
}

public sealed class UpdateCheckResult
{
    public string Channel { get; set; } = "stable";
    public Version CurrentVersion { get; set; } = new(0, 0);
    public Version? AvailableVersion { get; set; }
    public bool IsAvailable { get; set; }
    public string? PackageUrl { get; set; }
    public string? ChecksumsUrl { get; set; }
    public string Message { get; set; } = string.Empty;

    public static UpdateCheckResult None(string channel, Version currentVersion, string message) =>
        new() { Channel = channel, CurrentVersion = currentVersion, Message = message };
}

public sealed class AppMetadata
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "Agent Farm";

    [JsonPropertyName("version")]
    public string Version { get; set; } = string.Empty;
}

public sealed class RepositoryMetadata
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    [JsonPropertyName("path")]
    public string Path { get; set; } = string.Empty;

    [JsonPropertyName("branch")]
    public string Branch { get; set; } = string.Empty;
}

public sealed class RuntimeLimits
{
    [JsonPropertyName("max_parallel_workers")]
    public int MaxParallelWorkers { get; set; } = 3;

    [JsonPropertyName("max_changed_files")]
    public int MaxChangedFiles { get; set; }

    [JsonPropertyName("max_diff_lines")]
    public int MaxDiffLines { get; set; }
}

public sealed class RuntimeDefaults
{
    [JsonPropertyName("profile")]
    public string Profile { get; set; } = string.Empty;

    [JsonPropertyName("allowed_paths")]
    public List<string> AllowedPaths { get; set; } = [];

    [JsonPropertyName("forbidden_paths")]
    public List<string> ForbiddenPaths { get; set; } = [];

    [JsonPropertyName("test_commands")]
    public List<string> TestCommands { get; set; } = [];
}

public sealed class SupervisorMetadata
{
    [JsonPropertyName("model")]
    public string Model { get; set; } = string.Empty;

    [JsonPropertyName("provider")]
    public string Provider { get; set; } = string.Empty;

    [JsonPropertyName("mode")]
    public string Mode { get; set; } = string.Empty;

    [JsonPropertyName("backend")]
    public string Backend { get; set; } = string.Empty;

    [JsonPropertyName("ready")]
    public bool Ready { get; set; }
}

public sealed class WorkerProfileSummary
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = string.Empty;

    [JsonPropertyName("model")]
    public string Model { get; set; } = string.Empty;

    [JsonPropertyName("provider")]
    public string Provider { get; set; } = string.Empty;

    [JsonPropertyName("provider_name")]
    public string? ProviderName { get; set; }

    [JsonPropertyName("is_default")]
    public bool IsDefault { get; set; }

    public string RouteDescription => $"{ProviderName ?? Provider} · {Model}";
}

public sealed class ThreadListResponse
{
    [JsonPropertyName("threads")]
    public List<ThreadSummary> Threads { get; set; } = [];
}

public sealed class ThreadSummary
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("thread_id")]
    public string ThreadId { get; set; } = string.Empty;

    [JsonPropertyName("title")]
    public string Title { get; set; } = "New task";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "idle";

    [JsonPropertyName("updated_at")]
    public DateTimeOffset? UpdatedAt { get; set; }

    [JsonPropertyName("turn_count")]
    public int TurnCount { get; set; }

    public string StatusLabel => Status.Replace('_', ' ');

    public string UpdatedLabel => UpdatedAt?.ToLocalTime().ToString("MMM d, HH:mm") ?? "Just now";
}

public sealed class ThreadDocument
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("thread_id")]
    public string ThreadId { get; set; } = string.Empty;

    [JsonPropertyName("title")]
    public string Title { get; set; } = "New task";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "idle";

    [JsonPropertyName("turns")]
    public List<ThreadTurn> Turns { get; set; } = [];
}

public sealed class ThreadTurn
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("turn_id")]
    public string TurnId { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("items")]
    public List<ThreadItem> Items { get; set; } = [];
}

public sealed class ThreadItem
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("item_id")]
    public string ItemId { get; set; } = string.Empty;

    [JsonPropertyName("type")]
    public string Type { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("payload")]
    public JsonElement Payload { get; set; }
}

public sealed class CreateThreadRequest
{
    [JsonPropertyName("title")]
    public string Title { get; set; } = "New task";
}

public sealed class PlanRequest
{
    [JsonPropertyName("request")]
    public string Request { get; set; } = string.Empty;

    [JsonPropertyName("task_id")]
    public string? TaskId { get; set; }

    [JsonPropertyName("base_ref")]
    public string BaseRef { get; set; } = "HEAD";

    [JsonPropertyName("worker_count")]
    public int WorkerCount { get; set; } = 3;

    [JsonPropertyName("thread_id")]
    public string? ThreadId { get; set; }

    [JsonPropertyName("attachments")]
    public List<string> Attachments { get; set; } = [];
}

public sealed class AddAttachmentRequest
{
    [JsonPropertyName("local_path")]
    public string LocalPath { get; set; } = string.Empty;
}

public sealed class AttachmentItem
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    [JsonPropertyName("size")]
    public long Size { get; set; }

    [JsonPropertyName("mime_type")]
    public string MimeType { get; set; } = string.Empty;

    [JsonPropertyName("kind")]
    public string Kind { get; set; } = string.Empty;

    [JsonPropertyName("content_available")]
    public bool ContentAvailable { get; set; }

    [JsonPropertyName("truncated")]
    public bool Truncated { get; set; }

    [JsonIgnore]
    public string SizeLabel => Size >= 1_048_576
        ? $"{Size / 1_048_576d:0.#} MB"
        : $"{Math.Max(1, Size / 1024d):0.#} KB";

    [JsonIgnore]
    public string Glyph => Kind switch
    {
        "image" => "\uEB9F",
        "code" => "\uE943",
        "data" => "\uE80F",
        _ => "\uE8A5",
    };
}

public sealed class PlanJobResponse
{
    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("thread_id")]
    public string? ThreadId { get; set; }

    [JsonPropertyName("turn_id")]
    public string? TurnId { get; set; }

    [JsonPropertyName("plan")]
    public WorkerPlan? Plan { get; set; }

    [JsonPropertyName("error")]
    public ApiError? Error { get; set; }
}

public sealed class WorkerPlan
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("task_id")]
    public string TaskId { get; set; } = string.Empty;

    [JsonPropertyName("base_ref")]
    public string BaseRef { get; set; } = "HEAD";

    [JsonPropertyName("max_parallel")]
    public int? MaxParallel { get; set; }

    [JsonPropertyName("workers")]
    public List<WorkerPlanItem> Workers { get; set; } = [];

    [JsonPropertyName("deliverable")]
    public DeliverablePlan? Deliverable { get; set; }
}

public sealed class WorkerPlanItem
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("role")]
    public string Role { get; set; } = string.Empty;

    [JsonPropertyName("profile")]
    public string Profile { get; set; } = string.Empty;

    [JsonPropertyName("goal")]
    public string Goal { get; set; } = string.Empty;

    [JsonPropertyName("complexity")]
    public string Complexity { get; set; } = "standard";

    [JsonPropertyName("attachments")]
    public List<string> Attachments { get; set; } = [];

    [JsonPropertyName("depends_on")]
    public List<string> DependsOn { get; set; } = [];

    [JsonPropertyName("allowed_paths")]
    public List<string> AllowedPaths { get; set; } = [];

    [JsonPropertyName("forbidden_paths")]
    public List<string> ForbiddenPaths { get; set; } = [];

    [JsonPropertyName("test_commands")]
    public List<string> TestCommands { get; set; } = [];

    [JsonPropertyName("acceptance")]
    public List<string> Acceptance { get; set; } = [];

    [JsonPropertyName("context")]
    public string Context { get; set; } = string.Empty;

    [JsonIgnore]
    public string DependencyLabel => DependsOn.Count == 0
        ? "Runs in parallel"
        : "After " + string.Join(", ", DependsOn);

    [JsonIgnore]
    public string RouteLabel => string.IsNullOrWhiteSpace(Profile) ? Id : $"{Profile} · {Id}";
}

public sealed class DeliverablePlan
{
    [JsonPropertyName("path")]
    public string Path { get; set; } = string.Empty;

    [JsonPropertyName("instructions")]
    public string Instructions { get; set; } = string.Empty;
}

public sealed class FarmSubmission
{
    [JsonPropertyName("plan")]
    public FarmPlanPayload Plan { get; set; } = new();

    [JsonPropertyName("thread_id")]
    public string? ThreadId { get; set; }

    [JsonPropertyName("turn_id")]
    public string? TurnId { get; set; }

    [JsonPropertyName("attachments")]
    public List<string> Attachments { get; set; } = [];

    public static FarmSubmission FromPlan(
        WorkerPlan plan,
        string? threadId,
        string? turnId,
        IEnumerable<string>? attachments = null) => new()
    {
        Plan = FarmPlanPayload.FromPlan(plan),
        ThreadId = threadId,
        TurnId = turnId,
        Attachments = attachments?.ToList() ?? [],
    };
}

public sealed class FarmPlanPayload
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("task_id")]
    public string TaskId { get; set; } = string.Empty;

    [JsonPropertyName("base_ref")]
    public string BaseRef { get; set; } = "HEAD";

    [JsonPropertyName("max_parallel")]
    public int? MaxParallel { get; set; }

    [JsonPropertyName("workers")]
    public List<FarmWorkerPayload> Workers { get; set; } = [];

    [JsonPropertyName("deliverable")]
    public FarmDeliverablePayload? Deliverable { get; set; }

    public static FarmPlanPayload FromPlan(WorkerPlan plan) => new()
    {
        SchemaVersion = plan.SchemaVersion,
        TaskId = plan.TaskId,
        BaseRef = plan.BaseRef,
        MaxParallel = plan.MaxParallel,
        Workers = plan.Workers.Select(FarmWorkerPayload.FromPlanItem).ToList(),
        Deliverable = plan.Deliverable is null
            ? null
            : FarmDeliverablePayload.FromPlan(plan.Deliverable),
    };
}

public sealed class FarmWorkerPayload
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("role")]
    public string Role { get; set; } = string.Empty;

    [JsonPropertyName("profile")]
    public string Profile { get; set; } = string.Empty;

    [JsonPropertyName("goal")]
    public string Goal { get; set; } = string.Empty;

    [JsonPropertyName("complexity")]
    public string Complexity { get; set; } = "standard";

    [JsonPropertyName("attachments")]
    public List<string> Attachments { get; set; } = [];

    [JsonPropertyName("depends_on")]
    public List<string> DependsOn { get; set; } = [];

    [JsonPropertyName("allowed_paths")]
    public List<string> AllowedPaths { get; set; } = [];

    [JsonPropertyName("forbidden_paths")]
    public List<string> ForbiddenPaths { get; set; } = [];

    [JsonPropertyName("test_commands")]
    public List<string> TestCommands { get; set; } = [];

    [JsonPropertyName("acceptance")]
    public List<string> Acceptance { get; set; } = [];

    [JsonPropertyName("context")]
    public string Context { get; set; } = string.Empty;

    public static FarmWorkerPayload FromPlanItem(WorkerPlanItem worker) => new()
    {
        Id = worker.Id,
        Role = worker.Role,
        Profile = worker.Profile,
        Goal = worker.Goal,
        Complexity = worker.Complexity,
        Attachments = [.. worker.Attachments],
        DependsOn = [.. worker.DependsOn],
        AllowedPaths = [.. worker.AllowedPaths],
        ForbiddenPaths = [.. worker.ForbiddenPaths],
        TestCommands = [.. worker.TestCommands],
        Acceptance = [.. worker.Acceptance],
        Context = worker.Context,
    };
}

public sealed class FarmDeliverablePayload
{
    [JsonPropertyName("path")]
    public string Path { get; set; } = string.Empty;

    [JsonPropertyName("instructions")]
    public string Instructions { get; set; } = string.Empty;

    public static FarmDeliverablePayload FromPlan(DeliverablePlan deliverable) => new()
    {
        Path = deliverable.Path,
        Instructions = deliverable.Instructions,
    };
}

public sealed class FarmJobResponse
{
    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = string.Empty;

    [JsonPropertyName("task_id")]
    public string TaskId { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("farm_id")]
    public string? FarmId { get; set; }

    [JsonPropertyName("thread_id")]
    public string? ThreadId { get; set; }

    [JsonPropertyName("turn_id")]
    public string? TurnId { get; set; }

    [JsonPropertyName("error")]
    public ApiError? Error { get; set; }
}

public sealed class JobEventBatch
{
    [JsonPropertyName("events")]
    public List<JobEvent> Events { get; set; } = [];

    [JsonPropertyName("next_sequence")]
    public long NextSequence { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;
}

public sealed class JobStreamEnvelope
{
    [JsonPropertyName("protocol_version")]
    public int ProtocolVersion { get; set; }

    [JsonPropertyName("stream")]
    public string Stream { get; set; } = string.Empty;

    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = string.Empty;

    [JsonPropertyName("sequence")]
    public long Sequence { get; set; }

    [JsonPropertyName("event")]
    public JobEvent Event { get; set; } = new();
}

public sealed class JobEvent
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("sequence")]
    public long Sequence { get; set; }

    [JsonPropertyName("timestamp")]
    public DateTimeOffset Timestamp { get; set; }

    [JsonPropertyName("type")]
    public string Type { get; set; } = string.Empty;

    [JsonPropertyName("agent_id")]
    public string AgentId { get; set; } = string.Empty;

    [JsonPropertyName("agent_kind")]
    public string AgentKind { get; set; } = string.Empty;

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = string.Empty;

    [JsonPropertyName("profile")]
    public string Profile { get; set; } = string.Empty;

    [JsonPropertyName("provider")]
    public string Provider { get; set; } = string.Empty;

    [JsonPropertyName("model")]
    public string Model { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("depends_on")]
    public List<string> DependsOn { get; set; } = [];

    [JsonPropertyName("progress")]
    public double? Progress { get; set; }

    [JsonPropertyName("turn")]
    public int? Turn { get; set; }

    [JsonPropertyName("delta")]
    public string Delta { get; set; } = string.Empty;

    [JsonPropertyName("error")]
    public string Error { get; set; } = string.Empty;

    [JsonPropertyName("item")]
    public JsonElement Item { get; set; }

    [JsonPropertyName("approval")]
    public JsonElement Approval { get; set; }
}

public sealed class ApprovalListResponse
{
    [JsonPropertyName("approvals")]
    public List<ApprovalRequest> Approvals { get; set; } = [];
}

public sealed class ApprovalRequest
{
    [JsonPropertyName("protocol_version")]
    public int ProtocolVersion { get; set; }

    [JsonPropertyName("approval_id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("job_kind")]
    public string JobKind { get; set; } = string.Empty;

    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = string.Empty;

    [JsonPropertyName("kind")]
    public string Kind { get; set; } = string.Empty;

    [JsonPropertyName("scope")]
    public string Scope { get; set; } = string.Empty;

    [JsonPropertyName("tool_name")]
    public string ToolName { get; set; } = string.Empty;

    [JsonPropertyName("title")]
    public string Title { get; set; } = "Approval required";

    [JsonPropertyName("description")]
    public string Description { get; set; } = string.Empty;

    [JsonPropertyName("details")]
    public JsonElement Details { get; set; }

    [JsonPropertyName("agent_id")]
    public string AgentId { get; set; } = string.Empty;

    [JsonPropertyName("display_name")]
    public string AgentName { get; set; } = string.Empty;

    [JsonPropertyName("agent_kind")]
    public string AgentKind { get; set; } = string.Empty;

    [JsonPropertyName("profile")]
    public string Profile { get; set; } = string.Empty;

    [JsonPropertyName("provider")]
    public string Provider { get; set; } = string.Empty;

    [JsonPropertyName("model")]
    public string Model { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("decision")]
    public string? Decision { get; set; }

    [JsonIgnore]
    public string SourceLabel => string.IsNullOrWhiteSpace(AgentName) ? AgentId : AgentName;
}

public sealed class ApprovalDecisionRequest
{
    [JsonPropertyName("decision")]
    public string Decision { get; set; } = string.Empty;
}

public sealed class EmptyRequest
{
}

public sealed class RetryJobRequest
{
    [JsonPropertyName("worker_id")]
    public string? WorkerId { get; set; }
}

public sealed class CancellationResponse
{
    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = string.Empty;

    [JsonPropertyName("worker_id")]
    public string? WorkerId { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;
}

public sealed class ApiError
{
    [JsonPropertyName("type")]
    public string Type { get; set; } = string.Empty;

    [JsonPropertyName("message")]
    public string Message { get; set; } = string.Empty;
}

public sealed class FarmSummary
{
    [JsonPropertyName("farm_id")]
    public string FarmId { get; set; } = string.Empty;

    [JsonPropertyName("plan_task_id")]
    public string PlanTaskId { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("worker_count")]
    public int WorkerCount { get; set; }

    [JsonPropertyName("passed_workers")]
    public int PassedWorkers { get; set; }

    public string Title => string.IsNullOrWhiteSpace(PlanTaskId) ? FarmId : PlanTaskId;

    public string Summary => $"{PassedWorkers}/{WorkerCount} workers passed · {Status.Replace('_', ' ')}";
}

public sealed class ChangeSetListResponse
{
    [JsonPropertyName("change_sets")]
    public List<WorkerChangeSet> ChangeSets { get; set; } = [];
}

public sealed class WorkerChangeSet
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("farm_id")]
    public string FarmId { get; set; } = string.Empty;

    [JsonPropertyName("worker_id")]
    public string WorkerId { get; set; } = string.Empty;

    [JsonPropertyName("role")]
    public string Role { get; set; } = string.Empty;

    [JsonPropertyName("provider")]
    public string Provider { get; set; } = string.Empty;

    [JsonPropertyName("model")]
    public string Model { get; set; } = string.Empty;

    [JsonPropertyName("files")]
    public List<ChangeSetFile> Files { get; set; } = [];

    [JsonPropertyName("unified_diff")]
    public string UnifiedDiff { get; set; } = string.Empty;

    [JsonPropertyName("truncated")]
    public bool Truncated { get; set; }

    [JsonPropertyName("binary")]
    public bool Binary { get; set; }

    [JsonPropertyName("tests")]
    public List<JsonObject> Tests { get; set; } = [];

    [JsonPropertyName("machine_review")]
    public MachineReviewPayload MachineReview { get; set; } = new();

    [JsonIgnore]
    public string DisplayName => string.IsNullOrWhiteSpace(Role)
        ? WorkerId
        : $"{Role} ({WorkerId})";

    [JsonIgnore]
    public string Summary =>
        $"{Files.Count} file(s) · {MachineReview.Status} · {Provider} / {Model}";
    [JsonIgnore]
    public string EvidenceSummary
    {
        get
        {
            var changedFiles = Files.Count == 0
                ? "No changed files reported"
                : string.Join(", ", Files.Select(file =>
                    $"{file.Status} {file.Path}{(file.Binary ? " (binary)" : string.Empty)}"));
            var tests = Tests.Count == 0
                ? "No tests reported"
                : string.Join(" | ", Tests.Select(test =>
                {
                    var command = test["command"]?.GetValue<string>() ?? "test";
                    var returnCode = test["returncode"]?.GetValue<int>() ?? -1;
                    var timedOut = test["timed_out"]?.GetValue<bool>() ?? false;
                    return $"{command}: {(timedOut ? "timed out" : returnCode == 0 ? "passed" : $"failed ({returnCode})")}";
                }));
            return $"{Summary}\nFiles: {changedFiles}\nTests: {tests}";
        }
    }
}

public sealed class ChangeSetFile
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("path")]
    public string Path { get; set; } = string.Empty;

    [JsonPropertyName("old_path")]
    public string? OldPath { get; set; }

    [JsonPropertyName("binary")]
    public bool Binary { get; set; }
}

public sealed class MachineReviewPayload
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;
}

public sealed class CheckpointListResponse
{
    [JsonPropertyName("checkpoints")]
    public List<CheckpointSummary> Checkpoints { get; set; } = [];
}

public sealed class CheckpointSummary
{
    [JsonPropertyName("checkpoint_id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("farm_id")]
    public string FarmId { get; set; } = string.Empty;

    [JsonPropertyName("worker_id")]
    public string WorkerId { get; set; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    [JsonPropertyName("created_at")]
    public DateTimeOffset? CreatedAt { get; set; }

    [JsonIgnore]
    public string DisplayName => $"{Status} · {WorkerId} · {CreatedAt?.ToLocalTime():MMM d HH:mm}";
}

public sealed class ApplyCandidateRequest
{
    [JsonPropertyName("worker_id")]
    public string WorkerId { get; set; } = string.Empty;
}

public sealed class RollbackCandidateRequest
{
    [JsonPropertyName("checkpoint_id")]
    public string CheckpointId { get; set; } = string.Empty;

    [JsonPropertyName("force")]
    public bool Force { get; set; }
}

public enum TimelineActivityActor
{
    User,
    Supervisor,
    WorkerFarm,
    Review,
    System,
}

public enum TimelineActivityState
{
    Pending,
    Running,
    Ready,
    Review,
    Completed,
    Failed,
    Cancelled,
    Informational,
}

public sealed class TimelineEntry
{
    public TimelineActivityActor Actor { get; set; } = TimelineActivityActor.System;
    public TimelineActivityState State { get; set; } = TimelineActivityState.Informational;
    public string Title { get; set; } = string.Empty;
    public string Body { get; set; } = string.Empty;
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.Now;

    public string Kind => Actor switch
    {
        TimelineActivityActor.User => "You",
        TimelineActivityActor.Supervisor => "Supervisor",
        TimelineActivityActor.WorkerFarm => "Farm",
        TimelineActivityActor.Review => "Review",
        _ => "System",
    };

    public string Status => State switch
    {
        TimelineActivityState.Pending => "pending",
        TimelineActivityState.Running => "running",
        TimelineActivityState.Ready => "ready",
        TimelineActivityState.Review => "review",
        TimelineActivityState.Completed => "completed",
        TimelineActivityState.Failed => "failed",
        TimelineActivityState.Cancelled => "cancelled",
        _ => "info",
    };

    public string Glyph => Actor switch
    {
        TimelineActivityActor.User => "\uE77B",
        TimelineActivityActor.Supervisor => "\uE945",
        TimelineActivityActor.WorkerFarm => "\uE768",
        TimelineActivityActor.Review => "\uE73E",
        _ => "\uE946",
    };

    public string TimeLabel => CreatedAt.ToLocalTime().ToString("HH:mm");

    public static TimelineActivityState ParseState(string? value) => value?.Trim().ToLowerInvariant() switch
    {
        "queued" or "pending" or "created" => TimelineActivityState.Pending,
        "running" or "planning" or "executing" => TimelineActivityState.Running,
        "ready" or "planned" => TimelineActivityState.Ready,
        "review" or "awaiting_review" => TimelineActivityState.Review,
        "completed" or "succeeded" or "approved" or "merged" => TimelineActivityState.Completed,
        "failed" or "error" or "rejected" => TimelineActivityState.Failed,
        "cancelled" or "canceled" => TimelineActivityState.Cancelled,
        _ => TimelineActivityState.Informational,
    };
}

public partial class LiveAgentOutput : ObservableObject
{
    private const int MaxVisibleCharacters = 24_000;

    public string Id { get; set; } = string.Empty;

    public string Kind { get; set; } = "Worker";

    public string DisplayName { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string Route { get; set; } = "Waiting for route";

    [ObservableProperty]
    public partial string Status { get; set; } = "Queued";

    [ObservableProperty]
    public partial string Activity { get; set; } = "Waiting to start";

    [ObservableProperty]
    public partial string Output { get; set; } = "Waiting for model output…";

    [ObservableProperty]
    public partial bool IsActive { get; set; }

    [ObservableProperty]
    public partial double Progress { get; set; }

    [ObservableProperty]
    public partial string DependencyLabel { get; set; } = "Runs in parallel";

    [ObservableProperty]
    public partial bool CanRetry { get; set; }

    public void AppendOutput(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return;
        }
        var current = Output == "Waiting for model output…" ? string.Empty : Output;
        current += value;
        if (current.Length > MaxVisibleCharacters)
        {
            current = "… earlier output hidden …\n" + current[^MaxVisibleCharacters..];
        }
        Output = current;
    }

    public void AppendMessageIfMissing(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return;
        }
        var normalized = value.Trim();
        if (Output == "Waiting for model output…" || !Output.TrimEnd().EndsWith(normalized, StringComparison.Ordinal))
        {
            AppendOutput((Output == "Waiting for model output…" ? string.Empty : "\n") + normalized);
        }
    }
}

public sealed class SettingsResponse
{
    [JsonPropertyName("config")]
    public JsonObject Config { get; set; } = new();

    [JsonPropertyName("editable_path")]
    public string EditablePath { get; set; } = string.Empty;

    [JsonPropertyName("runtime")]
    public JsonObject Runtime { get; set; } = new();

    [JsonPropertyName("provider_status")]
    public JsonObject ProviderStatus { get; set; } = new();

    [JsonPropertyName("provider_templates")]
    public List<ProviderTemplate> ProviderTemplates { get; set; } = [];

    [JsonPropertyName("options")]
    public SettingsOptions Options { get; set; } = new();
}

public sealed class NotificationItem
{
    public string Title { get; set; } = string.Empty;
    public string Message { get; set; } = string.Empty;
    public string Severity { get; set; } = "Informational";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.Now;
    public string TimeLabel => CreatedAt.ToString("t");
    public string Glyph => Severity switch
    {
        "Error" => "\uEA39",
        "Warning" => "\uE7BA",
        "Success" => "\uE73E",
        _ => "\uE946",
    };
}

public sealed class SettingsOptions
{
    [JsonPropertyName("wire_apis")]
    public List<string> WireApis { get; set; } = ["responses", "chat"];
}

public sealed class ProviderTemplate
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    [JsonPropertyName("category")]
    public string Category { get; set; } = string.Empty;

    [JsonPropertyName("description")]
    public string Description { get; set; } = string.Empty;

    [JsonPropertyName("base_url")]
    public string BaseUrl { get; set; } = string.Empty;

    [JsonPropertyName("env_key")]
    public string EnvKey { get; set; } = string.Empty;

    [JsonPropertyName("wire_api")]
    public string WireApi { get; set; } = "chat";

    [JsonPropertyName("default_model")]
    public string? DefaultModel { get; set; }

    [JsonPropertyName("custom")]
    public bool Custom { get; set; }

    [JsonPropertyName("models")]
    public List<ModelOption> Models { get; set; } = [];

    [JsonPropertyName("reasoning")]
    public ReasoningCapability Reasoning { get; set; } = new();

    public string DisplayLabel => string.IsNullOrWhiteSpace(Category) ? Name : $"{Name} · {Category}";
}

public sealed class ReasoningCapability
{
    [JsonPropertyName("efforts")]
    public List<string> Efforts { get; set; } = [];

    [JsonPropertyName("thinking")]
    public List<string> Thinking { get; set; } = [];
}

public sealed class ProviderModelCatalog
{
    [JsonPropertyName("provider_id")]
    public string ProviderId { get; set; } = string.Empty;

    [JsonPropertyName("models")]
    public List<ModelOption> Models { get; set; } = [];
}

public sealed class ModelOption
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    [JsonPropertyName("description")]
    public string Description { get; set; } = string.Empty;

    [JsonPropertyName("reasoning")]
    public ReasoningCapability Reasoning { get; set; } = new();

    public string DisplayName => string.IsNullOrWhiteSpace(Name) || Name == Id ? Id : $"{Name} ({Id})";
}

public sealed class ProviderOption
{
    public string Id { get; init; } = string.Empty;
    public string Name { get; init; } = string.Empty;
}

public partial class WorkerProfileEditor : ObservableObject
{
    public JsonObject Raw { get; set; } = new();

    [ObservableProperty]
    public partial string Name { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string DisplayName { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string Provider { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string Model { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string ReasoningMode { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string ReasoningEffort { get; set; } = string.Empty;

    [ObservableProperty]
    public partial int TimeoutSeconds { get; set; } = 1800;

    [ObservableProperty]
    public partial double BudgetUsd { get; set; }

    [ObservableProperty]
    public partial string CapabilityTier { get; set; } = "standard";

    public string RouteSummary => $"{Provider} · {Model}";

    partial void OnDisplayNameChanged(string value) => OnPropertyChanged(nameof(DisplayName));
    partial void OnProviderChanged(string value) => OnPropertyChanged(nameof(RouteSummary));
    partial void OnModelChanged(string value) => OnPropertyChanged(nameof(RouteSummary));
}

public partial class ProviderEditor : ObservableObject
{
    public JsonObject Raw { get; set; } = new();

    [ObservableProperty]
    public partial string Id { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string Name { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string BaseUrl { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string EnvKey { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string WireApi { get; set; } = "chat";

    [ObservableProperty]
    public partial string ApiKey { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string Status { get; set; } = "Configured";

    public string DisplayName => string.IsNullOrWhiteSpace(Name) ? Id : Name;

    partial void OnNameChanged(string value) => OnPropertyChanged(nameof(DisplayName));
}

public sealed class SettingsSaveRequest
{
    [JsonPropertyName("config")]
    public JsonObject Config { get; set; } = new();

    [JsonPropertyName("provider_secrets")]
    public Dictionary<string, string> ProviderSecrets { get; set; } = [];
}
