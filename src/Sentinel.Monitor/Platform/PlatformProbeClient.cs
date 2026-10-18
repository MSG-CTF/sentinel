using System.Diagnostics;
using System.Net.Http.Headers;
using System.Text.Json;
using Sentinel.Monitor.Instances;

namespace Sentinel.Monitor.Platform;

public sealed class PlatformProbeClient(HttpClient httpClient, HeartbeatStore? heartbeatStore = null, ClusterHeartbeatStore? clusterHeartbeatStore = null)
{
    private const int MaxBodyBytes = 65536;

    public async Task<ProbeObservation> CheckAsync(
        PlatformTarget target, int timeoutSeconds, CancellationToken cancellationToken = default)
    {
        if (!target.Enabled)
            return Result(target, "UNCONFIGURED", target.UnconfiguredReason, null, null, null);
        if (!target.IsValid())
            return Result(target, "UNVERIFIED", "INVALID_CONFIGURATION", null, null, null);
        if (target.Kind == "service_heartbeat")
            return heartbeatStore?.Read(target) ?? Result(target, "UNCONFIGURED", "HEARTBEAT_PENDING", null, null, null);
        if (target.Kind == "cluster_heartbeat")
            return clusterHeartbeatStore?.Read(target) ?? Result(target, "UNCONFIGURED", "HEARTBEAT_PENDING", null, null, null);

        if (target.Kind == "scheduler_inventory")
        {
            var started = Stopwatch.StartNew();
            var inventory = await SchedulerInventoryClient.ReadAsync(httpClient, target.Url!, target.AuthTokenEnv!, timeoutSeconds, cancellationToken);
            var status = "HEALTHY";
            var reason = "SCHEDULER_QUERY_READY";
            if (!inventory.Success)
            {
                (status, reason) = inventory.Reason switch
                {
                    "AUTH_TOKEN_NOT_CONFIGURED" => ("UNCONFIGURED", "AUTH_TOKEN_NOT_CONFIGURED"),
                    "UPSTREAM_TIMEOUT" => ("DOWN", "TIMEOUT"),
                    "EXPECTED_JSON" => ("UNVERIFIED", "EXPECTED_JSON_RECEIVED_OTHER"),
                    "INVALID_SCHEDULER_INVENTORY" => ("UNVERIFIED", "INVALID_INVENTORY_CONTRACT"),
                    "RUNTIME_OR_ENDPOINT_NOT_FOUND" => ("UNVERIFIED", "ENDPOINT_NOT_FOUND"),
                    "UPSTREAM_UNAVAILABLE" when inventory.HttpStatus == 429 => ("DEGRADED", "HTTP_RATE_LIMITED"),
                    "UPSTREAM_UNAVAILABLE" when inventory.HttpStatus is null => ("DOWN", "CONNECTION_FAILED"),
                    "UPSTREAM_UNAVAILABLE" => (inventory.HttpStatus >= 500 ? "DOWN" : "UNVERIFIED", "HTTP_ERROR"),
                    _ => ("UNVERIFIED", inventory.Reason),
                };
            }
            return Result(target, status, reason, DateTimeOffset.UtcNow, inventory.HttpStatus,
                Math.Round(started.Elapsed.TotalMilliseconds, 1), inventory.Success ? new Dictionary<string, string>
                {
                    ["inventory_total"] = inventory.Rows.Count.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    ["page_count"] = inventory.PageCount.ToString(System.Globalization.CultureInfo.InvariantCulture),
                } : null);
        }

        using var request = new HttpRequestMessage(HttpMethod.Get, target.Url);
        request.Headers.UserAgent.ParseAdd("MSG-Sentinel/0.1");
        if (target.Kind != "http")
            request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (!string.IsNullOrEmpty(target.AuthTokenEnv))
        {
            var token = Environment.GetEnvironmentVariable(target.AuthTokenEnv);
            if (string.IsNullOrWhiteSpace(token))
                return Result(target, "UNCONFIGURED", "AUTH_TOKEN_NOT_CONFIGURED", null, null, null);
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }

        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadline.CancelAfter(TimeSpan.FromSeconds(timeoutSeconds));
        var clock = Stopwatch.StartNew();
        int? observedCode = null;
        try
        {
            using var response = await httpClient.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, deadline.Token);
            var code = (int)response.StatusCode;
            observedCode = code;
            if (code == 429)
                return Measured("DEGRADED", "HTTP_RATE_LIMITED", code);
            if (code is >= 300 and < 400)
                return Measured("UNVERIFIED", "REDIRECT_BLOCKED", code);
            if (code is 401 or 403 && target.Kind != "auth_boundary")
                return Measured("UNVERIFIED", "AUTH_REQUIRED", code);
            if (code == 404)
                return Measured("UNVERIFIED", "ENDPOINT_NOT_FOUND", code);
            if (target.Kind is not ("backend_healthz" or "auth_boundary") && code != 200)
                return Measured(code >= 500 ? "DOWN" : "UNVERIFIED", "HTTP_ERROR", code);
            if (target.Kind == "auth_boundary" && code != 401)
                return Measured(code >= 500 ? "DOWN" : "UNVERIFIED", "HTTP_ERROR", code);
            if (target.Kind == "backend_healthz" && code is not (200 or 503))
                return Measured(code >= 500 ? "DOWN" : "UNVERIFIED", "HTTP_ERROR", code);

            var mediaType = response.Content.Headers.ContentType?.MediaType;
            if (target.Kind != "http" && !string.Equals(mediaType, "application/json", StringComparison.OrdinalIgnoreCase))
                return Measured("UNVERIFIED", "EXPECTED_JSON_RECEIVED_OTHER", code);
            if (response.Content.Headers.ContentLength > MaxBodyBytes)
                return Measured("UNVERIFIED", "RESPONSE_TOO_LARGE", code);
            await using var stream = await response.Content.ReadAsStreamAsync(deadline.Token);
            using var body = new MemoryStream();
            var buffer = new byte[4096];
            int read;
            while ((read = await stream.ReadAsync(buffer, deadline.Token)) > 0)
            {
                if (body.Length + read > MaxBodyBytes)
                    return Measured("UNVERIFIED", "RESPONSE_TOO_LARGE", code);
                await body.WriteAsync(buffer.AsMemory(0, read), deadline.Token);
            }
            body.Position = 0;
            if (target.Kind == "http")
            {
                if (target.ExpectedText is not null)
                {
                    using var reader = new StreamReader(body);
                    var text = await reader.ReadToEndAsync(deadline.Token);
                    if (!text.Contains(target.ExpectedText, StringComparison.Ordinal))
                        return Measured("UNVERIFIED", "PAGE_IDENTITY_MISMATCH", code);
                }
                return Measured("HEALTHY", "HTTP_REACHABLE", code);
            }

            using var payload = await JsonDocument.ParseAsync(body, cancellationToken: deadline.Token);
            var root = payload.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
                return Measured("UNVERIFIED", "INVALID_HEALTH_CONTRACT", code);
            if (target.Kind == "auth_boundary")
            {
                if (root.TryGetProperty("code", out var error) && error.ValueKind == JsonValueKind.String
                    && error.GetString() == "TOKEN_MISSING" && root.TryGetProperty("data", out var data)
                    && data.ValueKind == JsonValueKind.Null)
                    return Measured("HEALTHY", "AUTH_BOUNDARY_READY", code);
                return Measured("UNVERIFIED", "INVALID_HEALTH_CONTRACT", code);
            }
            if (target.Kind is "runtime_status" or "runtime_node" or "runtime_operation")
            {
                var result = RuntimeProbeContract.Check(root, target, DateTimeOffset.UtcNow);
                return Measured(result.Status, result.Reason, code, result.Components);
            }
            if (target.Kind == "broker_inventory")
            {
                if (!root.TryGetProperty("items", out var items) || items.ValueKind != JsonValueKind.Array
                    || !NonNegativeInt(root, "total", out var total) || !NonNegativeInt(root, "offset", out var offset)
                    || offset != 0 || !NonNegativeInt(root, "limit", out var limit) || limit is < 1 or > 500
                    || items.GetArrayLength() != Math.Min(total, limit)
                    || items.EnumerateArray().Any(item => item.ValueKind != JsonValueKind.Object)
                    || !root.TryGetProperty("generated_at", out var generatedAt) || generatedAt.ValueKind != JsonValueKind.String
                    || !generatedAt.TryGetDateTimeOffset(out var generatedTime)
                    || Math.Abs((DateTimeOffset.UtcNow - generatedTime).TotalSeconds) > 120)
                    return Measured("UNVERIFIED", "INVALID_INVENTORY_CONTRACT", code);
                return Measured("HEALTHY", "BROKER_QUERY_READY", code, new Dictionary<string, string>
                {
                    ["inventory_total"] = total.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    ["page_count"] = items.GetArrayLength().ToString(System.Globalization.CultureInfo.InvariantCulture),
                });
            }
            if (target.Kind == "json_fields")
            {
                foreach (var field in target.ExpectedFields)
                    if (!root.TryGetProperty(field.Key, out var value) || value.ValueKind != JsonValueKind.String
                        || value.GetString() != field.Value)
                        return Measured("UNVERIFIED", "HEALTH_FIELD_MISMATCH", code);
                return Measured("HEALTHY", "HEALTH_CONTRACT_MATCHED", code);
            }

            var components = new Dictionary<string, string>();
            foreach (var field in new[] { "status", "database", "cache" })
            {
                if (!root.TryGetProperty(field, out var value) || value.ValueKind != JsonValueKind.String
                    || value.GetString() is not ("ok" or "down"))
                    return Measured("UNVERIFIED", "INVALID_HEALTH_CONTRACT", code);
                components[field] = value.GetString()!;
            }
            var healthy = components["database"] == "ok" && components["cache"] == "ok";
            if (components["status"] != (healthy ? "ok" : "down") || code != (healthy ? 200 : 503))
                return Measured("UNVERIFIED", "INCONSISTENT_HEALTH_CONTRACT", code);
            var reason = healthy ? "BACKEND_READY"
                : components["database"] == "down" && components["cache"] == "down" ? "DATABASE_AND_CACHE_DOWN"
                : components["database"] == "down" ? "DATABASE_DOWN" : "CACHE_DOWN";
            return Measured(healthy ? "HEALTHY" : "DOWN", reason, code, components);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return Measured("DOWN", "TIMEOUT", null);
        }
        catch (OperationCanceledException) { throw; }
        catch (HttpRequestException) { return Measured("DOWN", "CONNECTION_FAILED", null); }
        catch (JsonException) { return Measured("UNVERIFIED", "INVALID_JSON", observedCode); }
        catch (IOException) { return Measured("DOWN", "RESPONSE_READ_FAILED", null); }

