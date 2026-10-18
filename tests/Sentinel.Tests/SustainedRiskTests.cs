using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Configuration;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class SustainedRiskTests
{
    internal static RiskPolicy Policy() => JsonSerializer.Deserialize<RiskPolicy>(
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "aws-risk-policy.json")), PlatformJson.Options)!;
    internal static readonly DateTimeOffset Epoch = DateTimeOffset.Parse("2026-10-19T00:00:00Z");
    internal static ProbeObservation Sample(int tick, double cpu = 99, double memory = 40, double disk = 60,
        double latency = 20, string status = "HEALTHY", string kind = "service_heartbeat", string target = "scheduler") =>
        new(target, "Scheduler", "SCHEDULER", "CONTROL", kind, null, true, status, "SERVICE_ACTIVE",
            Epoch.AddSeconds(tick), null, latency, new Dictionary<string, string>
            {
                ["cpu_used_percent"] = cpu.ToString(CultureInfo.InvariantCulture),
                ["memory_used_percent"] = memory.ToString(CultureInfo.InvariantCulture),
                ["disk_used_percent"] = disk.ToString(CultureInfo.InvariantCulture),
            });
    private static RiskSignal Cpu(RiskAssessment risk) => risk.Signals.Single(s => s.Metric == "cpu_used_percent");

    [Fact]
    public void ProductionPolicyBindsAndPublishesConfirmationConditions()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "aws-risk-policy.json");
        var policy = new ConfigurationBuilder().AddJsonFile(path).Build().Get<RiskPolicy>()!;
        Assert.True(policy.IsValid());
        Assert.Equal(90, policy.CpuUsedPercent.Warning);
        Assert.Equal(180, policy.CpuUsedPercent.WarningSeconds);
        Assert.Equal(3, policy.CpuUsedPercent.WarningSamples);
        Assert.Equal(300, policy.CpuUsedPercent.CriticalSeconds);
        Assert.Equal(0, policy.DiskUsedPercent.CriticalSeconds);
        var json = JsonSerializer.Serialize(policy, PlatformJson.Options);
        Assert.Contains("\"warning_seconds\":180", json);
        Assert.Contains("\"critical_samples\":5", json);
        foreach (var invalid in new[] { -1, 1801 })
        { policy.CpuUsedPercent.WarningSeconds = invalid; Assert.False(policy.IsValid()); }
        policy.CpuUsedPercent.WarningSeconds = 180;
        foreach (var invalid in new[] { 0, 121 })
        { policy.CpuUsedPercent.CriticalSamples = invalid; Assert.False(policy.IsValid()); }
    }

    [Fact]
    public void BriefWorkloadIsVisibleWithoutYellowOrRecoveryChatter()
    {
        var evaluator = new RiskEvaluator(Policy());
        var first = evaluator.Evaluate(Sample(0));
        Assert.Equal("NORMAL", first.Level);
        Assert.Equal("CRITICAL", Cpu(first).PendingLevel);
        Assert.Equal(99, Cpu(first).Value);
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(60)).Level);
        var settled = evaluator.Evaluate(Sample(120, cpu: 50, disk: 84.5));
        Assert.Equal("NORMAL", settled.Level);
        Assert.All(settled.Signals, s => Assert.Null(s.PendingLevel));
    }

    [Fact]
    public void PersistentCpuWarnsAtThreeMinutesAndEscalatesAtFive()
    {
        var evaluator = new RiskEvaluator(Policy());
        foreach (var tick in new[] { 0, 60, 120 }) Assert.Equal("NORMAL", evaluator.Evaluate(Sample(tick)).Level);
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(180)).Level);
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(240)).Level);
        Assert.Equal("CRITICAL", evaluator.Evaluate(Sample(300)).Level);
        Assert.Equal("CRITICAL", evaluator.Evaluate(Sample(360, cpu: 80)).Level);
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(420, cpu: 80)).Level);
    }

    [Fact]
    public void ConfirmationRequiresBothElapsedTimeAndDistinctReports()
    {
        var policy = Policy(); policy.CpuUsedPercent.WarningSamples = 5;
        var evaluator = new RiskEvaluator(policy);
        evaluator.Evaluate(Sample(0, cpu: 95)); evaluator.Evaluate(Sample(120, cpu: 95));
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(180, cpu: 95)).Level);
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(210, cpu: 95)).Level);
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(240, cpu: 95)).Level);
    }

    [Fact]
    public void DuplicateAndOlderReportsCannotChangeValuesOrCountTowardEntryOrClear()
    {
        var evaluator = new RiskEvaluator(Policy());
        evaluator.Evaluate(Sample(0)); evaluator.Evaluate(Sample(60)); evaluator.Evaluate(Sample(120));
        for (var repeat = 0; repeat < 10; repeat++)
        {
            var duplicate = evaluator.Evaluate(Sample(120, cpu: 0));
            Assert.Equal("NORMAL", duplicate.Level); Assert.Equal(99, Cpu(duplicate).Value);
        }
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(30, cpu: 0)).Level);
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(180)).Level);
        evaluator.Evaluate(Sample(240, cpu: 80));
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(240, cpu: 0)).Level);
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(300, cpu: 80)).Level);
    }

    [Theory]
    [InlineData("STALE")]
    [InlineData("UNVERIFIED")]
    [InlineData("PENDING")]
    [InlineData("UNCONFIGURED")]
    [InlineData("UNKNOWN")]
    public void UnmeasuredEvidenceBreaksPendingConfirmation(string status)
    {
        var evaluator = new RiskEvaluator(Policy());
        evaluator.Evaluate(Sample(0)); evaluator.Evaluate(Sample(60)); evaluator.Evaluate(Sample(120));
        Assert.Empty(evaluator.Evaluate(Sample(130, status: status)).Signals);
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(180)).Level);
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(240)).Level);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void MissingOrInvalidMetricBreaksItsPendingConfirmation(bool invalid)
    {
        var evaluator = new RiskEvaluator(Policy());
        evaluator.Evaluate(Sample(0)); evaluator.Evaluate(Sample(60)); evaluator.Evaluate(Sample(120));
        var sample = Sample(130);
        var components = new Dictionary<string, string>(sample.Components);
        if (invalid) components["cpu_used_percent"] = "NaN"; else components.Remove("cpu_used_percent");
        Assert.DoesNotContain(evaluator.Evaluate(sample with { Components = components }).Signals, s => s.Metric == "cpu_used_percent");
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(180)).Level);
    }

    [Fact]
    public void LongReportGapCannotCompleteEntryOrBridgeRecoverySamples()
    {
        var evaluator = new RiskEvaluator(Policy());
        evaluator.Evaluate(Sample(0)); evaluator.Evaluate(Sample(60));
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(240)).Level);
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(300)).Level);
        evaluator.Evaluate(Sample(360));
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(420)).Level);
        evaluator.Evaluate(Sample(480, cpu: 80));
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(660, cpu: 80)).Level);
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(720, cpu: 80)).Level);
    }

    [Fact]
    public void ASubcriticalSampleResetsOnlyTheCriticalCandidate()
    {
        var evaluator = new RiskEvaluator(Policy());
        foreach (var tick in new[] { 0, 60, 120 }) evaluator.Evaluate(Sample(tick));
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(180, cpu: 94)).Level);
        foreach (var tick in new[] { 240, 300, 360, 420, 480 }) Assert.Equal("WARNING", evaluator.Evaluate(Sample(tick)).Level);
        Assert.Equal("CRITICAL", evaluator.Evaluate(Sample(540)).Level);
    }

    [Theory]
    [InlineData(95, 40)]
    [InlineData(40, 95)]
    public void ExhaustionStillEscalatesImmediately(double memory, double disk) =>
        Assert.Equal("CRITICAL", new RiskEvaluator(Policy()).Evaluate(Sample(0, cpu: 10, memory: memory, disk: disk)).Level);

    [Theory]
    [InlineData("DOWN", "CRITICAL")]
    [InlineData("DEGRADED", "WARNING")]
    public void ServiceFailuresBypassNumericConfirmation(string status, string expected) =>
        Assert.Equal(expected, new RiskEvaluator(Policy()).Evaluate(Sample(0, status: status)).Level);

    [Fact]
    public void LatencyUsesProbeCadenceAndSustainedThresholds()
    {
        var evaluator = new RiskEvaluator(Policy(), probeGapSeconds: 39);
        foreach (var tick in new[] { 0, 15, 30, 45, 60, 75 })
            Assert.Equal("NORMAL", evaluator.Evaluate(Sample(tick, latency: 2100, kind: "http")).Level);
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(90, latency: 2100, kind: "http")).Level);
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(105, latency: 3600, kind: "http")).Level);
        Assert.Equal("WARNING", evaluator.Evaluate(Sample(120, latency: 3600, kind: "http")).Level);
        Assert.Equal("CRITICAL", evaluator.Evaluate(Sample(135, latency: 3600, kind: "http")).Level);
        var interrupted = new RiskEvaluator(Policy(), probeGapSeconds: 39);
        interrupted.Evaluate(Sample(0, latency: 2100, kind: "http"));
        Assert.Equal("NORMAL", interrupted.Evaluate(Sample(90, latency: 2100, kind: "http")).Level);
    }

    [Fact]
    public void TargetAndMetricCandidatesAreIndependent()
    {
        var evaluator = new RiskEvaluator(Policy());
        evaluator.Evaluate(Sample(0)); evaluator.Evaluate(Sample(60)); evaluator.Evaluate(Sample(120));
        Assert.Equal("NORMAL", evaluator.Evaluate(Sample(180, target: "other-host")).Level);
        var firstMemory = evaluator.Evaluate(Sample(180, cpu: 40, memory: 92));
        Assert.Equal("NORMAL", firstMemory.Level);
        Assert.Equal("WARNING", firstMemory.Signals.Single(s => s.Metric == "memory_used_percent").PendingLevel);
    }
}
