using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Options;

namespace Sentinel.Monitor.Platform;

public sealed class BrokerFleetOptions
{
    public const string SectionName = "broker_fleet";
    public bool Enabled { get; set; }
    [Microsoft.Extensions.Configuration.ConfigurationKeyName("state_file")]
    public string StateFile { get; set; } = "data/broker-fleet.json";
    [Microsoft.Extensions.Configuration.ConfigurationKeyName("publisher_token_env")]
    public string PublisherTokenEnv { get; set; } = "BROKER_FLEET_PUBLISHER_TOKEN";
    [Microsoft.Extensions.Configuration.ConfigurationKeyName("observer_token_env")]
    public string ObserverTokenEnv { get; set; } = "BROKER_FLEET_OBSERVER_TOKEN";
    public bool IsValid() => !Enabled || StateFile == "data/broker-fleet.json"
        && PublisherTokenEnv != ObserverTokenEnv
        && new[] { PublisherTokenEnv, ObserverTokenEnv }.All(n => Regex.IsMatch(n, "^[A-Z][A-Z0-9_]{0,63}$"));
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record FleetNode([property: JsonRequired] Guid ResourceTargetId, [property: JsonRequired] Guid? NodeUid,
    [property: JsonRequired] string Name, [property: JsonRequired] string ScopeId, [property: JsonRequired] string? ServerUrl);
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record FleetWindows([property: JsonRequired] Guid ResourceTargetId, [property: JsonRequired] string Name,
    [property: JsonRequired] string Host, [property: JsonRequired] int Port);
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record FleetRoster([property: JsonRequired] int SchemaVersion, [property: JsonRequired] DateTimeOffset CheckedAt,
    [property: JsonRequired] bool Complete, [property: JsonRequired] FleetNode[] Nodes, [property: JsonRequired] FleetWindows[] Windows);
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record FleetRdp([property: JsonRequired] DateTimeOffset CheckedAt, [property: JsonRequired] bool Reachable);
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record FleetReport([property: JsonRequired] Guid ResourceTargetId, ClusterHeartbeat? Node = null, FleetRdp? Windows = null);
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record FleetBatch([property: JsonRequired] int SchemaVersion, [property: JsonRequired] string RosterVersion,
    [property: JsonRequired] DateTimeOffset CheckedAt, [property: JsonRequired] FleetReport[] Reports);

public sealed class BrokerFleetStore
{
    public const int MaximumMembers = 64;
    public const int FreshSeconds = 180;
    private readonly BrokerFleetOptions _options;
    private readonly TimeProvider _clock;
    private readonly object _gate = new();
    private FleetRoster? _roster;
    private string? _version;
    private bool _storageHealthy = true;
    private Dictionary<Guid, FleetReport> _reports = [];
    private Dictionary<string, string> _revisions = [];
    private PlatformTarget[] _targets = [];
    private ClusterHeartbeatStore _nodes;
    private HeartbeatStore _windows;
    private DateTimeOffset? _lastBatch;

    public BrokerFleetStore(IOptions<BrokerFleetOptions> options, TimeProvider? clock = null)
    {
        _options = options.Value; _clock = clock ?? TimeProvider.System;
        (_nodes, _windows) = Stores([]);
        if (!_options.Enabled || !File.Exists(_options.StateFile)) return;
        try
        {
            var value = JsonSerializer.Deserialize<FleetRoster>(File.ReadAllBytes(_options.StateFile), PlatformJson.Options);
            if (value is null || !ValidRoster(value)) { _storageHealthy = false; return; }
            Apply(value);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException)
        { _storageHealthy = false; }
    }

    public bool Enabled => _options.Enabled;
    public bool Authorize(string authorization, bool publisher)
    {
        if (!Enabled || !authorization.StartsWith("Bearer ", StringComparison.Ordinal)) return false;
        var value = Environment.GetEnvironmentVariable(publisher ? _options.PublisherTokenEnv : _options.ObserverTokenEnv);
        var other = Environment.GetEnvironmentVariable(publisher ? _options.ObserverTokenEnv : _options.PublisherTokenEnv);
        if (value == other) return false;
        return value?.Length >= 32 && CryptographicOperations.FixedTimeEquals(SHA256.HashData(Encoding.UTF8.GetBytes(value)),
            SHA256.HashData(Encoding.UTF8.GetBytes(authorization[7..])));
    }

