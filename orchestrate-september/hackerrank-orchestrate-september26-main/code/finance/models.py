"""Domain models for the financial forecasting and affordability engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass
class FinancialProfile:
    """Represents a user's financial configuration and constraints."""
    user_id: str
    home_currency: str
    current_available_balance: float
    minimum_balance_to_keep: float
    financial_priorities: List[str] = field(default_factory=list)
    expense_categories_to_protect: Set[str] = field(default_factory=set)
    expense_categories_user_is_willing_to_reduce: Set[str] = field(default_factory=set)
    expense_categories_user_is_willing_to_stop: Set[str] = field(default_factory=set)
    payment_methods_user_will_consider: Set[str] = field(default_factory=set)
    max_installment_months: Optional[int] = None

    @property
    def currency(self) -> str:
        return self.home_currency


@dataclass
class FinancialEvent:
    """Represents a transaction, obligation, income, or investment event."""
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: float
    currency: str
    event_date: str
    settlement_date: str
    status: str
    linked_event_id: str = ""
    flexibility: str = "fixed"
    minimum_allowed_amount: Optional[float] = None

    @property
    def effective_date(self) -> str:
        """The date this event impacts or impacted cash flow."""
        return self.settlement_date if self.settlement_date else self.event_date


@dataclass
class PaymentOption:
    """Represents a vendor/provider payment option for a purchase request."""
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: float
    number_of_payments: int
    first_payment_date: str
    payment_frequency_days: Optional[int]
    financing_fee: float
    total_payable_amount: float


@dataclass
class PurchaseRequest:
    """Represents an evaluation request from a user."""
    request_id: str
    user_id: str
    request_date: str
    request_type: str
    requested_amount: float
    desired_completion_date: str
    allows_partial_payment: bool
    request_text: str


@dataclass
class SpendingChange:
    """Represents a recommended change to a recurring flexible expense."""
    action: str
    event_id: str
    new_amount: Optional[float] = None

    def to_output_str(self) -> str:
        if self.action == "stop":
            return f"stop:{self.event_id}"
        elif self.action == "reduce_to":
            if self.new_amount is not None:
                if self.new_amount == int(self.new_amount):
                    return f"reduce_to:{self.event_id}:{int(self.new_amount)}"
                return f"reduce_to:{self.event_id}:{self.new_amount:.2f}"
            return f"reduce_to:{self.event_id}:0"
        return ""


@dataclass
class PaymentScheduleItem:
    """A single payment date and amount in a payment plan."""
    payment_date: str
    amount: float


@dataclass
class AffordabilityEvaluation:
    """Complete evaluation output for a purchase request."""
    request_id: str
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str
