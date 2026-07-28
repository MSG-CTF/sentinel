namespace Sentinel.Contracts.Instances;

// ctf-mock과 본 CTF 서버에서 받아온 문제 인스턴스의 최소 감시 단위입니다.
public sealed record MonitoredInstance(
    string InstanceId,
    string TeamId,
    string ChallengeId,
    InstanceStatus Status,
    DateTimeOffset CreatedAt,
    DateTimeOffset? ExpiresAt,
    DateTimeOffset? LastActivityAt);
