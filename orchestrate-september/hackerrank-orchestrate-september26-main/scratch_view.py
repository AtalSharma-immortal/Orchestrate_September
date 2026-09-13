"""
tools/sample_benchmark.py
-------------------------
Local validation script for benchmarking the existing finance engine against
dataset/sample_requests.csv (25 reference requests).

Does not modify any production code.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

# Ensure repository directories are importable
_CURRENT_FILE = Path(__file__).resolve()
_REPO_ROOT = _CURRENT_FILE.parent.parent
_CODE_DIR = _REPO_ROOT / "code"

if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import importlib

_finance_affordability = importlib.import_module("finance.affordability")
AffordabilityEngine = _finance_affordability.AffordabilityEngine
format_amount = _finance_affordability.format_amount

_finance_rates = importlib.import_module("finance.exchange_rates")
get_default_service = _finance_rates.get_default_service

_finance_forecast = importlib.import_module("finance.forecast")
FinancialForecaster = _finance_forecast.FinancialForecaster

_finance_loader = importlib.import_module("finance.loader")
load_events = _finance_loader.load_events
load_payment_options = _finance_loader.load_payment_options
load_profiles = _finance_loader.load_profiles
load_requests = _finance_loader.load_requests

_finance_models = importlib.import_module("finance.models")
AffordabilityEvaluation = _finance_models.AffordabilityEvaluation

_messages_processor = importlib.import_module("messages.message_processor")
build_amendments = _messages_processor.build_amendments


def is_amount_match(pred: float, exp: float, abs_tol: float = 1.0, rel_tol: float = 0.01) -> bool:
    """Check if predicted amount matches expected within absolute or relative tolerance."""
    diff = abs(pred - exp)
    if diff <= abs_tol:
        return True
    if exp != 0 and (diff / abs(exp)) <= rel_tol:
        return True
    return False


def normalize_plan(plan_str: str) -> str:
    """Normalize payment plan string for deterministic comparison."""
    if not plan_str or plan_str.strip().lower() in ("none", ""):
        return "none"
    parts = plan_str.strip().split("|")
    norm_parts = []
    for p in parts:
        if ":" in p:
            dt, amt = p.split(":", 1)
            try:
                amt_val = float(amt)
                norm_parts.append(f"{dt.strip()}:{format_amount(amt_val)}")
            except ValueError:
                norm_parts.append(p.strip())
        else:
            norm_parts.append(p.strip())
    return "|".join(norm_parts)


def run_benchmark() -> None:
    sample_csv_path = _REPO_ROOT / "dataset" / "sample_requests.csv"
    processed_events_path = _REPO_ROOT / "output" / "processed_financial_events.csv"
    if not processed_events_path.exists():
        processed_events_path = _REPO_ROOT / "dataset" / "financial_events.csv"

    messages_path = _REPO_ROOT / "dataset" / "messages.csv"
    profiles_path = _REPO_ROOT / "dataset" / "financial_profiles.csv"
    options_path = _REPO_ROOT / "dataset" / "request_payment_options.csv"

    print("=" * 80)
    print("SAMPLE REQUESTS BENCHMARK (dataset/sample_requests.csv)")
    print(f"Repository Root: {_REPO_ROOT}")
    print(f"Events source:   {processed_events_path}")
    print("=" * 80)

    # 1. Load ground truth records from sample_requests.csv
    gt_rows: Dict[str, dict] = {}
    with open(sample_csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            gt_rows[r["request_id"].strip()] = r

    # 2. Run the same finance pipeline as code/main.py
    amendments = build_amendments(str(messages_path))
    amends_by_user = defaultdict(list)
    for a in amendments:
        amends_by_user[a.user_id].append(a)

    profiles = load_profiles(profiles_path)
    events_by_user = load_events(processed_events_path)
    options_by_request = load_payment_options(options_path)
    requests = load_requests(sample_csv_path)
    rates_service = get_default_service()

    total_requests = len(requests)

    # Accuracy counters
    exact_amount_matches = 0
    tol_amount_matches = 0
    status_matches = 0
    method_matches = 0
    plan_matches = 0
    earliest_date_matches = 0
    spending_matches = 0
    full_row_exact_matches = 0

    mismatches: List[Tuple[str, dict, dict, List[str]]] = []

    for req in requests:
        rid = req.request_id
        gt = gt_rows[rid]

        user_id = req.user_id
        profile = profiles[user_id]
        user_events = events_by_user.get(user_id, [])
        user_amends = amends_by_user.get(user_id, [])
        user_options = options_by_request.get(rid, [])

        forecaster = FinancialForecaster(
            profile=profile,
            events=user_events,
            exchange_service=rates_service,
            amendments=user_amends,
        )
        engine = AffordabilityEngine(profile, forecaster)
        eval_res = engine.evaluate_request(req, user_options)

        safe_amt = max(0.0, min(float(req.requested_amount), float(eval_res.amount_safe_to_pay)))
        pred_amount_str = format_amount(safe_amt)
        pred_amount_val = safe_amt

        exp_amount_str = gt["amount_safe_to_pay"].strip()
        exp_amount_val = float(exp_amount_str)

        pred_status = eval_res.affordability_status.strip()
        exp_status = gt["affordability_status"].strip()

        pred_method = eval_res.recommended_payment_method.strip()
        exp_method = gt["recommended_payment_method"].strip()

        pred_plan = normalize_plan(eval_res.payment_plan)
        exp_plan = normalize_plan(gt["payment_plan"])

        pred_earliest = eval_res.earliest_date_for_full_payment.strip()
        exp_earliest = gt.get("earliest_date_for_full_payment", "").strip()

        pred_changes = eval_res.spending_changes_needed.strip()
        exp_changes = gt.get("spending_changes_needed", "none").strip()

        # Checks
        is_exact_amt = abs(pred_amount_val - exp_amount_val) < 1e-4
        is_tol_amt = is_amount_match(pred_amount_val, exp_amount_val)

        is_status = pred_status == exp_status
        is_method = pred_method == exp_method
        is_plan = pred_plan == exp_plan
        is_earliest = pred_earliest == exp_earliest
        is_changes = pred_changes == exp_changes

        if is_exact_amt:
            exact_amount_matches += 1
        if is_tol_amt:
            tol_amount_matches += 1
        if is_status:
            status_matches += 1
        if is_method:
            method_matches += 1
        if is_plan:
            plan_matches += 1
        if is_earliest:
            earliest_date_matches += 1
        if is_changes:
            spending_matches += 1

        failed_fields = []
        if not is_tol_amt:
            failed_fields.append("amount_safe_to_pay")
        if not is_status:
            failed_fields.append("affordability_status")
        if not is_method:
            failed_fields.append("recommended_payment_method")
        if not is_plan:
            failed_fields.append("payment_plan")
        if not is_earliest:
            failed_fields.append("earliest_date_for_full_payment")
        if not is_changes:
            failed_fields.append("spending_changes_needed")

        pred_dict = {
            "amount_safe_to_pay": pred_amount_str,
            "affordability_status": pred_status,
            "recommended_payment_method": pred_method,
            "payment_plan": pred_plan,
            "earliest_date_for_full_payment": pred_earliest,
            "spending_changes_needed": pred_changes,
        }
        exp_dict = {
            "amount_safe_to_pay": exp_amount_str,
            "affordability_status": exp_status,
            "recommended_payment_method": exp_method,
            "payment_plan": exp_plan,
            "earliest_date_for_full_payment": exp_earliest,
            "spending_changes_needed": exp_changes,
        }

        if not failed_fields and is_exact_amt:
            full_row_exact_matches += 1
        else:
            mismatches.append((rid, exp_dict, pred_dict, failed_fields))

    # Print summary
    print("\n" + "=" * 80)
    print("ACCURACY SUMMARY")
    print("=" * 80)
    print(f"Total Requests Evaluated:        {total_requests}")
    print(f"affordability_status:            {status_matches}/{total_requests} ({status_matches/total_requests*100:.1f}%)")
    print(f"recommended_payment_method:      {method_matches}/{total_requests} ({method_matches/total_requests*100:.1f}%)")
    print(f"payment_plan:                    {plan_matches}/{total_requests} ({plan_matches/total_requests*100:.1f}%)")
    print(f"earliest_date_for_full_payment:  {earliest_date_matches}/{total_requests} ({earliest_date_matches/total_requests*100:.1f}%)")
    print(f"spending_changes_needed:         {spending_matches}/{total_requests} ({spending_matches/total_requests*100:.1f}%)")
    print(f"amount_safe_to_pay (exact):      {exact_amount_matches}/{total_requests} ({exact_amount_matches/total_requests*100:.1f}%)")
    print(f"amount_safe_to_pay (tolerance):  {tol_amount_matches}/{total_requests} ({tol_amount_matches/total_requests*100:.1f}%)")
    print(f"Full Row Exact Matches (all 6):  {full_row_exact_matches}/{total_requests} ({full_row_exact_matches/total_requests*100:.1f}%)")

    # Print mismatch details
    print("\n" + "=" * 80)
    print(f"MISMATCHING REQUESTS ({len(mismatches)} total)")
    print("=" * 80)

    if not mismatches:
        print("None! All 25 sample requests matched expected values perfectly.")
    else:
        for rid, exp, pred, failed in mismatches:
            fail_str = ', '.join(failed) if failed else 'amount exact difference only'
            print(f"\n--- {rid} (Mismatches: {fail_str}) ---")
            for field in [
                "affordability_status",
                "recommended_payment_method",
                "amount_safe_to_pay",
                "payment_plan",
                "earliest_date_for_full_payment",
                "spending_changes_needed",
            ]:
                mark = "[MISMATCH]" if field in failed else "  [MATCH] "
                print(f"  {mark} {field}:")
                print(f"      Expected : {exp[field]}")
                print(f"      Predicted: {pred[field]}")


if __name__ == "__main__":
    run_benchmark()
