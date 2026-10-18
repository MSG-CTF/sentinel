using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Renci.SshNet;

namespace Sentinel.Monitor.Platform;

public sealed record ManagedAsset(string AssetId, string Host, int Port, string Username,
    string HostKeySha256, string Service, string PrivateKeyEnv);
public sealed record AssetActionRequest(string Action, string RequestId, string? ConfirmAssetId = null);
public sealed record AssetActionResult(string RequestId, string AssetId, string Action, string Status,
    string Code, DateTimeOffset CreatedAt, string Actor, string Output = "");
public sealed record SshActionOutput(int ExitCode, string Output);
public interface IAssetSshClient
{
    Task<SshActionOutput> RunAsync(ManagedAsset asset, string action, CancellationToken cancellationToken);
}

public sealed class AssetSshClient : IAssetSshClient
{
    public async Task<SshActionOutput> RunAsync(ManagedAsset asset, string action, CancellationToken cancellationToken)
    {
        if (action is not ("status" or "resources" or "start")) throw new ArgumentException("Unsupported asset action");
        var encoded = Environment.GetEnvironmentVariable(asset.PrivateKeyEnv) ?? throw new InvalidOperationException("SSH credential unavailable");
        using var keyBytes = new MemoryStream(Convert.FromBase64String(encoded));
        using var key = new PrivateKeyFile(keyBytes);
        using var ssh = new SshClient(asset.Host, asset.Port, asset.Username, key);
        ssh.ConnectionInfo.Timeout = TimeSpan.FromSeconds(10);
        // The registered fingerprint is the host's Ed25519 public key, read over trusted SSH.
        foreach (var algorithm in ssh.ConnectionInfo.HostKeyAlgorithms.Keys.Where(name => name != "ssh-ed25519").ToArray())
            ssh.ConnectionInfo.HostKeyAlgorithms.Remove(algorithm);
        ssh.HostKeyReceived += (_, e) => e.CanTrust = CryptographicOperations.FixedTimeEquals(
            SHA256.HashData(e.HostKey), Convert.FromBase64String(asset.HostKeySha256));
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(25));
        await ssh.ConnectAsync(timeout.Token);
        using var command = ssh.CreateCommand(action);
        command.CommandTimeout = TimeSpan.FromSeconds(15);
        await command.ExecuteAsync(timeout.Token);
        // The root-owned forced command only emits allowlisted host/service facts, never journal/env files.
        var output = command.Result;
        if (output.Length > 16384) throw new InvalidOperationException("SSH output exceeds limit");
        return new(command.ExitStatus ?? -1, output);
    }
}

public sealed class AssetOperations
{
    private readonly IReadOnlyList<ManagedAsset> _assets;
    private readonly IAssetSshClient _ssh;
    private readonly TimeProvider _clock;
    private readonly string _file;
    private readonly object _gate = new();
    private readonly SemaphoreSlim _execution = new(1, 1);
    private readonly List<AssetActionResult> _history = [];
    private bool _storageHealthy = true;
    public bool Enabled => _assets.Count > 0;

    public static IReadOnlyList<ManagedAsset> Load()
    {
        if (Environment.GetEnvironmentVariable("SENTINEL_CANDIDATE") == "true") return [];
        var json = Environment.GetEnvironmentVariable("SENTINEL_OPERATIONS_JSON");
        if (string.IsNullOrWhiteSpace(json)) return [];
        var result = JsonSerializer.Deserialize<ManagedAsset[]>(json, PlatformJson.Options) ?? throw new ArgumentException("Invalid managed assets");
        if (result.Length > 32 || result.Select(a => a.AssetId).Distinct().Count() != result.Length || result.Any(a => !Valid(a)))
            throw new ArgumentException("Invalid managed assets");
        return result;
    }

    public static bool Valid(ManagedAsset asset)
    {
        if (!Regex.IsMatch(asset.AssetId, "^[a-z][a-z0-9_-]{0,63}$") ||
            !Regex.IsMatch(asset.Host, "^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$") || asset.Port is < 1 or > 65535 ||
            !Regex.IsMatch(asset.Username, "^[a-z_][a-z0-9_-]{0,31}$") ||
            !Regex.IsMatch(asset.Service, "^(?:[a-zA-Z0-9_.@-]{1,100}\\.service|docker:[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127})$") ||
            !Regex.IsMatch(asset.PrivateKeyEnv, "^SENTINEL_SSH_[A-Z0-9_]+_B64$")) return false;
        try { return Convert.FromBase64String(asset.HostKeySha256).Length == 32; }
        catch (FormatException) { return false; }
    }

    public AssetOperations(IReadOnlyList<ManagedAsset> assets, IAssetSshClient ssh,
        string file = "data/asset-actions.json", TimeProvider? clock = null)
    {
        _assets = assets; _ssh = ssh; _file = file; _clock = clock ?? TimeProvider.System;
        if (!Enabled) return;
        try
        {
            if (!File.Exists(file)) return;
            if (new FileInfo(file).Length > 2_000_000) throw new JsonException();
            var saved = JsonSerializer.Deserialize<List<AssetActionResult>>(File.ReadAllText(file), PlatformJson.Options) ?? throw new JsonException();
            if (saved.Count > 200 || saved.Any(r => !Guid.TryParse(r.RequestId, out _))) throw new JsonException();
            _history.AddRange(saved.Select(r => r.Status == "RUNNING" ? r with { Status = "UNKNOWN", Code = "PROCESS_RESTARTED_NO_RETRY" } : r));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException) { _storageHealthy = false; }
    }

