namespace Sentinel.Contracts.Broker;

public enum BrokerAccountState
{
    Healthy,
    QuotaExhausted,
    BudgetRisk,
    BrokerUnavailable,
    Unknown
}
