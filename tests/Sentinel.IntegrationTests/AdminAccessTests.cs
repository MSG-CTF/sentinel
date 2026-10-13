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
            { Enabled = true, Username = "admin", PasswordHash = hash, ReadinessToken = new string('r', 40) }));
        var before = Environment.GetEnvironmentVariable("SENTINEL_AUTH_FILE");
        Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE", file);
        try
        {
            await using var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(builder =>
                builder.ConfigureServices(services => services.RemoveAll<IHostedService>()));
            using var client = factory.CreateClient(new WebApplicationFactoryClientOptions
                { BaseAddress = new Uri("https://localhost"), AllowAutoRedirect = false });
            Assert.Equal(HttpStatusCode.Found, (await client.GetAsync("/")).StatusCode);
            foreach (var path in new[] { "/api/status", "/api/history", "/metrics", "/healthz", "/asset-access.json", "/dashboard.js" })
                Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync(path)).StatusCode);
            Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/login")).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/internal/readiness")).StatusCode);
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
            Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/api/status")).StatusCode);
            Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/")).StatusCode);
            // Anonymous CSRF tokens cannot be reused after the identity changes.
            Assert.Equal(HttpStatusCode.BadRequest, (await client.PostAsync("/auth/logout", null)).StatusCode);
            csrf = await client.GetFromJsonAsync<JsonElement>("/auth/csrf");
            client.DefaultRequestHeaders.Remove("X-CSRF-TOKEN");
            client.DefaultRequestHeaders.Add("X-CSRF-TOKEN", csrf.GetProperty("request_token").GetString());
            Assert.Equal(HttpStatusCode.OK, (await client.PostAsync("/auth/logout", null)).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/status")).StatusCode);
        }
        finally { Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE", before); Directory.Delete(temp, true); }
    }

    [Theory]
    [InlineData("plain-password")]
    [InlineData("pbkdf2-sha256$1$invalid$invalid")]
    [InlineData("pbkdf2-sha256$600000$bad$bad")]
    public void WeakOrMalformedHashIsRejected(string hash) => Assert.False(AdminAccess.ValidHash(hash));
}
