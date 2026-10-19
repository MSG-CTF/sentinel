using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using Sentinel.Api;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.IntegrationTests;

public sealed class ChallengeIngestTests
{
    [Theory]
    [InlineData("UP", "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED")]
    [InlineData("DOWN", "FUNCTION_UNAVAILABLE")]
    [InlineData("CHECKER_ERROR", "REDIRECT_BLOCKED")]
    public async Task OwnedReporterRequiresItsDedicatedTokenExactSnakeCaseBodyAndCannotReadAdminData(string status, string reason)
    {
        const string tokenName = "TEST_OWNED_INGEST";
        var root = Path.Combine(Path.GetTempPath(), "sentinel-owned-ingest-" + Guid.NewGuid()); Directory.CreateDirectory(root);
        var oldAuth = Environment.GetEnvironmentVariable("SENTINEL_AUTH_FILE");
        var oldToken = Environment.GetEnvironmentVariable(tokenName);
        var salt = RandomNumberGenerator.GetBytes(16);
        var hash = "pbkdf2-sha256$600000$" + Convert.ToBase64String(salt) + "$" + Convert.ToBase64String(
            Rfc2898DeriveBytes.Pbkdf2("fixture-admin-password", salt, 600000, HashAlgorithmName.SHA256, 32));
        var auth = Path.Combine(root, "auth.json");
        await File.WriteAllTextAsync(auth, JsonSerializer.Serialize(new AdminAccess
            { Enabled = true, Username = "admin", PasswordHash = hash, ReadinessToken = new string('r', 40) }));
        var token = new string('o', 48);
        Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE", auth); Environment.SetEnvironmentVariable(tokenName, token);
        var now = DateTimeOffset.UtcNow;
        var target = new PlatformTarget
        {
            TargetId = "owned-notebook", Name = "실습 기능 점검", Role = "CHALLENGE", Track = "TEST", Kind = "challenge_heartbeat",
            AuthTokenEnv = tokenName, ExpectedFields = new()
            {
                ["instance_id"] = Guid.NewGuid().ToString(), ["challenge_id"] = Guid.NewGuid().ToString(),
                ["release_id"] = Guid.NewGuid().ToString(), ["owner_team_id"] = Guid.NewGuid().ToString(),
                ["runtime_target_id"] = "owned-cluster", ["runtime_workload_id"] = "owned-cluster/namespace",
                ["profile"] = "web-notebook", ["source_revision"] = ChallengeHeartbeatStore.SourceRevision,
                ["expires_at"] = now.AddMinutes(15).ToString("O"),
            },
        };
        var f = target.ExpectedFields;
        var payload = new ChallengeHeartbeat(now, f["instance_id"], f["challenge_id"], f["release_id"], f["owner_team_id"],
            f["runtime_target_id"], f["runtime_workload_id"], f["profile"], f["source_revision"], "owned_deployment_test",
            status, reason, 25);
        var snake = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
        try
        {
            await using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureServices(services =>
            {
                services.RemoveAll<IHostedService>();
                services.PostConfigure<PlatformOptions>(options => options.Targets = [target]);
            }));
            using var client = factory.CreateClient(new() { BaseAddress = new("https://localhost"), AllowAutoRedirect = false });
            const string path = "/ingest/challenge/owned-notebook";
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync(path, payload, snake)).StatusCode);
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/status")).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/ingest/challenge/other", payload, snake)).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/ingest/heartbeat/owned-notebook", payload, snake)).StatusCode);
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync(path, payload)).StatusCode);
            var body = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(JsonSerializer.Serialize(payload, snake))!;
            body.Remove("owner_team_id");
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync(path, body)).StatusCode);
            body = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(JsonSerializer.Serialize(payload, snake))!;
            body["response_body"] = JsonSerializer.SerializeToElement("discard this");
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync(path, body)).StatusCode);
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync(path, payload with { ReleaseId = Guid.NewGuid().ToString() }, snake)).StatusCode);
            Assert.Equal(HttpStatusCode.OK, (await client.PostAsJsonAsync(path, payload, snake)).StatusCode);
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync(path, payload, snake)).StatusCode);
        }
        finally
        {
            Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE", oldAuth); Environment.SetEnvironmentVariable(tokenName, oldToken);
            Directory.Delete(root, true);
        }
    }
}
