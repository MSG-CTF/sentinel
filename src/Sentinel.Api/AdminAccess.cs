using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Threading.RateLimiting;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.RateLimiting;

namespace Sentinel.Api;

public sealed class AdminAccess
{
    public bool Enabled { get; init; }
    public string Username { get; init; } = "";
    public string PasswordHash { get; init; } = "";
    public string ReadinessToken { get; init; } = "";
    public string Stamp => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Username + PasswordHash)));

    public static AdminAccess Load(IConfiguration configuration)
    {
        var file = Environment.GetEnvironmentVariable("SENTINEL_AUTH_FILE");
        var access = string.IsNullOrWhiteSpace(file) ? new AdminAccess() :
            System.Text.Json.JsonSerializer.Deserialize<AdminAccess>(File.ReadAllText(file))
                ?? throw new InvalidOperationException("Invalid administrator configuration");
        if (configuration["platform_monitor:profile"] == "aws" && !access.Enabled)
            throw new InvalidOperationException("AWS requires administrator authentication");
        if (access.Enabled && (access.Username.Length is < 3 or > 32 || !ValidHash(access.PasswordHash)
            || access.ReadinessToken.Length < 32))
            throw new InvalidOperationException("Invalid administrator configuration");
        return access;
    }

    public static bool ValidHash(string hash)
    {
        var parts = hash.Split('$');
        if (parts.Length != 4 || parts[0] != "pbkdf2-sha256" || parts[1] != "600000") return false;
        try { return Convert.FromBase64String(parts[2]).Length == 16 && Convert.FromBase64String(parts[3]).Length == 32; }
        catch (FormatException) { return false; }
    }

    public bool Verify(string? username, string? password)
    {
        if (!Enabled || password is null || password.Length is < 1 or > 256) return false;
        var parts = PasswordHash.Split('$');
        var actual = Rfc2898DeriveBytes.Pbkdf2(password, Convert.FromBase64String(parts[2]), 600000, HashAlgorithmName.SHA256, 32);
        var valid = CryptographicOperations.FixedTimeEquals(actual, Convert.FromBase64String(parts[3]));
        return valid & string.Equals(username, Username, StringComparison.Ordinal);
    }

    public static bool TokenMatches(string supplied, string expected) => expected.Length >= 32 &&
        CryptographicOperations.FixedTimeEquals(SHA256.HashData(Encoding.UTF8.GetBytes(supplied)),
            SHA256.HashData(Encoding.UTF8.GetBytes(expected)));
}

public sealed record LoginRequest(string? Username, string? Password);

public static class AdminAccessRegistration
{
    private const string Scheme = "sentinel-admin";
    public static void AddAdminAccess(this IServiceCollection services, AdminAccess access)
    {
        services.AddSingleton(access);
        var protection = services.AddDataProtection().SetApplicationName("MSG.Sentinel.Admin");
        if (access.Enabled)
            protection.PersistKeysToFileSystem(new DirectoryInfo(Path.Combine("data", "auth-keys")));
        services.AddAntiforgery(options =>
        {
            options.HeaderName = "X-CSRF-TOKEN";
            options.Cookie.Name = "__Host-sentinel-csrf";
            options.Cookie.SecurePolicy = CookieSecurePolicy.Always;
            options.Cookie.SameSite = SameSiteMode.Strict;
            options.Cookie.HttpOnly = true;
        });
        services.AddAuthentication(Scheme).AddCookie(Scheme, options =>
        {
            options.Cookie.Name = "__Host-sentinel-session";
            options.Cookie.HttpOnly = true;
            options.Cookie.SecurePolicy = CookieSecurePolicy.Always;
            options.Cookie.SameSite = SameSiteMode.Strict;
            options.ExpireTimeSpan = TimeSpan.FromHours(8);
            options.SlidingExpiration = false;
            options.Events.OnValidatePrincipal = context =>
            {
                if (context.Principal?.FindFirstValue("stamp") != access.Stamp) context.RejectPrincipal();
                return Task.CompletedTask;
            };
        });
        services.AddRateLimiter(options =>
        {
            options.RejectionStatusCode = 429;
            options.AddFixedWindowLimiter("login", limiter =>
            {
                limiter.PermitLimit = 10;
                limiter.Window = TimeSpan.FromMinutes(1);
                limiter.QueueLimit = 0;
            });
        });
    }

    public static void UseAdminAccess(this WebApplication app, AdminAccess access)
    {
        // The production listener is loopback-only behind the HTTPS proxy.
        if (access.Enabled) app.Use((context, next) => { context.Request.Scheme = "https"; return next(context); });
        app.UseAuthentication();
        app.UseRateLimiter();
        app.Use(async (context, next) =>
        {
            var path = context.Request.Path;
            var publicFile = path.Value is "/login" or "/login.html" or "/login.css" or "/login.js" or "/sentinel-core.js" or "/favicon.svg";
            var ownAuthentication = path.Value is "/auth/csrf" or "/auth/login" or "/internal/readiness"
                || path.StartsWithSegments("/internal/recovery") || path.StartsWithSegments("/ingest/heartbeat");
            if (access.Enabled && !publicFile && !ownAuthentication && context.User.Identity?.IsAuthenticated != true)
            {
                if (path.Value is "/" or "/index.html") context.Response.Redirect("/login");
                else context.Response.StatusCode = 401;
                return;
            }
            if (path == "/login") context.Request.Path = "/login.html";
            await next(context);
        });
    }

    public static void MapAdminAccess(this WebApplication app)
    {
        app.MapGet("/auth/csrf", (HttpContext context, IAntiforgery antiforgery) =>
            Results.Ok(new { request_token = antiforgery.GetAndStoreTokens(context).RequestToken }));
        app.MapPost("/auth/login", async (LoginRequest body, HttpContext context, IAntiforgery antiforgery, AdminAccess access) =>
        {
            try { await antiforgery.ValidateRequestAsync(context); }
            catch (AntiforgeryValidationException) { return Results.BadRequest(new { code = "INVALID_REQUEST" }); }
            if (!access.Verify(body.Username, body.Password)) return Results.Json(new { code = "INVALID_CREDENTIALS" }, statusCode: 401);
            var principal = new ClaimsPrincipal(new ClaimsIdentity([
                new Claim(ClaimTypes.Name, access.Username), new Claim("stamp", access.Stamp)
            ], Scheme));
            await context.SignInAsync(Scheme, principal, new AuthenticationProperties { IsPersistent = false });
            return Results.Ok(new { authenticated = true });
        }).RequireRateLimiting("login");
        app.MapGet("/auth/session", (HttpContext context, AdminAccess access) =>
            Results.Ok(new { username = context.User.Identity?.Name, authentication_enabled = access.Enabled }));
        app.MapPost("/auth/logout", async (HttpContext context, IAntiforgery antiforgery) =>
        {
            try { await antiforgery.ValidateRequestAsync(context); }
            catch (AntiforgeryValidationException) { return Results.BadRequest(); }
            await context.SignOutAsync(Scheme);
            return Results.Ok(new { authenticated = false });
        });
    }
}
