using System.Text.Json;

namespace Sentinel.Monitor.Platform;

internal sealed record RuntimeReadinessResult(string Status, string Reason,
    IReadOnlyDictionary<string, string>? Components = null, DateTimeOffset? CheckedAt = null);

internal static class RuntimeReadinessContract
{
    public static RuntimeReadinessResult Check(JsonElement root, int code, DateTimeOffset now)
    {
        var fields = new Dictionary<string, string>();
        foreach (var field in root.EnumerateObject())
            if (field.Value.ValueKind != JsonValueKind.String || !fields.TryAdd(field.Name, field.Value.GetString()!))
                return new("UNVERIFIED", "INVALID_RUNTIME_READINESS");
        if (fields.Count != 5 || fields.GetValueOrDefault("status") is not ("ok" or "down")
            || fields.GetValueOrDefault("store_mode") is not ("postgres" or "memory")
            || fields.GetValueOrDefault("database") is not ("ok" or "down" or "not_configured")
            || fields.GetValueOrDefault("mode") is not ("primary" or "deployment_bridge")
            || !root.TryGetProperty("checked_at", out var timestamp) || !timestamp.TryGetDateTimeOffset(out var at))
            return new("UNVERIFIED", "INVALID_RUNTIME_READINESS");
        if (now - at > TimeSpan.FromSeconds(30) || at - now > TimeSpan.FromSeconds(5))
            return new("UNVERIFIED", "STALE_RUNTIME_READINESS");
        var durable = fields["store_mode"] == "postgres";
        var healthy = durable && fields["database"] == "ok";
        if ((!durable && fields["database"] != "not_configured")
            || (durable && fields["database"] == "not_configured")
            || fields["status"] != (healthy ? "ok" : "down") || code != (healthy ? 200 : 503))
            return new("UNVERIFIED", "INCONSISTENT_RUNTIME_READINESS");
        fields.Remove("checked_at");
        return new(healthy ? "HEALTHY" : "DOWN",
            healthy ? "RUNTIME_API_READY" : durable ? "RUNTIME_DATABASE_DOWN" : "RUNTIME_STORE_NOT_DURABLE", fields, at);
    }
}
