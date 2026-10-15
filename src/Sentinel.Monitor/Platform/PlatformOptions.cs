using System.Text.RegularExpressions;
using Microsoft.Extensions.Configuration;
using Sentinel.Monitor.Instances;

namespace Sentinel.Monitor.Platform;

public sealed class PlatformOptions
{
    public const string SectionName = "platform_monitor";
    public string Profile { get; set; } = "local";
    [ConfigurationKeyName("poll_interval_seconds")]
    public int PollIntervalSeconds { get; set; } = 15;
    [ConfigurationKeyName("timeout_seconds")]
    public int TimeoutSeconds { get; set; } = 4;
    [ConfigurationKeyName("failure_threshold")]
    public int FailureThreshold { get; set; } = 3;
    [ConfigurationKeyName("recovery_threshold")]
    public int RecoveryThreshold { get; set; } = 2;
    [ConfigurationKeyName("event_file")]
    public string EventFile { get; set; } = "data/events.jsonl";
    public List<PlatformTarget> Targets { get; set; } = [];
    [ConfigurationKeyName("risk_policy")]
    public RiskPolicy RiskPolicy { get; set; } = new();

    public bool IsValid()
    {
        return Profile.Length is > 0 and <= 32
            && PollIntervalSeconds is >= 5 and <= 300
            && TimeoutSeconds is >= 1 and <= 30
            && FailureThreshold is >= 1 and <= 10
            && RecoveryThreshold is >= 1 and <= 10
            && !string.IsNullOrWhiteSpace(EventFile) && EventFile.Length <= 500
            && Targets.Count is > 0 and <= 32
            && Targets.Select(target => target.TargetId).Distinct().Count() == Targets.Count
            && Targets.All(target => target.IsValid()) && RiskPolicy.IsValid();
    }
}

public sealed class PlatformTarget
{
    [ConfigurationKeyName("target_id")]
    public string TargetId { get; set; } = "";
    public string Name { get; set; } = "";
    public string Role { get; set; } = "";
    public string Track { get; set; } = "";
    public string Kind { get; set; } = "backend_healthz";
    public string? Url { get; set; }
    public bool Enabled { get; set; } = true;
    [ConfigurationKeyName("expected_text")]
    public string? ExpectedText { get; set; }
    [ConfigurationKeyName("auth_token_env")]
    public string? AuthTokenEnv { get; set; }
    [ConfigurationKeyName("expected_fields")]
    public Dictionary<string, string> ExpectedFields { get; set; } = [];
    [ConfigurationKeyName("evidence_scope")]
    public string? EvidenceScope { get; set; }
    [ConfigurationKeyName("unconfigured_reason")]
    public string UnconfiguredReason { get; set; } = "ENDPOINT_NOT_CONFIGURED";

    public bool IsValid()
    {
        if (!Regex.IsMatch(TargetId, "^[a-z][a-z0-9_-]{0,63}$")
            || Name.Length is 0 or > 100 || Role.Length is 0 or > 32 || Track.Length is 0 or > 32
            || Kind is not ("backend_healthz" or "http" or "json_fields" or "scheduler_inventory" or "broker_inventory" or "runtime_status" or "runtime_node" or "runtime_operation" or "service_heartbeat" or "auth_boundary")
            || EvidenceScope?.Length > 300
            || !Regex.IsMatch(UnconfiguredReason, "^[A-Z][A-Z0-9_]{0,63}$"))
            return false;
        if (Kind == "service_heartbeat") return !Enabled ||
            (string.IsNullOrEmpty(Url) && AuthTokenEnv is not null && Regex.IsMatch(AuthTokenEnv, "^[A-Z][A-Z0-9_]{0,63}$"));
        if (Enabled && (Kind is "scheduler_inventory" or "broker_inventory" or "runtime_status" or "runtime_node" or "runtime_operation") && string.IsNullOrWhiteSpace(AuthTokenEnv))
            return false;
        if (Enabled && (Kind is "runtime_status" or "runtime_node")
            && (!Guid.TryParse(ExpectedFields.GetValueOrDefault("instance_id"), out _)
                || string.IsNullOrWhiteSpace(ExpectedFields.GetValueOrDefault("target_id"))))
            return false;
        if (Enabled && Kind == "runtime_operation"
            && string.IsNullOrWhiteSpace(ExpectedFields.GetValueOrDefault("operation_id")))
            return false;
        if (!Enabled && string.IsNullOrEmpty(Url)) return true;
        if (Kind == "scheduler_inventory") return SchedulerInventoryClient.TryEndpoint(Url, out _);
        return Uri.TryCreate(Url, UriKind.Absolute, out var uri)
            && uri.Scheme is "http" or "https" && !string.IsNullOrEmpty(uri.Host)
            && string.IsNullOrEmpty(uri.UserInfo) && string.IsNullOrEmpty(uri.Query)
            && string.IsNullOrEmpty(uri.Fragment)
            && (Kind != "json_fields" || ExpectedFields.Count > 0);
    }
}
