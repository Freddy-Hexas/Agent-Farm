using AgentFarm_Desktop.Models;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AgentFarm_Desktop.Services;

internal sealed class AgentFarmApiClient : IDisposable
{
    private readonly HttpClient _httpClient;
    private readonly JsonSerializerOptions _jsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true,
    };

    public AgentFarmApiClient(Uri runtimeUri)
    {
        var authority = runtimeUri.GetLeftPart(UriPartial.Authority);
        _httpClient = new HttpClient
        {
            BaseAddress = new Uri(authority + "/", UriKind.Absolute),
            // Planning and Worker inference can legitimately take a long time.
            // Individual operations remain cancellable through the app lifetime token.
            Timeout = System.Threading.Timeout.InfiniteTimeSpan,
        };
    }

    public Task<BootstrapResponse> GetBootstrapAsync(CancellationToken cancellationToken) =>
        GetAsync<BootstrapResponse>("api/bootstrap", cancellationToken);

    public Task<RuntimeHealth> GetHealthAsync(CancellationToken cancellationToken) =>
        GetAsync<RuntimeHealth>("api/health", cancellationToken);

    public Task<ProtocolInitializeResponse> InitializeProtocolAsync(
        ProtocolInitializeRequest request,
        CancellationToken cancellationToken) =>
        PostAsync<ProtocolInitializeRequest, ProtocolInitializeResponse>(
            "api/protocol/initialize",
            request,
            cancellationToken);

    public Task<ThreadDocument> GetThreadAsync(string threadId, CancellationToken cancellationToken) =>
        GetAsync<ThreadDocument>($"api/threads/{Uri.EscapeDataString(threadId)}", cancellationToken);

    public Task<ThreadListResponse> GetThreadsAsync(CancellationToken cancellationToken) =>
        GetAsync<ThreadListResponse>("api/threads", cancellationToken);

    public Task<ThreadDocument> CreateThreadAsync(CreateThreadRequest request, CancellationToken cancellationToken) =>
        PostAsync<CreateThreadRequest, ThreadDocument>("api/threads", request, cancellationToken);

    public Task<ThreadDocument> RenameThreadAsync(string threadId, string title, CancellationToken cancellationToken) =>
        PostAsync<JsonObject, ThreadDocument>(
            $"api/threads/{Uri.EscapeDataString(threadId)}/rename",
            new JsonObject { ["title"] = title },
            cancellationToken);

    public Task<ThreadDocument> ArchiveThreadAsync(string threadId, bool archived, CancellationToken cancellationToken) =>
        PostAsync<JsonObject, ThreadDocument>(
            $"api/threads/{Uri.EscapeDataString(threadId)}/{(archived ? "archive" : "resume")}",
            new JsonObject(),
            cancellationToken);

    public Task<ThreadDocument> ForkThreadAsync(string threadId, string? turnId, CancellationToken cancellationToken) =>
        PostAsync<JsonObject, ThreadDocument>(
            $"api/threads/{Uri.EscapeDataString(threadId)}/fork",
            new JsonObject { ["turn_id"] = turnId },
            cancellationToken);

    public Task<JsonObject> DeleteThreadAsync(string threadId, CancellationToken cancellationToken) =>
        PostAsync<JsonObject, JsonObject>(
            $"api/threads/{Uri.EscapeDataString(threadId)}/delete",
            new JsonObject(),
            cancellationToken);

    public Task<PlanJobResponse> CreatePlanAsync(PlanRequest request, CancellationToken cancellationToken) =>
        PostAsync<PlanRequest, PlanJobResponse>("api/plans", request, cancellationToken);

    public Task<AttachmentItem> AddAttachmentAsync(
        AddAttachmentRequest request,
        CancellationToken cancellationToken) =>
        PostAsync<AddAttachmentRequest, AttachmentItem>("api/attachments", request, cancellationToken);

    public async Task RemoveAttachmentAsync(string attachmentId, CancellationToken cancellationToken)
    {
        using var request = CreateRequest(
            HttpMethod.Delete,
            $"api/attachments/{Uri.EscapeDataString(attachmentId)}");
        using var response = await _httpClient.SendAsync(request, cancellationToken);
        await ReadResponseAsync<JsonObject>(response, cancellationToken);
    }

    public Task<PlanJobResponse> GetPlanJobAsync(string jobId, CancellationToken cancellationToken) =>
        GetAsync<PlanJobResponse>($"api/plan-jobs/{Uri.EscapeDataString(jobId)}", cancellationToken);

    public Task<JobEventBatch> GetPlanJobEventsAsync(
        string jobId,
        long after,
        CancellationToken cancellationToken) =>
        GetAsync<JobEventBatch>(
            $"api/plan-jobs/{Uri.EscapeDataString(jobId)}/events?after={after}",
            cancellationToken);

    public IAsyncEnumerable<JobStreamEnvelope> StreamPlanJobEventsAsync(
        string jobId,
        long after,
        CancellationToken cancellationToken) =>
        StreamJobEventsAsync("plan-jobs", jobId, after, cancellationToken);

    public Task<FarmJobResponse> StartFarmAsync(FarmSubmission request, CancellationToken cancellationToken) =>
        PostAsync<FarmSubmission, FarmJobResponse>("api/farms", request, cancellationToken);

    public Task<FarmJobResponse> GetFarmJobAsync(string jobId, CancellationToken cancellationToken) =>
        GetAsync<FarmJobResponse>($"api/jobs/{Uri.EscapeDataString(jobId)}", cancellationToken);

    public Task<JobEventBatch> GetFarmJobEventsAsync(
        string jobId,
        long after,
        CancellationToken cancellationToken) =>
        GetAsync<JobEventBatch>(
            $"api/jobs/{Uri.EscapeDataString(jobId)}/events?after={after}",
            cancellationToken);

    public IAsyncEnumerable<JobStreamEnvelope> StreamFarmJobEventsAsync(
        string jobId,
        long after,
        CancellationToken cancellationToken) =>
        StreamJobEventsAsync("jobs", jobId, after, cancellationToken);

    public Task<ApprovalListResponse> GetPendingApprovalsAsync(CancellationToken cancellationToken) =>
        GetAsync<ApprovalListResponse>("api/approvals?status=pending", cancellationToken);

    public Task<ApprovalRequest> ResolveApprovalAsync(
        string approvalId,
        string decision,
        CancellationToken cancellationToken) =>
        PostAsync<ApprovalDecisionRequest, ApprovalRequest>(
            $"api/approvals/{Uri.EscapeDataString(approvalId)}/decision",
            new ApprovalDecisionRequest { Decision = decision },
            cancellationToken);

    public Task<CancellationResponse> CancelPlanAsync(
        string jobId,
        CancellationToken cancellationToken) =>
        PostAsync<EmptyRequest, CancellationResponse>(
            $"api/plan-jobs/{Uri.EscapeDataString(jobId)}/cancel",
            new EmptyRequest(),
            cancellationToken);

    public Task<CancellationResponse> CancelFarmAsync(
        string jobId,
        CancellationToken cancellationToken) =>
        PostAsync<EmptyRequest, CancellationResponse>(
            $"api/jobs/{Uri.EscapeDataString(jobId)}/cancel",
            new EmptyRequest(),
            cancellationToken);

    public Task<CancellationResponse> CancelWorkerAsync(
        string jobId,
        string workerId,
        CancellationToken cancellationToken) =>
        PostAsync<EmptyRequest, CancellationResponse>(
            $"api/jobs/{Uri.EscapeDataString(jobId)}/workers/{Uri.EscapeDataString(workerId)}/cancel",
            new EmptyRequest(),
            cancellationToken);

    public Task<FarmJobResponse> RetryWorkerAsync(
        string jobId,
        string workerId,
        CancellationToken cancellationToken) =>
        PostAsync<RetryJobRequest, FarmJobResponse>(
            $"api/jobs/{Uri.EscapeDataString(jobId)}/retry",
            new RetryJobRequest { WorkerId = workerId },
            cancellationToken);

    public Task<JsonObject> GetFarmAsync(string farmId, CancellationToken cancellationToken) =>
        GetAsync<JsonObject>($"api/farms/{Uri.EscapeDataString(farmId)}", cancellationToken);

    public Task<ChangeSetListResponse> GetFarmChangeSetsAsync(
        string farmId,
        CancellationToken cancellationToken) =>
        GetAsync<ChangeSetListResponse>(
            $"api/farms/{Uri.EscapeDataString(farmId)}/changesets",
            cancellationToken);

    public Task<CheckpointListResponse> GetFarmCheckpointsAsync(
        string farmId,
        CancellationToken cancellationToken) =>
        GetAsync<CheckpointListResponse>(
            $"api/farms/{Uri.EscapeDataString(farmId)}/checkpoints",
            cancellationToken);

    public Task<JsonObject> ApplyCandidateAsync(
        string farmId,
        string workerId,
        CancellationToken cancellationToken) =>
        PostAsync<ApplyCandidateRequest, JsonObject>(
            $"api/farms/{Uri.EscapeDataString(farmId)}/apply",
            new ApplyCandidateRequest { WorkerId = workerId },
            cancellationToken);

    public Task<JsonObject> MergeCandidateAsync(
        string farmId,
        CancellationToken cancellationToken) =>
        PostAsync<EmptyRequest, JsonObject>(
            $"api/farms/{Uri.EscapeDataString(farmId)}/merge",
            new EmptyRequest(),
            cancellationToken);

    public Task<JsonObject> RollbackCandidateAsync(
        string farmId,
        string checkpointId,
        bool force,
        CancellationToken cancellationToken) =>
        PostAsync<RollbackCandidateRequest, JsonObject>(
            $"api/farms/{Uri.EscapeDataString(farmId)}/rollback",
            new RollbackCandidateRequest { CheckpointId = checkpointId, Force = force },
            cancellationToken);

    public Task<SettingsResponse> GetSettingsAsync(CancellationToken cancellationToken) =>
        GetAsync<SettingsResponse>("api/settings", cancellationToken);

    public Task<SettingsResponse> SaveSettingsAsync(SettingsSaveRequest request, CancellationToken cancellationToken) =>
        PostAsync<SettingsSaveRequest, SettingsResponse>("api/settings", request, cancellationToken);

    public Task<DiagnosticBundleResponse> ExportDiagnosticsAsync(CancellationToken cancellationToken) =>
        PostAsync<EmptyRequest, DiagnosticBundleResponse>(
            "api/diagnostics/export",
            new EmptyRequest(),
            cancellationToken);

    public Task<ProviderModelCatalog> GetProviderModelsAsync(
        string providerId,
        bool refresh,
        CancellationToken cancellationToken)
    {
        var suffix = refresh ? "?refresh=1" : string.Empty;
        return GetAsync<ProviderModelCatalog>(
            $"api/providers/{Uri.EscapeDataString(providerId)}/models{suffix}",
            cancellationToken);
    }

    public void Dispose() => _httpClient.Dispose();

    private async Task<T> GetAsync<T>(string path, CancellationToken cancellationToken)
    {
        using var request = CreateRequest(HttpMethod.Get, path);
        using var response = await _httpClient.SendAsync(request, cancellationToken);
        return await ReadResponseAsync<T>(response, cancellationToken);
    }

    private async IAsyncEnumerable<JobStreamEnvelope> StreamJobEventsAsync(
        string resource,
        string jobId,
        long after,
        [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        var path = $"api/{resource}/{Uri.EscapeDataString(jobId)}/stream?after={after}";
        using var request = CreateRequest(HttpMethod.Get, path);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
        using var response = await _httpClient.SendAsync(
            request,
            HttpCompletionOption.ResponseHeadersRead,
            cancellationToken);
        if (!response.IsSuccessStatusCode)
        {
            await ReadResponseAsync<JsonObject>(response, cancellationToken);
            yield break;
        }

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var reader = new StreamReader(stream);
        var data = new StringBuilder();
        while (true)
        {
            var line = await reader.ReadLineAsync(cancellationToken);
            if (line is null)
            {
                yield break;
            }
            if (line.Length == 0)
            {
                if (data.Length == 0)
                {
                    continue;
                }
                var envelope = JsonSerializer.Deserialize<JobStreamEnvelope>(data.ToString(), _jsonOptions)
                    ?? throw new AgentFarmApiException(
                        HttpStatusCode.InternalServerError,
                        "The local runtime returned an empty event envelope.");
                data.Clear();
                if (envelope.ProtocolVersion != 1 || envelope.JobId != jobId)
                {
                    throw new AgentFarmApiException(
                        HttpStatusCode.Conflict,
                        "The local runtime returned an incompatible event stream.");
                }
                yield return envelope;
                continue;
            }
            if (line.StartsWith("data:", StringComparison.Ordinal))
            {
                if (data.Length > 0)
                {
                    data.Append('\n');
                }
                data.Append(line.AsSpan(5).TrimStart());
            }
        }
    }

    private async Task<TResponse> PostAsync<TRequest, TResponse>(
        string path,
        TRequest request,
        CancellationToken cancellationToken)
    {
        var payload = JsonSerializer.SerializeToUtf8Bytes(request, _jsonOptions);
        using var content = new ByteArrayContent(payload);
        content.Headers.ContentType = new MediaTypeHeaderValue("application/json")
        {
            CharSet = "utf-8",
        };
        content.Headers.ContentLength = payload.LongLength;
        using var message = CreateRequest(HttpMethod.Post, path);
        message.Content = content;
        using var response = await _httpClient.SendAsync(message, cancellationToken);
        return await ReadResponseAsync<TResponse>(response, cancellationToken);
    }

    private static HttpRequestMessage CreateRequest(HttpMethod method, string path)
    {
        var request = new HttpRequestMessage(method, path);
        request.Headers.TryAddWithoutValidation("X-Correlation-ID", Guid.NewGuid().ToString("N"));
        return request;
    }

    private async Task<T> ReadResponseAsync<T>(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        if (!response.IsSuccessStatusCode)
        {
            var message = response.ReasonPhrase ?? "Agent Farm request failed.";
            try
            {
                var payload = await response.Content.ReadFromJsonAsync<JsonObject>(_jsonOptions, cancellationToken);
                message = payload?["error"]?["message"]?.GetValue<string>() ?? message;
            }
            catch (JsonException)
            {
                // Keep the HTTP status text when the backend did not return its normal error envelope.
            }

            throw new AgentFarmApiException(response.StatusCode, message);
        }

        var result = await response.Content.ReadFromJsonAsync<T>(_jsonOptions, cancellationToken);
        return result ?? throw new AgentFarmApiException(
            HttpStatusCode.InternalServerError,
            "The local Agent Farm runtime returned an empty response.");
    }
}

internal sealed class AgentFarmApiException(HttpStatusCode statusCode, string message) : Exception(message)
{
    public HttpStatusCode StatusCode { get; } = statusCode;
}
