"""CSV data loading and preprocessing utilities."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Union

from .models import (
    FinancialEvent,
    FinancialProfile,
    PaymentOption,
    PurchaseRequest,
)

DATASET_DIR = Path(__file__).resolve().parent.parent.parent / "dataset"
PROFILES_CSV = DATASET_DIR / "financial_profiles.csv"
EVENTS_CSV = DATASET_DIR / "financial_events.csv"
PAYMENT_OPTIONS_CSV = DATASET_DIR / "request_payment_options.csv"
REQUESTS_CSV = DATASET_DIR / "requests.csv"


def _parse_option_number(opt_id: str) -> Union[int, str]:
    """Extract trailing numeric index from payment_option_id for natural sorting."""
    parts = opt_id.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return int(parts[1])
    return opt_id


def load_profiles(csv_path: Optional[Union[str, Path]] = None) -> Dict[str, FinancialProfile]:
    """Load all financial profiles keyed by user_id."""
    path = Path(csv_path) if csv_path is not None else PROFILES_CSV
    profiles: Dict[str, FinancialProfile] = {}
    with open(path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            user_id = row["user_id"].strip()
            priorities = [p.strip().lower() for p in row.get("financial_priorities", "").split("|") if p.strip()]
            protect = {c.strip().lower() for c in row.get("expense_categories_to_protect", "").split("|") if c.strip()}
            reduce_cats = {c.strip().lower() for c in row.get("expense_categories_user_is_willing_to_reduce", "").split("|") if c.strip()}
            stop_cats = {c.strip().lower() for c in row.get("expense_categories_user_is_willing_to_stop", "").split("|") if c.strip()}
            methods = {m.strip().lower() for m in row.get("payment_methods_user_will_consider", "").split("|") if m.strip()}
            
            raw_months = row.get("max_installment_months", "").strip()
            max_months = int(raw_months) if raw_months and raw_months.isdigit() else None

            profiles[user_id] = FinancialProfile(
                user_id=user_id,
                home_currency=row["home_currency"].strip().upper(),
                current_available_balance=float(row["current_available_balance"]),
                minimum_balance_to_keep=float(row["minimum_balance_to_keep"]),
                financial_priorities=priorities,
                expense_categories_to_protect=protect,
                expense_categories_user_is_willing_to_reduce=reduce_cats,
                expense_categories_user_is_willing_to_stop=stop_cats,
                payment_methods_user_will_consider=methods,
                max_installment_months=max_months,
            )
    return profiles


def load_events(
    csv_path: Optional[Union[str, Path]] = None,
    amount_overrides: Optional[Dict[str, float]] = None,
) -> Dict[str, List[FinancialEvent]]:
    """Load all financial events grouped by user_id and sorted chronologically."""
    path = Path(csv_path) if csv_path is not None else EVENTS_CSV
    overrides = amount_overrides or {}
    events_by_user: Dict[str, List[FinancialEvent]] = {}

    with open(path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            event_id = row["event_id"].strip()
            user_id = row["user_id"].strip()
            raw_amt = row.get("amount", "").strip()
            
            if event_id in overrides:
                amt = float(overrides[event_id])
            elif raw_amt:
                amt = float(raw_amt)
            else:
                amt = 0.0

            raw_min_allowed = row.get("minimum_allowed_amount", "").strip()
            min_allowed = float(raw_min_allowed) if raw_min_allowed else None

            event = FinancialEvent(
                event_id=event_id,
                user_id=user_id,
                event_type=row.get("event_type", "").strip().lower(),
                description=row.get("description", "").strip(),
                category=row.get("category", "").strip().lower(),
                direction=row.get("direction", "").strip().lower(),
                amount=amt,
                currency=row.get("currency", "").strip().upper(),
                event_date=row.get("event_date", "").strip(),
                settlement_date=row.get("settlement_date", "").strip(),
                status=row.get("status", "").strip().lower(),
                linked_event_id=row.get("linked_event_id", "").strip(),
                flexibility=row.get("flexibility", "fixed").strip().lower(),
                minimum_allowed_amount=min_allowed,
            )
            events_by_user.setdefault(user_id, []).append(event)

    for user_id in events_by_user:
        events_by_user[user_id].sort(key=lambda e: e.effective_date)

    return events_by_user


def load_payment_options(
    csv_path: Optional[Union[str, Path]] = None,
) -> Dict[str, List[PaymentOption]]:
    """Load request payment options grouped by request_id."""
    path = Path(csv_path) if csv_path is not None else PAYMENT_OPTIONS_CSV
    options_by_request: Dict[str, List[PaymentOption]] = {}

    with open(path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            req_id = row["request_id"].strip()
            freq_raw = row.get("payment_frequency_days", "").strip()
            freq = int(freq_raw) if freq_raw and freq_raw.isdigit() else None

            opt = PaymentOption(
                payment_option_id=row["payment_option_id"].strip(),
                request_id=req_id,
                payment_method=row.get("payment_method", "").strip().lower(),
                payment_amount=float(row["payment_amount"]),
                number_of_payments=int(row["number_of_payments"]),
                first_payment_date=row.get("first_payment_date", "").strip(),
                payment_frequency_days=freq,
                financing_fee=float(row.get("financing_fee", "0") or "0"),
                total_payable_amount=float(row["total_payable_amount"]),
            )
            options_by_request.setdefault(req_id, []).append(opt)

    for req_id in options_by_request:
        options_by_request[req_id].sort(key=lambda o: _parse_option_number(o.payment_option_id))

    return options_by_request


def load_requests(
    csv_path: Optional[Union[str, Path]] = None,
) -> List[PurchaseRequest]:
    """Load all purchase evaluation requests."""
    path = Path(csv_path) if csv_path is not None else REQUESTS_CSV
    requests: List[PurchaseRequest] = []

    with open(path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            allows_partial = str(row.get("allows_partial_payment", "")).strip().lower() in ("true", "1", "yes")
            requests.append(
                PurchaseRequest(
                    request_id=row["request_id"].strip(),
                    user_id=row["user_id"].strip(),
                    request_date=row.get("request_date", "").strip(),
                    request_type=row.get("request_type", "").strip().lower(),
                    requested_amount=float(row["requested_amount"]),
                    desired_completion_date=row.get("desired_completion_date", "").strip(),
                    allows_partial_payment=allows_partial,
                    request_text=row.get("request_text", "").strip(),
                )
            )
    return requests
