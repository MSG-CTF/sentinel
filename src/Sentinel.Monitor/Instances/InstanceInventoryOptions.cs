using Microsoft.Extensions.Configuration;
using System.Text.RegularExpressions;

namespace Sentinel.Monitor.Instances;

public sealed class InstanceInventoryOptions
{
    public const string SectionName = "instance_monitor";
    public bool Enabled { get; set; }
    [ConfigurationKeyName("scheduler_url")]
    public string SchedulerUrl { get; set; } = "http://scheduler:8080/api/instances";
    [ConfigurationKeyName("scheduler_token_env")]
    public string SchedulerTokenEnv { get; set; } = "SCHEDULER_API_TOKEN";
    [ConfigurationKeyName("poll_interval_seconds")]
    public int PollIntervalSeconds { get; set; } = 30;
    [ConfigurationKeyName("timeout_seconds")]
    public int TimeoutSeconds { get; set; } = 4;
    [ConfigurationKeyName("runtime_targets")]
    public List<RuntimeInventoryTarget> RuntimeTargets { get; set; } = [];
    [ConfigurationKeyName("functional_checks")]
    public List<LocalFunctionalCheck> FunctionalChecks { get; set; } = [];

    public bool IsValid() => !Enabled ||
        (PollIntervalSeconds is >= 5 and <= 300 && TimeoutSeconds is >= 1 and <= 10
         && SchedulerInventoryClient.TryEndpoint(SchedulerUrl, out _) && ValidTokenName(SchedulerTokenEnv)
         && RuntimeTargets.Count <= 16 && RuntimeTargets.All(t => t.IsValid())
         && RuntimeTargets.Select(t => t.TargetId).Distinct().Count() == RuntimeTargets.Count
         && FunctionalChecks.Count <= 20 && FunctionalChecks.All(c => c.IsValid())
         && FunctionalChecks.Select(c => c.InstanceId).Distinct().Count() == FunctionalChecks.Count
         && FunctionalChecks.Select(c => c.CheckId).Distinct().Count() == FunctionalChecks.Count);

    internal static bool ValidUrl(string value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme is "http" or "https" && uri.Host.Length > 0
        && uri.UserInfo.Length == 0 && uri.Query.Length == 0 && uri.Fragment.Length == 0;
    internal static bool ValidTokenName(string value) => Regex.IsMatch(value, "^[A-Z][A-Z0-9_]{0,127}$");
}

// Explicit opt-in to an owned local fixture, never a URL discovered from Scheduler
public sealed class LocalFunctionalCheck
{
    [ConfigurationKeyName("check_id")]
    public string CheckId { get; set; } = "";
    [ConfigurationKeyName("instance_id")]
    public Guid InstanceId { get; set; }
    [ConfigurationKeyName("challenge_id")]
    public Guid ChallengeId { get; set; }
    [ConfigurationKeyName("target_id")]
    public string TargetId { get; set; } = "";
    [ConfigurationKeyName("runtime_workload_id")]
    public string RuntimeWorkloadId { get; set; } = "";
    public string Url { get; set; } = "";
    public bool IsValid() => Regex.IsMatch(CheckId, "^[a-z][a-z0-9_]{0,63}$")
        && InstanceId != Guid.Empty && ChallengeId != Guid.Empty
        && Regex.IsMatch(TargetId, "^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
        && !string.IsNullOrWhiteSpace(RuntimeWorkloadId) && RuntimeWorkloadId.Length <= 256
        && InstanceInventoryOptions.ValidUrl(Url) && new Uri(Url) is var uri
        && uri.Scheme == "http" && uri.Host is "127.0.0.1" or "host.docker.internal"
        && uri.Port is >= 30080 and <= 30089 && uri.AbsolutePath == "/calculate";
}

public sealed class RuntimeInventoryTarget
{
    [ConfigurationKeyName("target_id")]
    public string TargetId { get; set; } = "";
    [ConfigurationKeyName("base_url")]
    public string BaseUrl { get; set; } = "";
    [ConfigurationKeyName("auth_token_env")]
    public string AuthTokenEnv { get; set; } = "";
    public bool IsValid() => Regex.IsMatch(TargetId, "^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
        && InstanceInventoryOptions.ValidUrl(BaseUrl) && new Uri(BaseUrl).AbsolutePath == "/"
        && InstanceInventoryOptions.ValidTokenName(AuthTokenEnv);
}
