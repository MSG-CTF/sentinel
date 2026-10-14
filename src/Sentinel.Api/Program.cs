using System.Text.Json;
using System.Text;
using Sentinel.Api;
using Prometheus;
using Sentinel.Monitor.Django;
using Sentinel.Monitor.Platform;
using Sentinel.Monitor.Recovery;
using Sentinel.Monitor.Instances;
using Microsoft.AspNetCore.StaticFiles;

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.ConfigureKestrel(options => options.Limits.MaxRequestBodySize = 4096);
var profileFile = Environment.GetEnvironmentVariable("SENTINEL_PROFILE_FILE")
    ?? Path.Combine(builder.Environment.ContentRootPath, "profiles", "local.json");
builder.Configuration.AddJsonFile(profileFile, optional: false, reloadOnChange: false);
builder.Configuration.AddEnvironmentVariables();
var adminAccess = AdminAccess.Load(builder.Configuration);
builder.Services.AddAdminAccess(adminAccess);
builder.Services.AddSingleton(Metrics.NewCustomRegistry());
builder.Services.AddSingleton<PrometheusDjangoHealthMetrics>();
builder.Services.AddSingleton<IDjangoHealthMetrics>(services => services.GetRequiredService<PrometheusDjangoHealthMetrics>());
builder.Services.ConfigureHttpJsonOptions(options =>
{
    options.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
    options.SerializerOptions.PropertyNameCaseInsensitive = false;
    options.SerializerOptions.Converters.Add(new UtcTimestampConverter());
});
builder.Services.AddPlatformMonitor(builder.Configuration);
builder.Services.AddPlatformRecovery(builder.Configuration);
builder.Services.AddInstanceInventory(builder.Configuration);

var app = builder.Build();
app.Use(async (context, next) =>
{
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    context.Response.Headers["X-Frame-Options"] = "DENY";
    context.Response.Headers["Content-Security-Policy"] =
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'";
    context.Response.Headers["Referrer-Policy"] = "no-referrer";
    context.Response.Headers.CacheControl = "no-store";
    await next(context);
});
app.UseAdminAccess(adminAccess);
app.UseDefaultFiles();
var staticTypes = new FileExtensionContentTypeProvider();
staticTypes.Mappings[".hdr"] = "image/vnd.radiance";
app.UseStaticFiles(new StaticFileOptions
{
    ContentTypeProvider = staticTypes,
    OnPrepareResponse = context =>
    {
        // Public artwork/code can reuse validated bytes. Revalidate on every
        // visit because these paths are not content-hashed; HTML and private
        // responses retain no-store, including authentication failures.
        if (AdminAccessRegistration.IsPublicStaticAsset(context.Context.Request.Path)
            && context.Context.Response.StatusCode is 200 or 304)
            context.Context.Response.Headers.CacheControl = "public, max-age=0, must-revalidate";
    },
});
app.MapAdminAccess();
app.MapGet("/internal/readiness", (HttpContext context, PlatformMonitor monitor, RecoveryCoordinator recovery) =>
{
    if (!AdminAccess.TokenMatches(context.Request.Headers.Authorization.ToString(), "Bearer " + adminAccess.ReadinessToken))
        return Results.Unauthorized();
    var snapshot = monitor.Snapshot();
    return Results.Ok(new { status = "ok", mode = recovery.Mode, snapshot.Profile, snapshot.UpdatedAt, snapshot.EventStorageHealthy });
});
app.MapPost("/ingest/heartbeat/{target_id}", (string target_id, ServiceHeartbeat body, HttpContext context, HeartbeatStore store) =>
{
    if (!store.Authorize(target_id, context.Request.Headers.Authorization.ToString())) return Results.Unauthorized();
    return store.Accept(target_id, body) ? Results.Ok(new { accepted = true }) : Results.BadRequest();
});
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
app.MapGet("/api/history", (PlatformMonitor monitor) => Results.Ok(new { samples = monitor.History() }));
app.MapGet("/api/recovery", (RecoveryCoordinator recovery) => Results.Ok(recovery.Snapshot()));
app.MapGet("/api/instances", (InstanceInventory inventory) => Results.Ok(inventory.Snapshot()));
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
app.MapGet("/internal/metrics", async (HttpContext context, AdminAccess access, PlatformMonitor monitor,
    PrometheusDjangoHealthMetrics django, CollectorRegistry registry) =>
{
    var supplied = context.Request.Headers.Authorization.ToString();
    if (!supplied.StartsWith("Bearer ", StringComparison.Ordinal) ||
        !AdminAccess.TokenMatches(supplied[7..], access.MetricsToken)) return Results.Unauthorized();
    return await ExportMetrics(monitor, django, registry);
});
app.MapGet("/metrics", ExportMetrics);
app.Run();

static async Task<IResult> ExportMetrics(PlatformMonitor monitor, PrometheusDjangoHealthMetrics django, CollectorRegistry registry)
{
    foreach (var target in monitor.Snapshot().Targets.Where(target => target.Kind == "backend_healthz"))
        django.RecordPlatform(target);
    using var stream = new MemoryStream();
    await registry.CollectAndExportAsTextAsync(stream);
    return Results.Text(monitor.Metrics() + Encoding.UTF8.GetString(stream.ToArray()), "text/plain; version=0.0.4; charset=utf-8");
}

// 통합 테스트에서 WebApplicationFactory가 진입점을 찾을 수 있도록 공개합니다.
public partial class Program
{
}
