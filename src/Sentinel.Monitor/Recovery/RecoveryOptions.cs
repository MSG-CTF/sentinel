using Microsoft.Extensions.Configuration;
using Sentinel.Monitor.Platform;

namespace Sentinel.Monitor.Recovery;

public sealed class RecoveryOptions
{
    public const string SectionName = "platform_recovery";
    public const string AssetId = "internal-backend";
    public const string Track = "INTERNAL";
    public const string ExecutorId = "local-docker";
    public const string Action = "start_stopped_service";

    public bool Enabled { get; set; }
    [ConfigurationKeyName("enabled_assets")]
    public string[] EnabledAssets { get; set; } = [];
    public string[] EffectiveAssets => EnabledAssets.Length == 0 ? [AssetId] : EnabledAssets;
    [ConfigurationKeyName("token_file")]
    public string TokenFile { get; set; } = "/run/sentinel-secrets/executor.token";
    [ConfigurationKeyName("state_file")]
    public string StateFile { get; set; } = "data/local-recovery.json";
    [ConfigurationKeyName("request_lifetime_seconds")]
    public int RequestLifetimeSeconds { get; set; } = 120;
    [ConfigurationKeyName("verification_timeout_seconds")]
    public int VerificationTimeoutSeconds { get; set; } = 120;
    [ConfigurationKeyName("cooldown_seconds")]
    public int CooldownSeconds { get; set; } = 300;

    public bool IsValid(PlatformOptions platform) => !Enabled ||
        (platform.Profile == "local-recovery"
         && EffectiveAssets is { Length: > 0 and <= 8 }
         && EffectiveAssets.Distinct().Count() == EffectiveAssets.Length
         && EffectiveAssets.All(id => RecoveryBindings.All.Any(binding => binding.AssetId == id
             && binding.Matches(platform.Targets.SingleOrDefault(target => target.TargetId == id))
             && binding.VerificationTargets.Concat(binding.DependencyTargets).All(required =>
                 platform.Targets.Any(target => target.TargetId == required && RecoveryBindings.MatchesTarget(target)))))
         && RequestLifetimeSeconds is >= 30 and <= 300
         && VerificationTimeoutSeconds is >= 30 and <= 300
         && CooldownSeconds is >= 60 and <= 3600
         && !string.IsNullOrWhiteSpace(TokenFile) && !string.IsNullOrWhiteSpace(StateFile));
}

// Configuration selects fixed bindings, never a command, host or service name.
public sealed record RecoveryBinding(string AssetId, string Track, string Service, string Kind, string Url,
    string HealthyReason, string[] DependencyTargets, string[] VerificationTargets)
{
    public bool Matches(PlatformTarget? target) => target is not null && target.Enabled
        && target.TargetId == AssetId && target.Track == Track && target.Kind == Kind && target.Url == Url
        && (Kind != "http" || target.ExpectedText == "<div id=\"root\"></div>")
        && (Kind != "json_fields" || (target.ExpectedFields.GetValueOrDefault("status") == "UP"
            && target.ExpectedFields.GetValueOrDefault("scope") == "local_edge_process"))
        && (Kind != "runtime_status" || (target.ExpectedFields.GetValueOrDefault("instance_id") == RecoveryBindings.LocalInstance
            && target.ExpectedFields.GetValueOrDefault("target_id") == "sentinel-local-k3s"));
}

public static class RecoveryBindings
{
    public const string LocalInstance = "a716671b-48a3-4fcb-813a-1b9a2280463e";
    public const string RuntimeStatusUrl = "http://runtime:8080/internal/v1/instances/" + LocalInstance + "/runtime-status";
    public static string HealthyReasonFor(string id) => All.FirstOrDefault(binding => binding.AssetId == id)?.HealthyReason
        ?? (id switch { "runtime-db" => "RUNTIME_STORE_QUERY_READY", "linux-pool" => "K3S_NODE_READY", _ => "HEALTH_CONTRACT_MATCHED" });
    public static bool MatchesTarget(PlatformTarget target) => All.Any(binding => binding.Matches(target))
        || (target.Enabled && target.Track == "RUNTIME" && target.TargetId == "runtime-db" && target.Kind == "runtime_operation"
            && Guid.TryParse(target.ExpectedFields.GetValueOrDefault("operation_id"), out _)
            && target.Url == "http://runtime:8080/internal/v1/operations/" + target.ExpectedFields["operation_id"])
        || (target.Enabled && target.Track == "RUNTIME" && target.TargetId == "linux-pool" && target.Kind == "runtime_node"
            && target.Url == RuntimeStatusUrl && target.ExpectedFields.GetValueOrDefault("instance_id") == LocalInstance
            && target.ExpectedFields.GetValueOrDefault("target_id") == "sentinel-local-k3s")
        || (target.Enabled && target.Track == "CONTROL" && target.Kind == "json_fields"
            && target.ExpectedFields.GetValueOrDefault("status") == "UP"
            && ((target.TargetId == "scheduler-db" && target.Url == "http://scheduler:8080/actuator/health/db")
                || (target.TargetId == "broker-db" && target.Url == "http://broker:8000/health/ready")));

    public static readonly IReadOnlyList<RecoveryBinding> All = [
        new("internal-backend", "INTERNAL", "backend-internal", "backend_healthz", "http://backend-internal:8080/healthz", "BACKEND_READY", [], []),
        new("external-backend", "EXTERNAL", "backend-external", "backend_healthz", "http://backend-external:8080/healthz", "BACKEND_READY", [], []),
        new("scheduler", "CONTROL", "scheduler", "scheduler_inventory", "http://scheduler:8080/api/instances/recent", "SCHEDULER_QUERY_READY", [], ["scheduler-db"]),
        new("broker", "CONTROL", "broker", "broker_inventory", "http://broker:8000/v1/inventory/resource-targets", "BROKER_QUERY_READY", [], ["broker-db"]),
        new("internal-frontend", "INTERNAL", "frontend-internal", "http", "http://frontend-internal:8080/", "HTTP_REACHABLE", ["internal-backend"], ["internal-backend"]),
        new("external-frontend", "EXTERNAL", "frontend-external", "http", "http://frontend-external:8080/", "HTTP_REACHABLE", ["external-backend"], ["external-backend"]),
        new("edge", "ACCESS", "edge", "json_fields", "http://edge:8080/healthz", "HEALTH_CONTRACT_MATCHED", ["internal-frontend", "external-frontend", "internal-backend", "external-backend"], ["internal-frontend", "external-frontend", "internal-backend", "external-backend"]),
        new("runtime", "RUNTIME", "runtime", "runtime_status", RuntimeStatusUrl, "RUNTIME_WORKLOAD_READY", [], ["runtime-db", "linux-pool"])
    ];
}
