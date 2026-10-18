using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using Sentinel.Api;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.IntegrationTests;

public sealed class ClusterIngestTests
{
    [Fact]
    public async Task IngestRequiresItsOwnTokenAndSnakeCaseIdentityWithoutOpeningAdminApis()
    {
        const string tokenName = "SENTINEL_TEST_CLUSTER_INGEST";
        var temp = Path.Combine(Path.GetTempPath(), "sentinel-cluster-api-" + Guid.NewGuid());
        Directory.CreateDirectory(temp);
        var before = Environment.GetEnvironmentVariable("SENTINEL_AUTH_FILE");
        var beforeToken = Environment.GetEnvironmentVariable(tokenName);
        var salt = RandomNumberGenerator.GetBytes(16);
        var file = Path.Combine(temp, "auth.json");
        var hash = "pbkdf2-sha256$600000$" + Convert.ToBase64String(salt) + "$" + Convert.ToBase64String(
            Rfc2898DeriveBytes.Pbkdf2("fixture-admin-password", salt, 600000, HashAlgorithmName.SHA256, 32));
        await File.WriteAllTextAsync(file, JsonSerializer.Serialize(new AdminAccess
            { Enabled = true, Username = "admin", PasswordHash = hash, ReadinessToken = new string('r', 40) }));
        var token = new string('c', 48);
        Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE", file);
        Environment.SetEnvironmentVariable(tokenName, token);
        try
        {
            await using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder => builder.ConfigureServices(services =>
            {
                services.RemoveAll<IHostedService>();
                services.PostConfigure<PlatformOptions>(options => options.Targets = [new()
                {
                    TargetId = "cluster-one", Name = "Cluster One", Role = "K3S", Track = "RUNTIME", Kind = "cluster_heartbeat",
                    AuthTokenEnv = tokenName, ExpectedFields = new() { ["cluster_target_id"] = "cluster-a" },
                }]);
            }));
            using var client = factory.CreateClient(new() { BaseAddress = new("https://localhost"), AllowAutoRedirect = false });
            var payload = new ClusterHeartbeat(DateTimeOffset.UtcNow, "cluster-a", "ok", 1, 1, 1, 0, 0, 0, 0);
            var snake = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/ingest/cluster/cluster-one", payload, snake)).StatusCode);
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/status")).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/ingest/cluster/other", payload, snake)).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/ingest/heartbeat/cluster-one",
                new ServiceHeartbeat(DateTimeOffset.UtcNow, "active", 0, 10, 20, .1, "unverified"), snake)).StatusCode);
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync("/ingest/cluster/cluster-one", payload)).StatusCode);
            var invalid = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(JsonSerializer.Serialize(payload, snake))!;
            invalid.Remove("unknown_condition_node_count");
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync("/ingest/cluster/cluster-one", invalid)).StatusCode);
            invalid = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(JsonSerializer.Serialize(payload, snake))!;
            invalid["extra_credential"] = JsonSerializer.SerializeToElement("fixture-only");
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync("/ingest/cluster/cluster-one", invalid)).StatusCode);
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync("/ingest/cluster/cluster-one", payload with { ClusterTargetId = "other" }, snake)).StatusCode);
            var accepted = await client.PostAsJsonAsync("/ingest/cluster/cluster-one", payload, snake);
            Assert.Equal(HttpStatusCode.OK, accepted.StatusCode);
            Assert.True((await accepted.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("accepted").GetBoolean());
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync("/ingest/cluster/cluster-one", payload, snake)).StatusCode);
        }
        finally
        {
            Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE", before);
            Environment.SetEnvironmentVariable(tokenName, beforeToken);
            Directory.Delete(temp, true);
        }
    }
}
