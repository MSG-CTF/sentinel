using System.Globalization;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;

namespace Sentinel.Monitor.Platform;

public sealed class DiscordAlertOptions
{
    public bool Enabled { get; set; }
    [ConfigurationKeyName("state_file")]
    public string StateFile { get; set; } = "data/discord-alerts.json";
    [ConfigurationKeyName("dashboard_url")]
    public string DashboardUrl { get; set; } = "https://sla.msgctf.kr/";
    public static bool ValidWebhook(string? value) => value is not null &&
        Regex.IsMatch(value, @"\Ahttps://discord\.com/api/(?:v10/)?webhooks/[0-9]{16,22}/[A-Za-z0-9_-]{30,200}\z");
}

public sealed record RiskNotification(string EventId, string TargetId, string Name, string PreviousLevel,
    string Level, string ReasonCode, DateTimeOffset CreatedAt, IReadOnlyList<RiskSignal> Signals, bool Test = false,
    int? HttpStatusCode = null);
public sealed class AlertTargetState
{
    public string Level { get; set; } = "NORMAL";
    public DateTimeOffset? LastAt { get; set; }
    public int NormalSamples { get; set; }
    public DateTimeOffset? LatencyWarningSince { get; set; }
    public int LatencyWarningSamples { get; set; }
}
public sealed class AlertJournal
{
    public Dictionary<string, AlertTargetState> Targets { get; set; } = [];
    public List<RiskNotification> Pending { get; set; } = [];
    public List<RiskNotification> Delivered { get; set; } = [];
    public DateTimeOffset? LastSentAt { get; set; }
    public DateTimeOffset? LastTestAt { get; set; }
    public DateTimeOffset? RetryAt { get; set; }
    public string? FailureCode { get; set; }
    public int Attempts { get; set; }
}

public sealed class DiscordAlerts
{
    public const int LatencyWarningSeconds = 60;
    public const int LatencyWarningSamples = 3;
    private readonly DiscordAlertOptions _options;
    private readonly TimeProvider _clock;
    private readonly HttpClient _http;
    private readonly string? _webhook;
    private readonly object _gate = new();
    private readonly SemaphoreSlim _delivery = new(1, 1);
    private AlertJournal _journal = new();
    private bool _storageHealthy = true;
    public bool Enabled { get; }

