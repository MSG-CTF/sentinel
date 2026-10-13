namespace Sentinel.Monitor.Platform;

public sealed record TargetSample(string TargetId, string Status, double? LatencyMs);
public sealed record MonitorSample(DateTimeOffset CheckedAt, IReadOnlyList<TargetSample> Targets);
