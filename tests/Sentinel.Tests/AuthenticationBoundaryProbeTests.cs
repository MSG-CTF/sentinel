using System.Net;
using System.Text;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class AuthenticationBoundaryProbeTests
{
    private sealed class Handler(HttpStatusCode status, string body, string mediaType) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, mediaType) });
    }
    [Theory]
    [InlineData(401, "{\"code\":\"TOKEN_MISSING\",\"data\":null}", "application/json", "HEALTHY")]
    [InlineData(401, "{\"code\":\"INVALID_CREDENTIALS\",\"data\":null}", "application/json", "UNVERIFIED")]
    [InlineData(401, "<html>login</html>", "text/html", "UNVERIFIED")]
    [InlineData(200, "<html>frontend</html>", "text/html", "UNVERIFIED")]
    [InlineData(503, "{}", "application/json", "DOWN")]
    public async Task VerifiesOnlyTheAuthenticationBoundaryAndNeverInfersDatabaseHealth(int code, string body, string mediaType, string expected)
    {
        var probe = new PlatformProbeClient(new HttpClient(new Handler((HttpStatusCode)code, body, mediaType)));
        var target = new PlatformTarget { TargetId = "api", Name = "API", Role = "API", Track = "PUBLIC",
            Kind = "auth_boundary", Url = "https://example.com/api/v1/auth/me" };
        var result = await probe.CheckAsync(target, 5);
        Assert.Equal(expected, result.Status);
        Assert.Empty(result.Components);
    }
}
