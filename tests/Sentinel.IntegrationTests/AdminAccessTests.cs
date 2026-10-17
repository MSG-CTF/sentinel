using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using Sentinel.Api;
using Xunit;

[assembly: CollectionBehavior(DisableTestParallelization = true)]

namespace Sentinel.IntegrationTests;

public sealed class AdminAccessTests
{
    private const string Password = "fixture-only-long-admin-password";
    [Fact]
    public async Task SessionFlowRequiresPasswordAndCsrfAndClearsCookieOnLogout()
    {
        var temp = Path.Combine(Path.GetTempPath(), "sentinel-auth-" + Guid.NewGuid());
        Directory.CreateDirectory(temp);
        var salt = RandomNumberGenerator.GetBytes(16);
        var hash = "pbkdf2-sha256$600000$" + Convert.ToBase64String(salt) + "$" + Convert.ToBase64String(
            Rfc2898DeriveBytes.Pbkdf2(Password, salt, 600000, HashAlgorithmName.SHA256, 32));
        var file = Path.Combine(temp, "auth.json");
        await File.WriteAllTextAsync(file, JsonSerializer.Serialize(new AdminAccess
            { Enabled = true, Username = "admin", PasswordHash = hash, ReadinessToken = new string('r', 40), MetricsToken = new string('m', 40) }));
        var before = Environment.GetEnvironmentVariable("SENTINEL_AUTH_FILE");
        Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE", file);
        try
        {
            await using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder =>
                builder.ConfigureServices(services => services.RemoveAll<IHostedService>()));
            using var client = factory.CreateClient(new WebApplicationFactoryClientOptions
                { BaseAddress = new Uri("https://localhost"), AllowAutoRedirect = false });
            Assert.Equal(HttpStatusCode.Found, (await client.GetAsync("/")).StatusCode);
            foreach (var path in new[] { "/api/status", "/api/history", "/metrics", "/healthz", "/asset-access.json", "/dashboard.js", "/api/alerts", "/api/operations/assets/discord-bot", "/asset-operations.js" })
            {
                var protectedResponse = await client.GetAsync(path);
                Assert.Equal(HttpStatusCode.Unauthorized, protectedResponse.StatusCode);
                Assert.True(protectedResponse.Headers.CacheControl?.NoStore);
            }
            var loginPage = await client.GetAsync("/login");
            Assert.Equal(HttpStatusCode.OK, loginPage.StatusCode);
            Assert.True(loginPage.Headers.CacheControl?.NoStore);
            Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/sentinel-core.js")).StatusCode);
            Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/mechanism.css")).StatusCode);
            foreach (var path in new[] { "/sentinel-scene.js", "/vendor/three/three.module.js", "/vendor/three/three.core.js", "/vendor/three/HDRLoader.js", "/vendor/three/RectAreaLightUniformsLib.js", "/vendor/three/RectAreaLightTexturesLib.js" })
            {
                var script = await client.GetAsync(path);
                Assert.Equal(HttpStatusCode.OK, script.StatusCode);
                Assert.Contains("javascript", script.Content.Headers.ContentType?.MediaType ?? "");
                Assert.True(script.Headers.CacheControl?.Public);
                Assert.True(script.Headers.CacheControl?.MustRevalidate);
                Assert.Equal(TimeSpan.Zero, script.Headers.CacheControl?.MaxAge);
                Assert.NotNull(script.Headers.ETag);
                using var conditional = new HttpRequestMessage(HttpMethod.Get, path);
                conditional.Headers.IfNoneMatch.Add(script.Headers.ETag!);
                var reused = await client.SendAsync(conditional);
                Assert.Equal(HttpStatusCode.NotModified, reused.StatusCode);
                Assert.Empty(await reused.Content.ReadAsByteArrayAsync());
                Assert.True(reused.Headers.CacheControl?.MustRevalidate);
            }
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/vendor/unlisted.js")).StatusCode);
            var environment = await client.GetAsync("/assets/studio-small-09.hdr");
            Assert.Equal(HttpStatusCode.OK, environment.StatusCode);
            Assert.Equal("image/vnd.radiance", environment.Content.Headers.ContentType?.MediaType);
            Assert.True(environment.Headers.CacheControl?.Public);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/assets/unlisted.hdr")).StatusCode);
            var visual = await client.GetAsync("/assets/sentinel-optic-poster.jpg");
            Assert.Equal(HttpStatusCode.OK, visual.StatusCode);
            Assert.Equal("image/jpeg", visual.Content.Headers.ContentType?.MediaType);
            Assert.True(visual.Headers.CacheControl?.Public);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/internal/readiness")).StatusCode);
            client.DefaultRequestHeaders.Add("X-Sentinel-User", "forged-admin");
            var deniedGrafana = await client.GetAsync("/internal/grafana-auth");
            Assert.Equal(HttpStatusCode.Found, deniedGrafana.StatusCode);
            Assert.Equal("/login", deniedGrafana.Headers.Location?.ToString());
            Assert.False(deniedGrafana.Headers.Contains("X-Sentinel-User"));
            foreach (var token in new[] { "", "Bearer " + new string('r', 40), "Bearer invalid" })
            {
                using var request = new HttpRequestMessage(HttpMethod.Get, "/internal/metrics");
                request.Headers.TryAddWithoutValidation("Authorization", token);
                Assert.Equal(HttpStatusCode.Unauthorized, (await client.SendAsync(request)).StatusCode);
            }
            using (var request = new HttpRequestMessage(HttpMethod.Get, "/internal/metrics"))
            {
                request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", new string('m', 40));
                var metrics = await client.SendAsync(request);
                Assert.Equal(HttpStatusCode.OK, metrics.StatusCode);
                Assert.Contains("sentinel_", await metrics.Content.ReadAsStringAsync());
            }
            var credentials = new { username = "admin", password = Password };
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync("/auth/login", credentials)).StatusCode);
            var csrf = await client.GetFromJsonAsync<JsonElement>("/auth/csrf");
            client.DefaultRequestHeaders.Add("X-CSRF-TOKEN", csrf.GetProperty("request_token").GetString());
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsJsonAsync("/auth/login", new { username = "admin", password = "wrong" })).StatusCode);
            var login = await client.PostAsJsonAsync("/auth/login", credentials);
            Assert.Equal(HttpStatusCode.OK, login.StatusCode);
            var cookie = Assert.Single(login.Headers.GetValues("Set-Cookie"), value => value.StartsWith("__Host-sentinel-session="));
            Assert.Contains("secure", cookie.ToLowerInvariant());
            Assert.Contains("httponly", cookie.ToLowerInvariant());
            Assert.Contains("samesite=strict", cookie.ToLowerInvariant());
            var authenticatedStatus = await client.GetAsync("/api/status");
            Assert.Equal(HttpStatusCode.OK, authenticatedStatus.StatusCode);
            Assert.True(authenticatedStatus.Headers.CacheControl?.NoStore);
            var grafana = await client.GetAsync("/internal/grafana-auth");
            Assert.Equal(HttpStatusCode.OK, grafana.StatusCode);
            Assert.Equal("admin", Assert.Single(grafana.Headers.GetValues("X-Sentinel-User")));
            Assert.True(grafana.Headers.CacheControl?.NoStore);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/internal/metrics")).StatusCode);
            var dashboard = await client.GetAsync("/");
            Assert.Equal(HttpStatusCode.OK, dashboard.StatusCode);
            Assert.True(dashboard.Headers.CacheControl?.NoStore);
            // Anonymous CSRF tokens cannot be reused after the identity changes.
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsync("/auth/logout", null)).StatusCode);
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync("/api/alerts/test", new { })).StatusCode);
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsJsonAsync("/api/operations/assets/discord-bot/actions",
                new { action="start", request_id=Guid.NewGuid().ToString(), confirm_asset_id="discord-bot" })).StatusCode);
            csrf = await client.GetFromJsonAsync<JsonElement>("/auth/csrf");
            client.DefaultRequestHeaders.Remove("X-CSRF-TOKEN");
            client.DefaultRequestHeaders.Add("X-CSRF-TOKEN", csrf.GetProperty("request_token").GetString());
            Assert.Equal(HttpStatusCode.Conflict, (await client.PostAsJsonAsync("/api/operations/assets/discord-bot/actions",
                new { action="start", request_id=Guid.NewGuid().ToString(), confirm_asset_id="discord-bot" })).StatusCode);
            Assert.Equal(HttpStatusCode.Conflict, (await client.PostAsJsonAsync("/api/alerts/test", new { })).StatusCode);
            Assert.Equal(HttpStatusCode.OK, (await client.PostAsync("/auth/logout", null)).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/status")).StatusCode);
            Assert.Equal(HttpStatusCode.Found, (await client.GetAsync("/internal/grafana-auth")).StatusCode);
        }
        finally { Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE", before); Directory.Delete(temp, true); }
    }

    [Theory]
    [InlineData("plain-password")]
    [InlineData("pbkdf2-sha256$1$invalid$invalid")]
    [InlineData("pbkdf2-sha256$600000$bad$bad")]
    public void WeakOrMalformedHashIsRejected(string hash) => Assert.False(AdminAccess.ValidHash(hash));
}
