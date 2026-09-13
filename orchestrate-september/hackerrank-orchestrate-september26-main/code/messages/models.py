"""
models.py
---------
Shared dataclasses/enums for the message-ingestion module.

Schema reference (dataset/messages.csv), confirmed against the repository:
    message_id, user_id, request_id, related_event_id, sent_at, source_type, message_text

- request_id        : nullable — set only when the message pertains to a specific
                       "Buy or Wait?" request.
- related_event_id  : nullable — set only when the message directly amends/describes
                       one row in dataset/financial_events.csv.
- sent_at            : ISO-8601 timestamp.
- source_type        : one of employer | service_provider | bank | merchant | financial_service
- message_text       : free text, multilingual (English + Indonesian observed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class SourceType(str, Enum):
    EMPLOYER = "employer"
    SERVICE_PROVIDER = "service_provider"
    BANK = "bank"
    MERCHANT = "merchant"
    FINANCIAL_SERVICE = "financial_service"
    UNKNOWN = "unknown"


class AmendmentType(str, Enum):
    # Recurring income (salary / payroll) adjustments
    SALARY_CHANGE = "salary_change"              # permanent new amount from effective_date
    SALARY_TEMPORARY = "salary_temporary"        # bounded-period reduced amount
    SALARY_END = "salary_end"                    # income stops after effective_date
    SALARY_DATE_CHANGE = "salary_date_change"    # next confirmed pay date replaces an earlier one

    # One-off cash flow events (not necessarily tied to a recurring salary)
    INCOME_CONFIRMED = "income_confirmed"        # settled/approved -> counts as cash_in on date
    INCOME_EXCLUDED = "income_excluded"          # pending/unapproved -> explicitly NOT counted

    # Corrections to an existing financial_events.csv row
    EVENT_CANCELLED = "event_cancelled"
    EVENT_AMENDED = "event_amended"

    # Investments
    NON_CASH_EXCLUDED = "non_cash_excluded"      # valuation change, no units sold -> not cash

    # Transfers / refunds / fraud
    INTERNAL_TRANSFER = "internal_transfer"      # net to zero, ignore for income/expense
    REFUND_PENDING = "refund_pending"            # not yet settled -> excluded
    REFUND_SETTLED = "refund_settled"            # settled -> cash_in
    SCAM_EXCLUDED = "scam_excluded"               # prize/fee scam pattern -> ignore entirely

    UNRESOLVED = "unresolved"                    # could not classify deterministically


@dataclass
class Message:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: datetime
    source_type: SourceType
    message_text: str


@dataclass
class Amendment:
    """
    A single normalized fact extracted from one message. This is the unit that
    event_reconciler.py / financial_state.py should consume — never raw message
    text. Nothing here is executable; it is data only.
    """
    message_id: str
    user_id: str
    amendment_type: AmendmentType
    sent_at: datetime

    request_id: Optional[str] = None
    related_event_id: Optional[str] = None

    effective_date: Optional[date] = None   # when the change takes effect
    end_date: Optional[date] = None         # for bounded/temporary changes

    amount: Optional[float] = None
    currency: Optional[str] = None
    category: Optional[str] = None          # e.g. "salary", "commission", "investment"

    confidence: float = 1.0                 # 1.0 = deterministic rule match
    evidence_span: str = ""                 # the substring that triggered classification
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "user_id": self.user_id,
            "amendment_type": self.amendment_type.value,
            "sent_at": self.sent_at.isoformat() if self.sent_at else "",
            "request_id": self.request_id or "",
            "related_event_id": self.related_event_id or "",
            "effective_date": self.effective_date.isoformat() if self.effective_date else "",
            "end_date": self.end_date.isoformat() if self.end_date else "",
            "amount": self.amount if self.amount is not None else "",
            "currency": self.currency or "",
            "category": self.category or "",
            "confidence": self.confidence,
            "evidence_span": self.evidence_span,
            "notes": self.notes,
        }