    public static string NodeId(Guid id) => "fleet-node-" + id.ToString("N");
    public static string WindowsId(Guid id) => "fleet-windows-" + id.ToString("N");
    public static bool ReservedId(string id) => id.StartsWith("fleet-node-", StringComparison.Ordinal)
        || id.StartsWith("fleet-windows-", StringComparison.Ordinal);
    public PlatformTarget[] Targets() { lock (_gate) return _targets.ToArray(); }
    public string? Version { get { lock (_gate) return _version; } }

    public object Desired()
    {
        lock (_gate) return new { schema_version = 1, roster_version = _version, checked_at = _roster?.CheckedAt,
            complete = Fresh(), storage_healthy = _storageHealthy, nodes = _roster?.Nodes ?? [], windows = _roster?.Windows ?? [] };
    }

    public string AcceptRoster(FleetRoster value)
    {
        lock (_gate)
        {
            if (!Enabled || !ValidRoster(value) || value.CheckedAt > _clock.GetUtcNow().AddSeconds(30)
                || _clock.GetUtcNow() - value.CheckedAt > TimeSpan.FromSeconds(90)) return "INVALID";
            if (_roster is not null && value.CheckedAt <= _roster.CheckedAt) return "REPLAY";
            var parent = Path.GetDirectoryName(Path.GetFullPath(_options.StateFile))!;
            var temporary = Path.Combine(parent, ".broker-fleet-" + Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                Directory.CreateDirectory(parent);
                using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    JsonSerializer.Serialize(file, value, PlatformJson.Options); file.Flush(true);
                }
                if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(temporary, UnixFileMode.UserRead | UnixFileMode.UserWrite);
                File.Move(temporary, _options.StateFile, true);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            { _storageHealthy = false; return "STORAGE_UNAVAILABLE"; }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
            Apply(value); _storageHealthy = true; return "ACCEPTED";
        }
    }

    public string AcceptBatch(FleetBatch value)
    {
        lock (_gate)
        {
            if (!Enabled || !Fresh() || !_storageHealthy || value.RosterVersion != _version) return "ROSTER_CHANGED";
            if (value.SchemaVersion != 1 || value.Reports is null || value.Reports.Any(r => r is null) || value.Reports.Length != _targets.Length
                || value.Reports.Select(r => r.ResourceTargetId).Distinct().Count() != value.Reports.Length
                || value.CheckedAt > _clock.GetUtcNow().AddSeconds(30) || _clock.GetUtcNow() - value.CheckedAt > TimeSpan.FromSeconds(90)) return "INVALID";
            if (_lastBatch is { } last && value.CheckedAt <= last) return "REPLAY";
            var (nodes, windows) = Stores(_targets);
            foreach (var report in value.Reports)
            {
                var node = _roster!.Nodes.FirstOrDefault(n => n.ResourceTargetId == report.ResourceTargetId);
                var rdp = _roster.Windows.FirstOrDefault(n => n.ResourceTargetId == report.ResourceTargetId);
                var at = report.Node?.CheckedAt ?? report.Windows?.CheckedAt;
                if (at is null || at > value.CheckedAt || value.CheckedAt - at > TimeSpan.FromSeconds(60)
                    || _reports.TryGetValue(report.ResourceTargetId, out var previous)
                        && at <= (previous.Node?.CheckedAt ?? previous.Windows!.CheckedAt)) return "REPLAY";
                if (node is not null)
                {
                    if (report.Node is null || report.Windows is not null || !nodes.Accept(NodeId(node.ResourceTargetId), report.Node)) return "INVALID";
                }
                else if (rdp is not null)
                {
                    if (report.Windows is null || report.Node is not null || !windows.Accept(WindowsId(rdp.ResourceTargetId), Heartbeat(report.Windows))) return "INVALID";
                }
                else return "INVALID";
            }
            _nodes = nodes; _windows = windows; _reports = value.Reports.ToDictionary(r => r.ResourceTargetId);
            _lastBatch = value.CheckedAt; return "ACCEPTED";
        }
    }

