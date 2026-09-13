"""
Tests for message_processor.py.

Mirrors the edge-case checklist from the master build spec (section 120) for
everything that is message-ingestion's responsibility:
  - salary increase / temporary reduction / ended salary / changed salary date
  - pending commission / bonus not counted
  - confirmed invoice payout counted on settlement
  - investment valuation (non-cash) vs investment sale (cash)
  - internal account transfer netted
  - refund pending vs refund settled
  - scam/prize-fee pattern excluded
  - explicit event cancellation / amendment
  - multilingual (Indonesian) messages
  - conflicting messages resolved by "newer explicit record wins"
  - prompt-injection defense: instructions embedded in message_text are inert
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from datetime import date, datetime
from models import AmendmentType, Message, SourceType
from message_processor import (
    load_messages,
    process_messages,
    resolve_conflicts,
    build_amendments,
    classify_message,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "messages_sample.csv")


def _by_user(amendments, user_id):
    return [a for a in amendments if a.user_id == user_id]


def test_load_messages_schema():
    messages = load_messages(FIXTURE)
    assert len(messages) == 20
    m = messages[0]
    assert m.message_id == "message_01"
    assert m.user_id == "user_01"
    assert m.source_type == SourceType.EMPLOYER
    assert m.request_id is None
    assert m.related_event_id is None


def test_salary_increase_extracted():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_01")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.SALARY_CHANGE
    assert a.amount == 42750000
    assert a.currency == "IDR"
    assert a.effective_date == date(2025, 8, 15)


def test_salary_date_change_replaces_previous():
    """message_02 says the new date REPLACES the earlier one; after conflict
    resolution only the salary-date-change fact should survive for user_01,
    and it should be the newer message."""
    amendments = build_amendments(FIXTURE)
    u1 = _by_user(amendments, "user_01")
    date_changes = [a for a in u1 if a.amendment_type == AmendmentType.SALARY_DATE_CHANGE]
    assert len(date_changes) == 1
    assert date_changes[0].message_id == "message_02"
    assert date_changes[0].effective_date == date(2025, 8, 20)
    # The original SALARY_CHANGE fact (the amount + promotion) still stands —
    # only the date reference was replaced, not the amount.
    changes = [a for a in u1 if a.amendment_type == AmendmentType.SALARY_CHANGE]
    assert len(changes) == 1
    assert changes[0].amount == 42750000


def test_salary_temporary_reduction_has_bounded_window():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_03")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.SALARY_TEMPORARY
    assert a.amount == 900
    assert a.currency == "EUR"
    assert a.effective_date == date(2025, 7, 5)
    assert a.end_date == date(2025, 9, 5)


def test_salary_increase_amount_currency_order_variant():
    """message_04 has 'EUR 1037.52' — should still parse correctly (this is
    the exact figure/date pair called out in the dataset docs)."""
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_04")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.SALARY_CHANGE
    assert a.amount == 1037.52
    assert a.currency == "EUR"
    assert a.effective_date == date(2025, 6, 15)


def test_salary_ended():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_05")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.SALARY_END
    assert a.effective_date == date(2025, 5, 10)


def test_pending_bonus_excluded_not_confirmed():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_06")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.INCOME_EXCLUDED
    assert a.request_id == "req_101"


def test_pending_payout_then_confirmed_invoice():
    messages = load_messages(FIXTURE)
    pending = classify_message(next(m for m in messages if m.message_id == "message_07"))
    confirmed = classify_message(next(m for m in messages if m.message_id == "message_08"))
    assert pending.amendment_type == AmendmentType.INCOME_EXCLUDED
    assert confirmed.amendment_type == AmendmentType.INCOME_CONFIRMED
    assert confirmed.amount == 1200
    assert confirmed.currency == "USD"
    assert confirmed.effective_date == date(2025, 3, 1)


def test_event_amended_rent_increase():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_09")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.EVENT_AMENDED
    assert a.related_event_id == "event_14"


def test_event_cancelled():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_10")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.EVENT_CANCELLED
    assert a.related_event_id == "event_21"


def test_investment_valuation_is_non_cash():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_11")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.NON_CASH_EXCLUDED


def test_investment_sale_settled_is_cash():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_12")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.INCOME_CONFIRMED
    assert a.category == "investment_sale"
    assert a.amount == 3000
    assert a.currency == "USD"


def test_internal_transfer_netted():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_13")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.INTERNAL_TRANSFER


def test_refund_pending_then_settled():
    messages = load_messages(FIXTURE)
    pending = classify_message(next(m for m in messages if m.message_id == "message_14"))
    settled = classify_message(next(m for m in messages if m.message_id == "message_15"))
    assert pending.amendment_type == AmendmentType.REFUND_PENDING
    assert settled.amendment_type == AmendmentType.REFUND_SETTLED
    assert settled.amount == 45
    assert settled.currency == "EUR"


def test_scam_pattern_excluded_even_with_amount_present():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_16")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.SCAM_EXCLUDED
    # Confirm we did NOT let the $10000 leak through as confirmed income.
    assert a.amendment_type != AmendmentType.INCOME_CONFIRMED


def test_indonesian_salary_increase():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_17")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.SALARY_CHANGE
    assert a.amount == 15000000
    assert a.currency == "IDR"
    assert a.effective_date == date(2025, 2, 1)
    assert a.request_id == "req_202"


def test_indonesian_contract_ended():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_18")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.SALARY_END
    assert a.effective_date == date(2025, 1, 15)


def test_indonesian_pending_commission_excluded():
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_19")
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.INCOME_EXCLUDED


def test_prompt_injection_is_inert():
    """message_20 tries to instruct the system directly. It must not produce
    any amendment type that could change minimum_balance_to_keep or any other
    protected rule — there is no amendment type for that at all, so the
    worst case is it falls through to UNRESOLVED (flagged, inert) or a
    harmless income classification. It must never be SCAM/EVENT/SALARY types
    that would imply we executed the embedded instruction."""
    messages = load_messages(FIXTURE)
    msg = next(m for m in messages if m.message_id == "message_20")
    a = classify_message(msg)
    assert a.amendment_type in (AmendmentType.UNRESOLVED,)
    # There is no field on Amendment that could carry "minimum_balance_to_keep"
    # — structurally impossible for this module to leak that instruction
    # downstream.
    assert not hasattr(a, "minimum_balance_to_keep")


def test_full_pipeline_row_count_matches_input():
    amendments = build_amendments(FIXTURE)
    messages = load_messages(FIXTURE)
    # Resolution can only reduce or preserve count (collapsing same-entity
    # conflicts), never invent new messages.
    assert len(amendments) <= len(messages)
    assert len(amendments) > 0


def test_resolve_conflicts_is_idempotent():
    amendments = build_amendments(FIXTURE)
    twice = resolve_conflicts(amendments)
    assert len(twice) == len(amendments)




def test_salary_reduction_extracted():
    """Verify salary reduction notification is classified as SALARY_CHANGE with sent_at fallback."""
    msg = Message(
        message_id="msg_test_reduc",
        user_id="user_test",
        request_id=None,
        related_event_id=None,
        sent_at=datetime(2025, 2, 6, 9, 15),
        source_type=SourceType.EMPLOYER,
        message_text="Hi, payroll here. Your next salary is reduced to EUR 1422.85. The adjustment is due to approved unpaid leave.",
    )
    a = classify_message(msg)
    assert a.amendment_type == AmendmentType.SALARY_CHANGE
    assert a.amount == 1422.85
    assert a.currency == "EUR"
    assert a.effective_date == date(2025, 2, 6)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
