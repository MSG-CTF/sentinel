using System.Net;
using System.Net.Http.Json;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Sentinel.Contracts.Alerts;
using Sentinel.Contracts.Events;
using Sentinel.Contracts.Scheduler;
using Sentinel.Infrastructure.CtfMock;
using Xunit;

namespace Sentinel.Tests;

public sealed class CtfMockClientTests
{
    [Fact]
    public void AddCtfMockClient_ShouldRegisterClientFromConfiguration()
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["CtfMock:BaseUrl"] = "http://ctf-mock.local",
                ["CtfMock:Timeout"] = "00:00:03",
            })
            .Build();
        var services = new ServiceCollection();

        services.AddCtfMockClient(configuration);
        using var provider = services.BuildServiceProvider();

        var client = provider.GetRequiredService<ICtfMockClient>();

        Assert.IsType<CtfMockClient>(client);
    }

    [Fact]
    public async Task GetDjangoHealthAsync_ShouldCallHealthEndpointAndMapResponse()
    {
        var handler = new StubHttpMessageHandler(request =>
        {
            Assert.Equal(HttpMethod.Get, request.Method);
            Assert.Equal("/health", request.RequestUri?.AbsolutePath);

            return JsonResponse(new
            {
                status = "unhealthy",
                database = "unhealthy",
                cache = "healthy",
                reason = "database_unavailable",
                checkedAt = "2026-07-28T01:00:00Z",
            });
        });
        var client = CreateClient(handler);

        var response = await client.GetDjangoHealthAsync();

        Assert.Equal("database_unavailable", response.Reason);
        Assert.Equal("Unhealthy", response.Status.ToString());
        Assert.Equal("Unhealthy", response.DatabaseStatus.ToString());
        Assert.Equal("Healthy", response.CacheStatus.ToString());
    }

    [Fact]
    public async Task GetInstancesAsync_ShouldCallInstancesEndpointAndMapResponse()
    {
        var handler = new StubHttpMessageHandler(request =>
        {
            Assert.Equal(HttpMethod.Get, request.Method);
            Assert.Equal("/api/instances", request.RequestUri?.AbsolutePath);

            return JsonResponse(new
            {
                instances = new[]
                {
                    new
                    {
                        instanceId = "instance-web-1",
                        teamId = "team-1",
                        challengeId = "web-100",
                        status = "unhealthy",
                        createdAt = "2026-07-28T00:00:00Z",
                        expiresAt = "2026-07-28T02:00:00Z",
                        lastActivityAt = "2026-07-28T00:30:00Z",
                    },
                },
            });
        });
        var client = CreateClient(handler);

        var instances = await client.GetInstancesAsync();

        var instance = Assert.Single(instances);
        Assert.Equal("instance-web-1", instance.InstanceId);
        Assert.Equal("team-1", instance.TeamId);
        Assert.Equal("web-100", instance.ChallengeId);
        Assert.Equal("Unhealthy", instance.Status.ToString());
    }

    [Fact]
    public async Task ReadMethods_ShouldCallRemainingCtfMockEndpoints()
    {
        var calls = new Queue<string>(new[]
        {
            "/api/status",
            "/api/instances/instance-web-1/health",
            "/api/broker/accounts",
        });
        var handler = new StubHttpMessageHandler(request =>
        {
            var expectedPath = calls.Dequeue();
            Assert.Equal(HttpMethod.Get, request.Method);
            Assert.Equal(expectedPath, request.RequestUri?.AbsolutePath);

            if (expectedPath == "/api/status")
            {
                return JsonResponse(new
                {
                    status = "healthy",
                    apis = new[]
                    {
                        new
                        {
                            name = "scoreboard",
                            path = "/api/scoreboard",
                            status = "healthy",
                            latencyMs = 120,
                        },
                    },
                    checkedAt = "2026-07-28T01:00:00Z",
                });
            }

            if (expectedPath.Contains("/health", StringComparison.Ordinal))
            {
                return JsonResponse(new
                {
                    instanceId = "instance-web-1",
                    status = "timeout",
                    reason = "health_check_timeout",
                    checkedAt = "2026-07-28T01:01:00Z",
                });
            }

            return JsonResponse(new[]
            {
                new
                {
                    accountId = "acct-001",
                    status = "budget_risk",
                    quota = new
                    {
                        cpuUsed = 60,
                        cpuLimit = 100,
                    },
                    budget = new
                    {
                        usedUsd = 95m,
                        limitUsd = 100m,
                    },
                },
            });
        });
        var client = CreateClient(handler);

        var apiStatus = await client.GetApiStatusAsync();
        var instanceHealth = await client.GetInstanceHealthAsync("instance-web-1");
        var brokerAccounts = await client.GetBrokerAccountsAsync();

        Assert.Equal("healthy", apiStatus.Status);
        Assert.Equal("scoreboard", Assert.Single(apiStatus.Apis).Name);
        Assert.Equal("health_check_timeout", instanceHealth.Reason);
        Assert.Equal("Unhealthy", instanceHealth.Status.ToString());
        Assert.Equal("BudgetRisk", Assert.Single(brokerAccounts).State.ToString());
        Assert.Empty(calls);
    }

    [Fact]
    public async Task SendSchedulerSignalAsync_ShouldPostSignalToSchedulerEndpoint()
    {
        HttpRequestMessage? capturedRequest = null;
        var handler = new StubHttpMessageHandler(async request =>
        {
            capturedRequest = request;
            var body = await request.Content!.ReadAsStringAsync();
            Assert.Contains("\"signalId\":\"signal-1\"", body, StringComparison.Ordinal);
            Assert.Contains("\"targetId\":\"instance-web-1\"", body, StringComparison.Ordinal);

            return JsonResponse(new { accepted = true, received = 1 });
        });
        var client = CreateClient(handler);
        var signal = new SchedulerSignal(
            SignalId: "signal-1",
            Type: SchedulerSignalType.RestartInstance,
            TargetId: "instance-web-1",
            Reason: "instance_timeout",
            RequestedAt: DateTimeOffset.Parse("2026-07-28T01:00:00Z"),
            Metadata: new Dictionary<string, string>());

        var result = await client.SendSchedulerSignalAsync(signal);

        Assert.Equal(HttpMethod.Post, capturedRequest?.Method);
        Assert.Equal("/api/scheduler/signals", capturedRequest?.RequestUri?.AbsolutePath);
        Assert.True(result.Accepted);
        Assert.Equal(1, result.Received);
    }

    [Fact]
    public async Task SendAdminAlertAsync_ShouldPostAlertToAdminEndpoint()
    {
        HttpRequestMessage? capturedRequest = null;
        var handler = new StubHttpMessageHandler(async request =>
        {
            capturedRequest = request;
            var body = await request.Content!.ReadAsStringAsync();
            Assert.Contains("\"alertId\":\"alert-1\"", body, StringComparison.Ordinal);
            Assert.Contains("\"severity\":\"Critical\"", body, StringComparison.Ordinal);

            return JsonResponse(new { accepted = true, received = 1 });
        });
        var client = CreateClient(handler);
        var alert = new AdminAlert(
            AlertId: "alert-1",
            Severity: EventSeverity.Critical,
            Title: "Django down",
            Message: "본 CTF 서버 health check가 실패했습니다.",
            Source: "django-server-monitor",
            CreatedAt: DateTimeOffset.Parse("2026-07-28T01:00:00Z"),
            Metadata: new Dictionary<string, string>());

        var result = await client.SendAdminAlertAsync(alert);

        Assert.Equal(HttpMethod.Post, capturedRequest?.Method);
        Assert.Equal("/api/admin/alerts", capturedRequest?.RequestUri?.AbsolutePath);
        Assert.True(result.Accepted);
        Assert.Equal(1, result.Received);
    }

    private static CtfMockClient CreateClient(HttpMessageHandler handler)
    {
        var httpClient = new HttpClient(handler)
        {
            BaseAddress = new Uri("http://ctf-mock.local"),
        };

        return new CtfMockClient(httpClient);
    }

    private static HttpResponseMessage JsonResponse<T>(T value)
    {
        return new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = JsonContent.Create(value, options: CtfMockJson.Options),
        };
    }

    private sealed class StubHttpMessageHandler : HttpMessageHandler
    {
        private readonly Func<HttpRequestMessage, Task<HttpResponseMessage>> _handler;

        public StubHttpMessageHandler(Func<HttpRequestMessage, HttpResponseMessage> handler)
            : this(request => Task.FromResult(handler(request)))
        {
        }

        public StubHttpMessageHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler)
        {
            _handler = handler;
        }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            return _handler(request);
        }
    }
}
