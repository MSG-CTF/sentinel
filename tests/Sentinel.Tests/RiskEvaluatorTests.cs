using System.Text.Json;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class RiskEvaluatorTests
{
    [Theory]
    [InlineData("79.9", "NORMAL")]
    [InlineData("80", "WARNING")]
    [InlineData("95", "CRITICAL")]
    [InlineData("100", "CRITICAL")]
    public void CpuThresholdsAreMeasuredIndependently(string value,string level)
    {
        var sample=Sample() with { Components=new Dictionary<string,string> {{"cpu_used_percent",value}} };
        Assert.Equal(level,new RiskEvaluator(new()).Evaluate(sample).Level);
    }
    private static readonly DateTimeOffset Epoch = DateTimeOffset.Parse("2026-10-14T10:00:00Z");
    private static ProbeObservation Sample(double latency = 20, double memory = 20, double disk = 40, int tick = 0,
        string status = "HEALTHY", string target = "discord-bot") => new(target,"Test","DISCORD","CONTROL",
        "service_heartbeat",null,true,status,"SERVICE_ACTIVE",Epoch.AddSeconds(tick),null,latency,
        new Dictionary<string,string> { ["memory_used_percent"] = memory.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["disk_used_percent"] = disk.ToString(System.Globalization.CultureInfo.InvariantCulture) });

    [Theory]
    [InlineData(79.99, "NORMAL")]
    [InlineData(80, "WARNING")]
    [InlineData(89.99, "WARNING")]
    [InlineData(90, "CRITICAL")]
    [InlineData(100, "CRITICAL")]
    public void ResourceThresholdsIncludeBoundary(double value, string level)
    {
        Assert.Equal(level,new RiskEvaluator(new()).Evaluate(Sample(memory:value)).Level);
        Assert.Equal(level,new RiskEvaluator(new()).Evaluate(Sample(disk:value)).Level);
    }
    [Theory]
    [InlineData(999, "NORMAL")]
    [InlineData(1000, "WARNING")]
    [InlineData(2999, "WARNING")]
    [InlineData(3000, "CRITICAL")]
    public void LatencyThresholdsAreIndependentOfServiceHealth(double value,string level)
    {
        var sample = Sample(latency:value);
        Assert.Equal(level,new RiskEvaluator(new()).Evaluate(sample).Level);
        Assert.Equal("HEALTHY",sample.Status);
    }
    [Fact]
    public void RecoveryNeedsDistinctSamplesBelowClearMargin()
    {
        var risk = new RiskEvaluator(new());
        Assert.Equal("CRITICAL",risk.Evaluate(Sample(memory:92)).Level);
        Assert.Equal("CRITICAL",risk.Evaluate(Sample(memory:89,tick:1)).Level);
        var first = risk.Evaluate(Sample(memory:84,tick:2));
        Assert.True(first.Signals.Single(s=>s.Metric == "memory_used_percent").Recovering);
        Assert.Equal("CRITICAL",risk.Evaluate(Sample(memory:84,tick:2)).Level);
        Assert.Equal("WARNING",risk.Evaluate(Sample(memory:84,tick:3)).Level);
        Assert.Equal("WARNING",risk.Evaluate(Sample(memory:76,tick:4)).Level);
        Assert.Equal("WARNING",risk.Evaluate(Sample(memory:74,tick:5)).Level);
        Assert.Equal("NORMAL",risk.Evaluate(Sample(memory:74,tick:6)).Level);
    }
    [Fact]
    public void CriticalClearCannotBypassWarningClearMargin()
    {
        var risk = new RiskEvaluator(new()); risk.Evaluate(Sample(memory:95));
        risk.Evaluate(Sample(memory:79,tick:1));
        Assert.Equal("WARNING",risk.Evaluate(Sample(memory:79,tick:2)).Level);
        risk.Evaluate(Sample(memory:74,tick:3));
        Assert.Equal("NORMAL",risk.Evaluate(Sample(memory:74,tick:4)).Level);
    }
    [Fact]
    public void BounceResetsClearStreakAndAnEscalationIsImmediate()
    {
        var risk = new RiskEvaluator(new());
        risk.Evaluate(Sample(memory:81));
        risk.Evaluate(Sample(memory:74,tick:1));
        risk.Evaluate(Sample(memory:78,tick:2));
        Assert.Equal("WARNING",risk.Evaluate(Sample(memory:74,tick:3)).Level);
        Assert.Equal("CRITICAL",risk.Evaluate(Sample(memory:92,tick:4)).Level);
        Assert.Equal("NORMAL",risk.Evaluate(Sample(memory:74,target:"another")).Level);
    }
    [Theory]
    [InlineData("STALE","UNKNOWN")]
    [InlineData("UNVERIFIED","UNKNOWN")]
    [InlineData("PENDING","PENDING")]
    [InlineData("UNCONFIGURED","PENDING")]
    public void MissingEvidenceNeverReusesOldNumbers(string status,string level)
    {
        var risk = new RiskEvaluator(new()); risk.Evaluate(Sample(memory:95));
        var result = risk.Evaluate(Sample(memory:95,tick:1,status:status));
        Assert.Equal(level,result.Level); Assert.Empty(result.Signals);
        Assert.Equal("NORMAL",risk.Evaluate(Sample(tick:2)).Level);
    }
    [Fact]
    public void DownIsCriticalEvenWithoutNumericMetrics()
    {
        var sample = Sample(status:"DOWN") with { Components = new Dictionary<string,string>(), LatencyMs = null };
        Assert.Equal("CRITICAL",new RiskEvaluator(new()).Evaluate(sample).Level);
    }
    [Fact]
    public void InvalidOrMissingMetricsAreNotZeroPercentMeasurements()
    {
        var sample = Sample() with { Components = new Dictionary<string,string> { ["memory_used_percent"]="NaN", ["disk_used_percent"]="101" }, LatencyMs=double.NaN };
        Assert.Empty(new RiskEvaluator(new()).Evaluate(sample).Signals);
    }
    [Fact]
    public void CustomThresholdsAreValidatedAndPublishedWithSnakeCase()
    {
        var policy = new RiskPolicy { LatencyMs = new() { Warning=400, Critical=800, ClearMargin=100 } };
        Assert.True(policy.IsValid());
        Assert.Equal("WARNING",new RiskEvaluator(policy).Evaluate(Sample(latency:500)).Level);
        var json = JsonSerializer.Serialize(policy,PlatformJson.Options);
        Assert.Contains("\"latency_ms\"",json); Assert.Contains("\"clear_samples\":2",json);
        policy.LatencyMs.Critical = 400; Assert.False(policy.IsValid());
        policy.LatencyMs.Critical = double.PositiveInfinity; Assert.False(policy.IsValid());
    }
}
