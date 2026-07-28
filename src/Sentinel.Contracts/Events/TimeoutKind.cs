namespace Sentinel.Contracts.Events;

public enum TimeoutKind
{
    TtlExpired,
    IdleTimeout,
    ApiTimeout
}