        ProbeObservation Measured(string status, string reason, int? code,
            IReadOnlyDictionary<string, string>? components = null)
        {
            // A 503 remains unavailable even when a proxy supplies HTML or broken JSON.
            // Without a valid health contract, no database/cache state can be inferred.
            if (target.Kind == "backend_healthz" && code >= 500 && status == "UNVERIFIED")
            { status = "DOWN"; reason = "HTTP_UNAVAILABLE"; components = null; }
            return Result(target, status, reason, DateTimeOffset.UtcNow, code,
                Math.Round(clock.Elapsed.TotalMilliseconds, 1), components);
        }
    }

    private static bool NonNegativeInt(JsonElement root, string key, out int value)
    {
        value = 0;
        return root.TryGetProperty(key, out var field) && field.ValueKind == JsonValueKind.Number
            && field.TryGetInt32(out value) && value >= 0;
    }

    internal static ProbeObservation Result(PlatformTarget target, string status, string reason,
        DateTimeOffset? at, int? code, double? latency, IReadOnlyDictionary<string, string>? components = null)
        => new(target.TargetId, target.Name, target.Role, target.Track, target.Kind, target.Url,
            target.Enabled, status, reason, at, code, latency, components ?? new Dictionary<string, string>(), target.EvidenceScope);
}