    public ProbeObservation[] Observations()
    {
        lock (_gate) return _targets.Select(target => Decorate(target.Kind == "cluster_heartbeat"
            ? _nodes.Read(target) : _windows.Read(target))).ToArray();
    }

    public ProbeObservation Project(PlatformTarget target, ProbeObservation? previous)
    {
        lock (_gate)
        {
            if (previous is null || previous.Components.GetValueOrDefault("fleet_member_revision") != _revisions.GetValueOrDefault(target.TargetId))
                return Decorate(PlatformProbeClient.Result(target, "PENDING", "FLEET_REPORT_PENDING", null, null, null));
            return Decorate(previous);
        }
    }

    public ProbeObservation ReadStatus(PlatformTarget target)
    {
        lock (_gate)
        {
            var components = new Dictionary<string, string> { ["source"] = "resource_broker", ["complete"] = Fresh() ? "true" : "false",
                ["node_count"] = (_roster?.Nodes.Length ?? 0).ToString(), ["windows_count"] = (_roster?.Windows.Length ?? 0).ToString(),
                ["roster_version"] = _version ?? "pending", ["storage_healthy"] = _storageHealthy ? "true" : "false" };
            return PlatformProbeClient.Result(target, Fresh() && _storageHealthy ? "HEALTHY" : "UNVERIFIED",
                !_storageHealthy ? "FLEET_STORAGE_UNAVAILABLE" : Fresh() ? "FLEET_ROSTER_READY" : "FLEET_ROSTER_STALE",
                _roster?.CheckedAt, null, null, components);
        }
    }

    private bool Fresh() => _roster is not null && (_clock.GetUtcNow() - _roster.CheckedAt).TotalSeconds is >= -30 and <= FreshSeconds;
    private ProbeObservation Decorate(ProbeObservation result)
    {
        var components = new Dictionary<string, string>(result.Components) { ["fleet_source"] = "resource_broker",
            ["fleet_member_revision"] = _revisions.GetValueOrDefault(result.TargetId) ?? "pending", ["roster_version"] = _version ?? "pending" };
        if (!Fresh() || !_storageHealthy) return result with { Status = "UNVERIFIED", ReasonCode = "FLEET_ROSTER_STALE",
            Risk = RiskEvaluator.Unmeasured("UNVERIFIED"), Components = components };
        return result with { Components = components };
    }

    private void Apply(FleetRoster value)
    {
        var version = Hash(new { nodes = value.Nodes.OrderBy(n => n.ResourceTargetId), windows = value.Windows.OrderBy(w => w.ResourceTargetId) });
        if (_version == version) { _roster = value; return; }
        var oldRevisions = _revisions;
        var oldReports = _reports;
        _roster = value;
        _version = version;
        _targets = value.Nodes.Select(NodeTarget).Concat(value.Windows.Select(WindowsTarget)).ToArray();
        _revisions = value.Nodes.ToDictionary(n => NodeId(n.ResourceTargetId), Hash)
            .Concat(value.Windows.ToDictionary(w => WindowsId(w.ResourceTargetId), Hash)).ToDictionary(r => r.Key, r => r.Value);
        (_nodes, _windows) = Stores(_targets); _reports = [];
        foreach (var pair in oldReports)
        {
            var id = pair.Value.Node is null ? WindowsId(pair.Key) : NodeId(pair.Key);
            if (_revisions.GetValueOrDefault(id) != oldRevisions.GetValueOrDefault(id)) continue;
            var accepted = pair.Value.Node is { } node ? _nodes.Accept(id, node) : _windows.Accept(id, Heartbeat(pair.Value.Windows!));
            if (accepted) _reports[pair.Key] = pair.Value;
        }
    }

