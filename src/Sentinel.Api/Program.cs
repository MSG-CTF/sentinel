using Prometheus;
using Sentinel.Contracts.Health;
using Sentinel.Infrastructure.CtfMock;
using Sentinel.Monitor.Django;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddCtfMockClient(builder.Configuration);
builder.Services.AddDjangoHealthMonitor(builder.Configuration);

var app = builder.Build();
app.UseHttpMetrics();

// 운영팀과 모니터링 시스템이 sentinel 자체 상태를 빠르게 확인하는 최소 엔드포인트입니다.
app.MapGet("/health", () =>
{
    var response = new HealthResponse(
        Service: "sentinel",
        Status: HealthStatus.Healthy,
        CheckedAt: DateTimeOffset.UtcNow);

    return Results.Ok(response);
});
app.MapMetrics();

app.Run();

// 통합 테스트에서 WebApplicationFactory가 진입점을 찾을 수 있도록 공개합니다.
public partial class Program
{
}