    public DiscordAlerts(IOptions<DiscordAlertOptions> options, HttpClient http, TimeProvider? clock = null)
    {
        _options = options.Value; _http = http; _clock = clock ?? TimeProvider.System;
        _webhook = Environment.GetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL");
        Enabled = _options.Enabled && Environment.GetEnvironmentVariable("SENTINEL_CANDIDATE") != "true";
        if (!Enabled) return;
        if (!DiscordAlertOptions.ValidWebhook(_webhook)) throw new ArgumentException("Invalid Discord notification configuration");
        if (!Uri.TryCreate(_options.DashboardUrl, UriKind.Absolute, out var url) || url.Scheme != "https" || url.UserInfo.Length != 0)
            throw new ArgumentException("Invalid notification dashboard URL");
        try
        {
            if (!File.Exists(_options.StateFile)) return;
            if (new FileInfo(_options.StateFile).Length > 2_000_000) throw new JsonException();
            _journal = JsonSerializer.Deserialize<AlertJournal>(File.ReadAllText(_options.StateFile), PlatformJson.Options) ?? throw new JsonException();
            if (_journal.Targets.Count > 32 || _journal.Pending.Count > 200 || _journal.Delivered.Count > 50 ||
                _journal.Targets.Values.Any(s => s.Level is not ("NORMAL" or "WARNING" or "CRITICAL")) ||
                _journal.Pending.Any(n => !Guid.TryParse(n.EventId, out _) || n.Level is not ("NORMAL" or "WARNING" or "CRITICAL"))) throw new JsonException();
            foreach (var state in _journal.Targets.Values)
            {
                state.NormalSamples = 0;
                ResetLatencyWarning(state);
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException)
        { _storageHealthy = false; }
    }

    public object Snapshot()
    {
        lock (_gate) return new { enabled = Enabled, storage_healthy = _storageHealthy,
            pending_count = _journal.Pending.Count, last_sent_at = _journal.LastSentAt,
            retry_at = _journal.RetryAt, failure_code = _journal.FailureCode,
            latency_warning_seconds = LatencyWarningSeconds, latency_warning_samples = LatencyWarningSamples,
            recent = _journal.Delivered.AsEnumerable().Reverse().Take(10).ToArray() };
    }

    public void Observe(PlatformSnapshot snapshot)
    {
        lock (_gate)
        {
            if (!Enabled || !_storageHealthy || snapshot.UpdatedAt is null) return;
            var changed = false;
            foreach (var item in snapshot.Targets)
            {
                var level = item.Risk?.Level ?? RiskEvaluator.BaseLevel(item.Status);
                var state = _journal.Targets.GetValueOrDefault(item.TargetId);
                // Unknown evidence neither resolves an incident nor counts as a healthy sample.
                if (!item.Enabled || level is not ("NORMAL" or "WARNING" or "CRITICAL") ||
                    item.CheckedAt is null || item.CheckedAt > _clock.GetUtcNow().AddSeconds(30) ||
                    _clock.GetUtcNow() - item.CheckedAt > TimeSpan.FromSeconds(item.Kind == "service_heartbeat" ? 150 : snapshot.StaleAfterSeconds))
                {
                    if (state is not null && (state.NormalSamples > 0 || state.LatencyWarningSince is not null))
                    { state.NormalSamples = 0; ResetLatencyWarning(state); changed = true; }
                    continue;
                }
                if (state is null) _journal.Targets[item.TargetId] = state = new();
                if (state.LastAt >= item.CheckedAt) continue;
                var previousAt = state.LastAt;
                state.LastAt = item.CheckedAt; changed = true;
                if (level == "WARNING" && DeferLatencyWarning(item, state, previousAt, snapshot.StaleAfterSeconds))
                { state.NormalSamples = 0; continue; }
                ResetLatencyWarning(state);
                // Collector hysteresis resets at restart; an open alert still needs clear margins.
                if (level == "NORMAL" && state.Level != "NORMAL" && item.Risk?.Signals.Any(s => s.Value >= s.ClearBelow) == true)
                { state.NormalSamples = 0; continue; }
                state.NormalSamples = level == "NORMAL" ? Math.Min(2, state.NormalSamples + 1) : 0;
                if (level == state.Level || level == "NORMAL" && state.NormalSamples < 2) continue;
                if (_journal.Pending.Count >= 200) { _storageHealthy = false; return; }
                _journal.Pending.Add(new(Guid.NewGuid().ToString(), item.TargetId, item.Name, state.Level,
                    level, item.ReasonCode, _clock.GetUtcNow(), item.Risk?.Signals ?? [], HttpStatusCode: item.HttpStatusCode));
                state.Level = level;
            }
            if (changed) Persist();
        }
    }

    private static void ResetLatencyWarning(AlertTargetState state)
    {
        state.LatencyWarningSince = null;
        state.LatencyWarningSamples = 0;
    }

    private static bool DeferLatencyWarning(ProbeObservation item, AlertTargetState state,
        DateTimeOffset? previousAt, int staleAfterSeconds)
    {
        // Only a new, latency-only warning on a healthy service is delayed.
        // Errors, resource pressure and escalation/relief of an open incident stay immediate.
        if (state.Level != "NORMAL" || item.Status != "HEALTHY" || item.Risk is null ||
            item.Risk.Signals.Any(s => s.Metric != "latency_ms" && s.Level != "NORMAL")) return false;
        var latency = item.Risk.Signals.FirstOrDefault(s => s.Metric == "latency_ms" && s.Level == "WARNING");
        if (latency is null) return false;
        // Collector recovery hysteresis can retain yellow after the actual latency fell.
        if (latency.Recovering || latency.Value < latency.Warning)
        { ResetLatencyWarning(state); return true; }
        var at = item.CheckedAt!.Value;
        if (state.LatencyWarningSince is null || previousAt is null ||
            at - previousAt.Value > TimeSpan.FromSeconds(staleAfterSeconds))
        { state.LatencyWarningSince = at; state.LatencyWarningSamples = 0; }
        state.LatencyWarningSamples = Math.Min(LatencyWarningSamples, state.LatencyWarningSamples + 1);
        return state.LatencyWarningSamples < LatencyWarningSamples ||
            at - state.LatencyWarningSince.Value < TimeSpan.FromSeconds(LatencyWarningSeconds);
    }

    public bool QueueTest()
    {
        lock (_gate)
        {
            var now = _clock.GetUtcNow();
            if (!Enabled || !_storageHealthy || _journal.Pending.Count >= 200 ||
                _journal.LastTestAt is { } last && now - last < TimeSpan.FromMinutes(1)) return false;
            _journal.LastTestAt = now;
            _journal.Pending.Add(new(Guid.NewGuid().ToString(), "sentinel", "Sentinel 알림 연결 시험", "NORMAL",
                "NORMAL", "NOTIFICATION_TEST", now, [], true));
            return Persist();
        }
    }

    public static object Payload(RiskNotification item, string dashboardUrl)
    {
        var title = item.Test ? "Sentinel · 디스코드 알림 연결 완료" :
            item.Level == "CRITICAL" ? "위험 · " + item.Name : item.Level == "WARNING" ?
                (item.PreviousLevel == "CRITICAL" ? "위험 완화 · 주의 유지 · " : "주의 · ") + item.Name : "복구 확인 · " + item.Name;
        var names = new Dictionary<string, string> { ["cpu_used_percent"] = "CPU", ["memory_used_percent"] = "메모리",
            ["disk_used_percent"] = "디스크", ["latency_ms"] = "응답 시간" };
        var values = item.Signals.Select(s => $"{names.GetValueOrDefault(s.Metric, s.Metric)} {s.Value.ToString("0.#", CultureInfo.InvariantCulture)}{s.Unit} · 주의 ≥{s.Warning} / 위험 ≥{s.Critical}").ToArray();
        var description = item.Test ? "연결 시험 메시지입니다. 실제 장애 알림이 아닙니다. 주의·위험·정상 복귀를 이 채널로 전달합니다." :
            item.Level == "NORMAL" ? "새 정상 측정 2회가 확인되었습니다." : item.PreviousLevel == "CRITICAL" && item.Level == "WARNING" ?
                "위험 단계는 해제됐지만 주의 기준이 남아 있습니다." : "자산 상태나 수치가 경고 기준에 도달했습니다.";
        var reason = item.ReasonCode switch
        {
            "HTTP_RATE_LIMITED" => "요청 제한 응답 · 서버 과부하 여부는 추가 확인 필요",
            "HTTP_UNAVAILABLE" => "서비스 이용 불가 응답 · DB·Redis 상태는 확인되지 않음",
            "HTTP_ERROR" => "예상한 HTTP 상태와 다른 응답",
            "TIMEOUT" => "점검 제한 시간 안에 응답하지 않음",
            "CONNECTION_FAILED" => "서비스 연결 실패 · 호스트 또는 네트워크 확인 필요",
            "RESPONSE_READ_FAILED" => "응답을 끝까지 읽지 못함",
            _ => item.ReasonCode,
        };
        if (item.HttpStatusCode is { } code) reason = $"HTTP {code} · {reason}";
        return new { username = "Sentinel", allowed_mentions = new { parse = Array.Empty<string>() },
            embeds = new[] { new { title, description, url = dashboardUrl,
                color = item.Level == "CRITICAL" ? 0xEF6666 : item.Level == "WARNING" ? 0xE8BD5A : 0x70BA9E,
                timestamp = item.CreatedAt, fields = new[] {
                    new { name = "대상", value = item.TargetId, inline = true },
                    new { name = "상태", value = item.Test ? "연결 시험" : item.PreviousLevel + " → " + item.Level, inline = true },
                    new { name = "감지 근거", value = reason, inline = false },
                    new { name = "측정 근거", value = values.Length == 0 ? item.ReasonCode : string.Join("\n", values), inline = false } },
                footer = new { text = "Sentinel · " + item.EventId } } } };
    }

    public async Task DeliverAsync(CancellationToken cancellationToken = default)
    {
        if (!await _delivery.WaitAsync(0, cancellationToken)) return;
        try
        {
            RiskNotification? next;
            lock (_gate)
            {
                if (!Enabled || !_storageHealthy || _journal.RetryAt > _clock.GetUtcNow()) return;
                next = _journal.Pending.FirstOrDefault();
            }
            if (next is null) return;
            string? failure = null; TimeSpan? retry = null;
            try
            {
                using var response = await _http.PostAsJsonAsync(_webhook + "?wait=true", Payload(next, _options.DashboardUrl), cancellationToken);
                if (!response.IsSuccessStatusCode)
                {
                    failure = "HTTP_" + (int)response.StatusCode;
                    if ((int)response.StatusCode == 429)
                    {
                        retry = response.Headers.RetryAfter?.Delta;
                        if (retry is null)
                        {
                            using var body = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);
                            if (body.RootElement.TryGetProperty("retry_after", out var delay) && delay.TryGetDouble(out var seconds) && double.IsFinite(seconds))
                                retry = TimeSpan.FromSeconds(Math.Clamp(seconds, 1, 3600));
                        }
                    }
                }
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { return; }
            catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException or IOException)
            { failure = "DELIVERY_UNCONFIRMED"; }
            lock (_gate)
            {
                if (failure is null)
                {
                    _journal.Pending.RemoveAll(n => n.EventId == next.EventId);
                    _journal.Delivered.Add(next); while (_journal.Delivered.Count > 50) _journal.Delivered.RemoveAt(0);
                    _journal.LastSentAt = _clock.GetUtcNow(); _journal.Attempts = 0; _journal.RetryAt = null;
                }
                else
                {
                    _journal.Attempts = Math.Min(10, _journal.Attempts + 1);
                    _journal.RetryAt = _clock.GetUtcNow() + (retry ?? TimeSpan.FromSeconds(
                        failure is "HTTP_400" or "HTTP_401" or "HTTP_403" or "HTTP_404" ? 3600 : Math.Min(300, 5 * Math.Pow(2, _journal.Attempts))));
                }
                _journal.FailureCode = failure; Persist();
            }
        }
        finally { _delivery.Release(); }
    }

    private bool Persist()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(_options.StateFile))!);
            File.WriteAllText(_options.StateFile + ".tmp", JsonSerializer.Serialize(_journal, PlatformJson.Options));
            File.Move(_options.StateFile + ".tmp", _options.StateFile, true); return true;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { _storageHealthy = false; return false; }
    }
}

public static class DiscordAlertRegistration
{
    public static IServiceCollection AddDiscordAlerts(this IServiceCollection services, IConfiguration configuration)
    {
        services.Configure<DiscordAlertOptions>(configuration.GetSection("discord_alerts"));
        services.AddHttpClient("discord-alerts", client => client.Timeout = TimeSpan.FromSeconds(10))
            .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler { AllowAutoRedirect = false, UseCookies = false })
            .RemoveAllLoggers(); // A webhook URL contains its credential.
        services.AddSingleton<DiscordAlerts>(sp => new(sp.GetRequiredService<IOptions<DiscordAlertOptions>>(),
            sp.GetRequiredService<IHttpClientFactory>().CreateClient("discord-alerts")));
        services.AddHostedService<DiscordAlertWorker>(); return services;
    }
}

public sealed class DiscordAlertWorker(DiscordAlerts alerts, PlatformMonitor monitor) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!alerts.Enabled) return;
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(3));
        do { alerts.Observe(monitor.Snapshot()); await alerts.DeliverAsync(stoppingToken); }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }
}
