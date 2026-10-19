using System.Net.Http.Headers;
using System.Text.Json;

namespace Sentinel.Monitor.Instances;

// The configured control plane is the only destination; a cursor is data, never a URL
internal static class SchedulerInventoryClient
{
    internal const int PageSize = 200;
    internal const int ScanTimeoutSeconds = 60;
    private const int MaxPageBytes = 2 * 1024 * 1024;

    internal static bool TryEndpoint(string? url, out Uri endpoint)
    {
        endpoint = null!;
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https") || uri.UserInfo.Length != 0
            || uri.Query.Length != 0 || uri.Fragment.Length != 0
            || uri.AbsolutePath is not ("/api/instances" or "/api/instances/recent")) return false;
        // Migrate existing deployment profiles without reverting to the recent-20 window
        endpoint = new UriBuilder(uri) { Path = "/api/instances" }.Uri;
        return true;
    }

    internal sealed record Result(IReadOnlyList<SchedulerRow> Rows, int PageCount, string Reason, int? HttpStatus)
    {
        internal bool Success => Reason == "OK";
    }

    internal static async Task<Result> ReadAsync(HttpClient http, string url, string tokenEnv,
        int timeoutSeconds, CancellationToken token)
    {
        if (!TryEndpoint(url, out var endpoint)) return new([], 0, "INVALID_CONFIGURATION", null);
        var credential = Environment.GetEnvironmentVariable(tokenEnv);
        if (string.IsNullOrWhiteSpace(credential)) return new([], 0, "AUTH_TOKEN_NOT_CONFIGURED", null);
        using var scan = CancellationTokenSource.CreateLinkedTokenSource(token);
        scan.CancelAfter(TimeSpan.FromSeconds(ScanTimeoutSeconds));
        var rows = new List<SchedulerRow>();
        var ids = new HashSet<Guid>();
        var cursors = new HashSet<string>(StringComparer.Ordinal);
        string? cursor = null;
        var pages = 0;
        int? code = null;
        try
        {
            do
            {
                scan.Token.ThrowIfCancellationRequested();
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(scan.Token);
                deadline.CancelAfter(TimeSpan.FromSeconds(timeoutSeconds));
                var uri = new UriBuilder(endpoint) { Query = "limit=" + PageSize
                    + (cursor is null ? "" : "&cursor=" + Uri.EscapeDataString(cursor)) }.Uri;
                using var request = new HttpRequestMessage(HttpMethod.Get, uri);
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", credential);
                request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
                request.Headers.UserAgent.ParseAdd("MSG-Sentinel/0.1");
                code = null;
                using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token);
                code = (int)response.StatusCode;
                if (code != 200) return Fail(code switch
                {
                    401 or 403 => "AUTH_REQUIRED", 404 => "RUNTIME_OR_ENDPOINT_NOT_FOUND",
                    >= 300 and < 400 => "REDIRECT_BLOCKED", _ => "UPSTREAM_UNAVAILABLE",
                });
                if (!string.Equals(response.Content.Headers.ContentType?.MediaType, "application/json", StringComparison.OrdinalIgnoreCase))
                    return Fail("EXPECTED_JSON");
                if (response.Content.Headers.ContentLength > MaxPageBytes) return Fail("RESPONSE_TOO_LARGE");
                await using var input = await response.Content.ReadAsStreamAsync(deadline.Token);
                using var body = new MemoryStream();
                var buffer = new byte[8192];
                int count;
                while ((count = await input.ReadAsync(buffer, deadline.Token)) > 0)
                {
                    if (body.Length + count > MaxPageBytes) return Fail("RESPONSE_TOO_LARGE");
                    body.Write(buffer, 0, count);
                }
                body.Position = 0;
                using var document = await JsonDocument.ParseAsync(body, cancellationToken: deadline.Token);
                var root = document.RootElement;
                if (root.ValueKind != JsonValueKind.Object || Text(root, "code") != "SUCCESS"
                    || !root.TryGetProperty("data", out var data) || data.ValueKind != JsonValueKind.Object
                    || !data.TryGetProperty("items", out var items) || items.ValueKind != JsonValueKind.Array
                    || items.GetArrayLength() > PageSize || !data.TryGetProperty("next_cursor", out var next)
                    || next.ValueKind is not (JsonValueKind.Null or JsonValueKind.String))
                    return Fail("INVALID_SCHEDULER_INVENTORY");
                cursor = next.ValueKind == JsonValueKind.Null ? null : next.GetString();
                if (cursor is not null && (string.IsNullOrWhiteSpace(cursor) || cursor.Length > 1024
                    || items.GetArrayLength() == 0 || !cursors.Add(cursor)))
                    return Fail("INVALID_SCHEDULER_INVENTORY");
                foreach (var item in items.EnumerateArray())
                {
                    if (!TryRow(item, out var row) || !ids.Add(row!.InstanceId))
                        return Fail("INVALID_SCHEDULER_INVENTORY");
                    rows.Add(row);
                }
                pages++;
            } while (cursor is not null);
            token.ThrowIfCancellationRequested();
            return new(rows, pages, "OK", code);
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested) { return Fail("UPSTREAM_TIMEOUT"); }
        catch (HttpRequestException) { return Fail("UPSTREAM_UNAVAILABLE"); }
        catch (IOException) { return Fail("UPSTREAM_UNAVAILABLE"); }
        catch (JsonException) { return Fail("INVALID_JSON"); }

        // Never expose a successfully read prefix as a complete inventory
        Result Fail(string reason) => new([], pages, reason, code);
    }

    internal sealed record SchedulerRow(Guid InstanceId, string Status, string? RuntimeType, string? TargetId,
        string? WorkloadId, DateTimeOffset ExpiresAt, Guid? ChallengeId, Guid? ReleaseId = null, Guid? TeamId = null);
    private static readonly HashSet<string> States = ["REQUESTED", "SCHEDULING", "PROVISIONING", "RUNNING",
        "RESTARTING", "RESETTING", "STOPPING", "STOPPED", "FAILED", "EXPIRED", "CLEANUP_PENDING", "CLEANED"];
    private static bool TryRow(JsonElement row, out SchedulerRow? parsed)
    {
        parsed = null;
        if (row.ValueKind != JsonValueKind.Object || !Guid.TryParse(Text(row, "instance_id"), out var id)
            || id == Guid.Empty || !States.Contains(Text(row, "status") ?? "")
            || Timestamp(row, "expires_at") is not { } expiry || Timestamp(row, "hard_expires_at") is not { } hard)
            return false;
        foreach (var field in new[] { "runtime_type", "runtime_target_id", "runtime_workload_id" })
            if (row.TryGetProperty(field, out var value) && value.ValueKind is not (JsonValueKind.Null or JsonValueKind.String)) return false;
        var type = Text(row, "runtime_type");
        if (type is not (null or "KUBERNETES" or "VM")) return false;
        var idle = Timestamp(row, "idle_expires_at");
        if (row.TryGetProperty("idle_expires_at", out var idleValue)
            && idleValue.ValueKind != JsonValueKind.Null && idle is null) return false;
        expiry = expiry < hard ? expiry : hard;
        if (idle is { } idleAt && idleAt < expiry) expiry = idleAt;
        Guid? challengeId = null;
        if (row.TryGetProperty("challenge_id", out var challenge) && challenge.ValueKind != JsonValueKind.Null)
        {
            if (challenge.ValueKind != JsonValueKind.String || !Guid.TryParse(challenge.GetString(), out var parsedChallenge)
                || parsedChallenge == Guid.Empty) return false;
            challengeId = parsedChallenge;
        }
        Guid? OptionalId(string field) => Guid.TryParse(Text(row, field), out var value) && value != Guid.Empty ? value : null;
        parsed = new(id, Text(row, "status")!, type, Text(row, "runtime_target_id"), Text(row, "runtime_workload_id"), expiry,
            challengeId, OptionalId("release_id"), OptionalId("team_id"));
        return true;
    }
    private static string? Text(JsonElement root, string field) => root.TryGetProperty(field, out var value)
        && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
    private static DateTimeOffset? Timestamp(JsonElement root, string field) => root.TryGetProperty(field, out var value)
        && value.ValueKind == JsonValueKind.String && value.TryGetDateTimeOffset(out var at) ? at : null;
}