    private PlatformTarget NodeTarget(FleetNode node) => new() { TargetId = NodeId(node.ResourceTargetId),
        Name = Truncate("실행 노드 · " + node.Name + " · " + node.ScopeId), Role = "K3S", Track = "RUNTIME", Kind = "cluster_heartbeat",
        AuthTokenEnv = _options.ObserverTokenEnv, ExpectedFields = new() { ["cluster_target_id"] = node.NodeUid?.ToString() ?? "unbound-" + node.ResourceTargetId.ToString("N") },
        EvidenceScope = "브로커 활성 등록 자동 동기화 · Node UID·인증서 검증 후 Node Ready·pressure·CPU·메모리 조회 · 문제 기능은 별도 검증" };
    private PlatformTarget WindowsTarget(FleetWindows windows) => new() { TargetId = WindowsId(windows.ResourceTargetId),
        Name = Truncate("Windows · " + windows.Name), Role = "WINDOWS", Track = "RUNTIME", Kind = "service_heartbeat",
        AuthTokenEnv = _options.ObserverTokenEnv, ExpectedFields = new() { ["probe_kind"] = "rdp_tcp", ["connection_target_count"] = "1" },
        EvidenceScope = "브로커 활성 Windows 등록 자동 동기화 · 등록한 RDP TCP 포트 연결 확인 · OS 자원·로그인·프로그램 실행은 별도 검증" };
    private static string Truncate(string value) => value.Length > 100 ? value[..100] : value;
    private (ClusterHeartbeatStore, HeartbeatStore) Stores(PlatformTarget[] targets)
    {
        var options = Microsoft.Extensions.Options.Options.Create(new PlatformOptions { Targets = targets.ToList() });
        return (new(options, _clock), new(options, _clock));
    }
    private static ServiceHeartbeat Heartbeat(FleetRdp rdp) => new(rdp.CheckedAt, rdp.Reachable ? "active" : "failed",
        null, null, null, null, "unverified", ConnectionTargetCount: 1, ConnectionReadyCount: rdp.Reachable ? 1 : 0);
    private static string Hash<T>(T value) => Convert.ToHexString(SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(value, PlatformJson.Options))).ToLowerInvariant();
    private static bool Text(string? value, int max) => value is not null && value.Length is > 0 && value.Length <= max && !value.Any(char.IsControl);
    public static bool Address(string? value)
    {
        if (!IPAddress.TryParse(value, out var ip)) return false;
        if (ip.IsIPv4MappedToIPv6) ip = ip.MapToIPv4();
        var bytes = ip.GetAddressBytes();
        return !IPAddress.IsLoopback(ip) && !ip.Equals(IPAddress.Any) && !ip.Equals(IPAddress.IPv6Any)
            && !ip.IsIPv6LinkLocal && !ip.IsIPv6Multicast && !(bytes.Length == 4 && (bytes[0] is 0 or >= 224 || bytes[0] == 169 && bytes[1] == 254));
    }
    private static bool ValidRoster(FleetRoster value)
    {
        if (value.SchemaVersion != 1 || !value.Complete || value.Nodes is null || value.Windows is null
            || value.Nodes.Any(n => n is null) || value.Windows.Any(w => w is null)
            || value.Nodes.Length + value.Windows.Length > MaximumMembers || value.CheckedAt == default) return false;
        var ids = value.Nodes.Select(n => n.ResourceTargetId).Concat(value.Windows.Select(w => w.ResourceTargetId)).ToArray();
        var bound = value.Nodes.Where(n => n.NodeUid is not null).Select(n => n.NodeUid).ToArray();
        if (ids.Any(id => id == Guid.Empty) || ids.Distinct().Count() != ids.Length || bound.Any(id => id == Guid.Empty) || bound.Distinct().Count() != bound.Length) return false;
        return value.Nodes.All(n => Text(n.Name, 80) && Text(n.ScopeId, 80)
                && (n.ServerUrl is null || Uri.TryCreate(n.ServerUrl, UriKind.Absolute, out var uri) && uri.Scheme == "https" && uri.Port == 6443
                && uri.AbsolutePath == "/" && uri.UserInfo == "" && uri.Query == "" && uri.Fragment == "" && Address(uri.Host)))
            && value.Windows.All(w => Text(w.Name, 80) && Address(w.Host) && w.Port is >= 1 and <= 65535);
    }
}
