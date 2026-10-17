using System.Globalization;
using System.Text.Json;

namespace Sentinel.Monitor.Platform;

internal sealed record RuntimeContractResult(string Status, string Reason, IReadOnlyDictionary<string, string>? Components = null);

internal static class RuntimeProbeContract
{
    public static RuntimeContractResult Check(JsonElement root, PlatformTarget target, DateTimeOffset now)
    {
        foreach (var field in target.ExpectedFields)
            if (!Text(root, field.Key, out var value) || value != field.Value)
                return new("UNVERIFIED", "RUNTIME_IDENTITY_MISMATCH");

        if (target.Kind == "runtime_operation")
        {
            if (!Text(root, "type", out var type) || type != "CREATE"
                || !Text(root, "status", out var status) || status != "SUCCEEDED"
                || !Integer(root, "attempt", out var attempt) || attempt < 1
                || !Integer(root, "max_attempts", out var maximum) || maximum < attempt
                || !root.TryGetProperty("result", out var result) || result.ValueKind != JsonValueKind.Object
                || !Text(result, "runtime_workload_id", out var workload) || workload.Length == 0)
                return new("UNVERIFIED", "INVALID_RUNTIME_OPERATION");
            return new("HEALTHY", "RUNTIME_STORE_QUERY_READY", new Dictionary<string, string>
            {
                ["operation_status"] = status,
            });
        }

        if (!root.TryGetProperty("observed_at", out var observed) || observed.ValueKind != JsonValueKind.String
            || !observed.TryGetDateTimeOffset(out var timestamp) || Math.Abs((now - timestamp).TotalSeconds) > 60)
            return new("UNVERIFIED", "STALE_RUNTIME_OBSERVATION");
        if (!Text(root, "phase", out var phase) || !Text(root, "runtime_workload_id", out var workloadId)
            || string.IsNullOrEmpty(workloadId) || !Boolean(root, "endpoint_ready", out var endpoint)
            || !Boolean(root, "metrics_available", out var metrics)
            || !root.TryGetProperty("node", out var node) || node.ValueKind != JsonValueKind.Object
            || !Boolean(node, "ready", out var ready) || !Boolean(node, "memory_pressure", out var memory)
            || !Boolean(node, "disk_pressure", out var disk) || !Boolean(node, "pid_pressure", out var pid)
            || !root.TryGetProperty("containers", out var containers) || containers.ValueKind != JsonValueKind.Array
            || containers.GetArrayLength() > 64)
            return new("UNVERIFIED", "INVALID_RUNTIME_STATUS");

        var allReady = containers.GetArrayLength() > 0;
        var restarts = 0;
        foreach (var container in containers.EnumerateArray())
        {
            if (container.ValueKind != JsonValueKind.Object || !Text(container, "name", out var name) || name.Length == 0
                || !Text(container, "pod_name", out var pod) || pod.Length == 0
                || !Text(container, "state", out var state) || !Boolean(container, "ready", out var containerReady)
                || !Integer(container, "restart_count", out var count) || count < 0 || count > 1_000_000)
                return new("UNVERIFIED", "INVALID_RUNTIME_STATUS");
            allReady &= containerReady && state == "RUNNING";
            restarts += count;
        }
        var components = new Dictionary<string, string>
        {
            ["node_ready"] = ready ? "ok" : "down",
            ["endpoint_ready"] = endpoint ? "ok" : "down",
            ["metrics_available"] = metrics ? "yes" : "no",
            ["container_count"] = containers.GetArrayLength().ToString(CultureInfo.InvariantCulture),
            ["restart_count"] = restarts.ToString(CultureInfo.InvariantCulture),
        };
        if (!ready) return new("DOWN", "K3S_NODE_NOT_READY", components);
        if (memory || disk || pid) return new("DOWN", "K3S_NODE_PRESSURE", components);
        if (target.Kind == "runtime_node") return new("HEALTHY", "K3S_NODE_READY", components);
        return phase == "READY" && allReady && endpoint
            ? new("HEALTHY", "RUNTIME_WORKLOAD_READY", components)
            : new("DOWN", "RUNTIME_WORKLOAD_NOT_READY", components);
    }

    private static bool Text(JsonElement root, string field, out string value)
    {
        value = "";
        if (!root.TryGetProperty(field, out var item) || item.ValueKind != JsonValueKind.String) return false;
        value = item.GetString()!;
        return true;
    }

    private static bool Boolean(JsonElement root, string field, out bool value)
    {
        value = false;
        if (!root.TryGetProperty(field, out var item) || item.ValueKind is not (JsonValueKind.True or JsonValueKind.False)) return false;
        value = item.GetBoolean();
        return true;
    }

    private static bool Integer(JsonElement root, string field, out int value)
    {
        value = 0;
        return root.TryGetProperty(field, out var item) && item.ValueKind == JsonValueKind.Number && item.TryGetInt32(out value);
    }
}
