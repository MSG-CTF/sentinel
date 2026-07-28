namespace Sentinel.Contracts.Broker;

// 계정별 quota와 budget 상태를 함께 보고 resource risk를 판단하기 위한 모델입니다.
public sealed record BrokerAccountStatus(
    string AccountId,
    BrokerAccountState State,
    int? RemainingQuota,
    decimal? BudgetUsed,
    decimal? BudgetLimit,
    DateTimeOffset CheckedAt);
