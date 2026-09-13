"""Finance package for HackerRank 'Buy or Wait?' solution."""

from .affordability import AffordabilityEngine
from .exchange_rates import (
    ExchangeRateError,
    ExchangeRateNotFoundError,
    ExchangeRateService,
    convert_amount,
    get_rate,
)
from .forecast import FinancialForecaster, ProjectedCashEvent
from .loader import (
    load_events,
    load_payment_options,
    load_profiles,
    load_requests,
)
from .models import (
    AffordabilityEvaluation,
    FinancialEvent,
    FinancialProfile,
    PaymentOption,
    PaymentScheduleItem,
    PurchaseRequest,
    SpendingChange,
)
from .spending_adjustments import SpendingAdjustmentEngine

__all__ = [
    "ExchangeRateService",
    "ExchangeRateError",
    "ExchangeRateNotFoundError",
    "get_rate",
    "convert_amount",
    "FinancialProfile",
    "FinancialEvent",
    "PaymentOption",
    "PurchaseRequest",
    "SpendingChange",
    "PaymentScheduleItem",
    "AffordabilityEvaluation",
    "load_profiles",
    "load_events",
    "load_payment_options",
    "load_requests",
    "FinancialForecaster",
    "ProjectedCashEvent",
    "SpendingAdjustmentEngine",
    "AffordabilityEngine",
]
