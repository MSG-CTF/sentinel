using Sentinel.Contracts.Health;
using Sentinel.Infrastructure;
using Sentinel.Monitor;
using Xunit;

namespace Sentinel.Tests;

public sealed class BootstrapTests
{
    [Fact]
    public void HealthResponse_ShouldKeepSentinelHealthContract()
    {
        var checkedAt = DateTimeOffset.Parse("2026-07-22T00:00:00Z");
        var response = new HealthResponse("sentinel", HealthStatus.Healthy, checkedAt);

        Assert.Equal("sentinel", response.Service);
        Assert.Equal(HealthStatus.Healthy, response.Status);
        Assert.Equal(checkedAt, response.CheckedAt);
    }

    [Fact]
    public void ProjectDefaults_ShouldExposeStableNames()
    {
        Assert.Equal("ctf-server", ExternalEndpointNames.CtfServer);
        Assert.Equal("scheduler", ExternalEndpointNames.Scheduler);
        Assert.Equal("admin-alert", ExternalEndpointNames.AdminAlert);
        Assert.Equal("django-server-monitor", MonitorDefaults.DjangoServerMonitorName);
        Assert.Equal("instance-monitor", MonitorDefaults.InstanceMonitorName);
    }
}
