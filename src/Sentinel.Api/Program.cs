using System.Text.Json;
using Sentinel.Monitor.Platform;
using Sentinel.Monitor.Recovery;

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.ConfigureKestrel(options => options.Limits.MaxRequestBodySize = 4096);
var profileFile = Environment.GetEnvironmentVariable("SENTINEL_PROFILE_FILE")
    ?? Path.Combine(builder.Environment.ContentRootPath, "profiles", "local.json");
builder.Configuration.AddJsonFile(profileFile, optional: false, reloadOnChange: false);
builder.Configuration.AddEnvironmentVariables();
builder.Services.ConfigureHttpJsonOptions(options =>
{
    options.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
    options.SerializerOptions.PropertyNameCaseInsensitive = false;
    options.SerializerOptions.Converters.Add(new UtcTimestampConverter());
});
builder.Services.AddPlatformMonitor(builder.Configuration);
builder.Services.AddPlatformRecovery(builder.Configuration);

var app = builder.Build();
app.Use(async (context, next) =>
{
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    context.Response.Headers["X-Frame-Options"] = "DENY";
    context.Response.Headers["Content-Security-Policy"] =
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'";
    context.Response.Headers.CacheControl = "no-store";
    await next(context);
});
app.UseDefaultFiles();
app.UseStaticFiles();
app.MapGet("/health", (RecoveryCoordinator recovery) => Results.Ok(new
{
    service = "sentinel", status = "ok", mode = recovery.Mode, checked_at = DateTimeOffset.UtcNow,
}));
app.MapGet("/healthz", (RecoveryCoordinator recovery) => Results.Ok(new
{
    service = "sentinel", status = "ok", mode = recovery.Mode, checked_at = DateTimeOffset.UtcNow,
}));
app.MapGet("/api/status", (PlatformMonitor monitor, RecoveryCoordinator recovery) =>
    Results.Ok(monitor.Snapshot() with { Mode = recovery.Mode, Recovery = recovery.Snapshot() }));
app.MapGet("/api/recovery", (RecoveryCoordinator recovery) => Results.Ok(recovery.Snapshot()));
var recoveryApi = app.MapGroup("/internal/recovery");
recoveryApi.AddEndpointFilter(async (context, next) =>
{
    var request = context.HttpContext.Request;
    var authentication = context.HttpContext.RequestServices.GetRequiredService<RecoveryAuthentication>();
    if (request.Headers.Authorization.Count != 1 || !authentication.Authorize(request.Headers.Authorization.ToString()))
        return Results.Unauthorized();
    return await next(context);
});
recoveryApi.MapGet("/requests/next", (RecoveryCoordinator recovery) => Results.Ok(new { request = recovery.Poll() }));
recoveryApi.MapPost("/inspections", (RecoveryInspection body, RecoveryCoordinator recovery) =>
    recovery.Inspect(body) ? Results.Ok(new { accepted = true }) : Results.BadRequest());
recoveryApi.MapPost("/requests/{request_id}/claim", (string request_id, RecoveryClaim body, RecoveryCoordinator recovery) =>
    recovery.Claim(request_id, body.ExecutorId) is { } claim ? Results.Ok(claim) : Results.Conflict());
recoveryApi.MapPost("/requests/{request_id}/result", (string request_id, RecoveryResult body, RecoveryCoordinator recovery) =>
    recovery.Complete(request_id, body) ? Results.Ok(new { accepted = true }) : Results.Conflict());
app.MapGet("/api/events", (PlatformMonitor monitor) => Results.Ok(new { events = monitor.Events() }));
app.MapGet("/metrics", (PlatformMonitor monitor) =>
    Results.Text(monitor.Metrics(), "text/plain; version=0.0.4; charset=utf-8"));
app.Run();

// 통합 테스트에서 WebApplicationFactory가 진입점을 찾을 수 있도록 공개합니다.
public partial class Program
{
}