    public object Snapshot(string id, PlatformSnapshot snapshot)
    {
        var asset = _assets.FirstOrDefault(a => a.AssetId == id);
        var sample = snapshot.Targets.FirstOrDefault(t => t.TargetId == id);
        var canStart = asset is not null && _storageHealthy && Stopped(sample);
        lock (_gate) return new { asset_id = id, configured = asset is not null,
            transport = asset is null ? null : "ssh", host = asset?.Host, port = asset?.Port, username = asset?.Username,
            service = asset?.Service, host_key_pinned = asset is not null, storage_healthy = _storageHealthy,
            can_start = canStart, reason = asset is null ? "SSH_NOT_CONFIGURED" : !_storageHealthy ? "AUDIT_STORAGE_UNAVAILABLE" :
                canStart ? "STOPPED_SERVICE_CONFIRMED" : "FRESH_STOPPED_SERVICE_REQUIRED",
            actions = asset is null ? Array.Empty<string>() : new[] { "status", "resources", "start" },
            history = _history.Where(r => r.AssetId == id).Reverse().Take(10).ToArray() };
    }

    private bool Stopped(ProbeObservation? sample) => sample is { Kind: "service_heartbeat", Status: "DOWN", CheckedAt: { } at } &&
        (_clock.GetUtcNow() - at).TotalSeconds is >= 0 and <= 150 &&
        sample.Components.GetValueOrDefault("service_state") is "inactive" or "failed";

    public async Task<AssetActionResult> ExecuteAsync(string id, AssetActionRequest request, string actor,
        PlatformSnapshot snapshot, CancellationToken cancellationToken = default)
    {
        var now = _clock.GetUtcNow();
        AssetActionResult Denied(string code) => new(request.RequestId, id, request.Action, "REJECTED", code, now, actor);
        var asset = _assets.FirstOrDefault(a => a.AssetId == id);
        if (!Guid.TryParseExact(request.RequestId, "D", out _) || request.Action is not ("status" or "resources" or "start")) return Denied("INVALID_ACTION");
        if (asset is null) return Denied("SSH_NOT_CONFIGURED");
        if (!await _execution.WaitAsync(0, cancellationToken)) return Denied("ACTION_IN_PROGRESS");
        try
        {
            lock (_gate)
            {
                var previous = _history.FirstOrDefault(r => r.RequestId == request.RequestId);
                if (previous is not null) return previous.AssetId == id && previous.Action == request.Action ? previous : Denied("REQUEST_ID_CONFLICT");
                if (!_storageHealthy) return Denied("AUDIT_STORAGE_UNAVAILABLE");
                if (request.Action == "start" && (request.ConfirmAssetId != id || !Stopped(snapshot.Targets.FirstOrDefault(t => t.TargetId == id))))
                    return Denied("FRESH_STOPPED_SERVICE_REQUIRED");
                if (_history.Any(r => r.AssetId == id && r.Action == request.Action &&
                    now - r.CreatedAt < TimeSpan.FromSeconds(request.Action == "start" ? 300 : 5))) return Denied("ACTION_COOLDOWN");
                _history.Add(new(request.RequestId, id, request.Action, "RUNNING", "SSH_REQUESTED", now, actor));
                while (_history.Count > 200) _history.RemoveAt(0);
                if (!Persist()) return Denied("AUDIT_STORAGE_UNAVAILABLE");
            }
            AssetActionResult result;
            try
            {
                var output = await _ssh.RunAsync(asset, request.Action, cancellationToken);
                result = new(request.RequestId, id, request.Action, output.ExitCode == 0 ? "SUCCEEDED" : "FAILED",
                    output.ExitCode == 0 ? request.Action == "start" ? "START_REQUESTED_AWAITING_HEARTBEAT" : "SSH_VERIFIED" : "REMOTE_ACTION_REJECTED", now, actor,
                    output.Output.Length > 16384 ? output.Output[..16384] : output.Output);
            }
            catch (Exception)
            {
                // Never return SSH exception text, keys, or retry a possibly executed mutation.
                result = new(request.RequestId, id, request.Action, "UNKNOWN", "SSH_RESULT_UNCONFIRMED_NO_RETRY", now, actor);
            }
            lock (_gate)
            {
                var index = _history.FindIndex(r => r.RequestId == request.RequestId);
                _history[index] = result; Persist();
            }
            return result;
        }
        finally { _execution.Release(); }
    }

    private bool Persist()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(_file))!);
            File.WriteAllText(_file + ".tmp", JsonSerializer.Serialize(_history, PlatformJson.Options));
            File.Move(_file + ".tmp", _file, true); return true;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { _storageHealthy = false; return false; }
    }
}
