using Xunit;

namespace Sentinel.IntegrationTests;

public sealed class ApiBootstrapTests
{
    [Fact]
    public void ApiProject_ShouldExposeApplicationEntryPoint()
    {
        Assert.Equal("Sentinel.Api", typeof(global::Program).Assembly.GetName().Name);
    }
}
