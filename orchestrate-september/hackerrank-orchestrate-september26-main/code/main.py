"""
main.py
-------
Orchestration entry point for the "Buy or Wait?" financial decision engine.

Connects:
1. Financial-event / image extraction (resolves missing event amounts via image cache/parser)
2. Message processing (extracts and reconciles factual amendments from messages.csv)
3. Financial forecasting (constructs cash-flow timelines, exchange rate conversion, 90-day simulation)
4. Affordability & plan evaluation (determines amount_safe_to_pay, plan ranking, spending adjustments)
5. Request-level output generation (serializes final decisions to dataset/output.csv)
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional


# Ensure 'code' directory is on sys.path regardless of execution working directory
_CURRENT_FILE = Path(__file__).resolve()
_REPO_ROOT = _CURRENT_FILE.parent.parent
_CODE_DIR = _CURRENT_FILE.parent

if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from finance.affordability import AffordabilityEngine, format_amount
from finance.exchange_rates import get_default_service
from finance.forecast import FinancialForecaster
from finance.loader import (
    load_events,
    load_payment_options,
    load_profiles,
    load_requests,
)
from finance.models import AffordabilityEvaluation, PurchaseRequest
from messages.message_processor import build_amendments


OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]


def resolve_financial_events(
    repo_root: Path,
    events_rel_path: str = "dataset/financial_events.csv",
    images_rel_path: str = "dataset/images.csv",
    processed_rel_path: str = "output/processed_financial_events.csv",
    cache_rel_path: str = "image_cache.json",
) -> Path:
    """Ensure all financial events have resolved amounts, using image cache/parser when needed.

    Returns the path to the complete processed financial events CSV.
    Never modifies the original raw events CSV.
    """
    events_path = repo_root / events_rel_path
    images_path = repo_root / images_rel_path
    processed_path = repo_root / processed_rel_path
    cache_path = repo_root / cache_rel_path

    if not events_path.exists():
        raise FileNotFoundError(f"Required financial events file not found: {events_path}")

    # Check if processed events file already exists and has all amounts populated
    if processed_path.exists():
        try:
            with open(processed_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                has_null = False
                row_count = 0
                for row in reader:
                    row_count += 1
                    amt = row.get("amount", "").strip()
                    if not amt:
                        has_null = True
                        break
                if not has_null and row_count > 0:
                    print(f"Using existing processed events from {processed_rel_path} ({row_count} records).")
                    return processed_path
        except Exception as e:
            print(f"Warning: Could not validate existing processed events file: {e}")

    # Otherwise, resolve missing amounts from images.csv and image_cache.json / ImageParser
    print("Resolving missing financial event amounts...")
    with open(events_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        event_rows = list(reader)

    # Build mapping from related_event_id to image_id
    event_to_image: Dict[str, str] = {}
    if images_path.exists():
        with open(images_path, mode="r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rel_ev = row.get("related_event_id", "").strip()
                img_id = row.get("image_id", "").strip()
                if rel_ev and img_id:
                    event_to_image[rel_ev] = img_id

    # Load local image cache
    cache: Dict[str, dict] = {}
    if cache_path.exists():
        try:
            with open(cache_path, mode="r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load image cache: {e}")

    # Identify missing amounts and fill them
    missing_events: List[str] = []
    filled_count = 0

    for row in event_rows:
        amt_str = row.get("amount", "").strip()
        if not amt_str:
            ev_id = row["event_id"].strip()
            img_id = event_to_image.get(ev_id)
            extracted_amt = None
            if img_id and img_id in cache:
                val = cache[img_id].get("amount")
                if val is not None:
                    try:
                        extracted_amt = float(val)
                    except (ValueError, TypeError):
                        pass

            if extracted_amt is not None:
                row["amount"] = str(extracted_amt)
                filled_count += 1
            else:
                missing_events.append(ev_id)

    # If any still missing and ImageParser can be used, attempt fallback extraction
    if missing_events:
        try:
            import pandas as pd
            from image_parser import ImageParser
            parser = ImageParser(media_dir=str(repo_root / "dataset" / "media" / "images"))
            images_df = pd.read_csv(images_path) if images_path.exists() else None
            for row in event_rows:
                if not row.get("amount", "").strip():
                    ev_id = row["event_id"].strip()
                    extracted = parser.get_amount_for_event(ev_id, images_df) if images_df is not None else None
                    if extracted is not None:
                        row["amount"] = str(extracted)
                        filled_count += 1
        except Exception as e:
            print(f"Note: Online image parser not active ({e}); checked local cache.")

    # Save to processed events path
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    with open(processed_path, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(event_rows)

    print(f"Saved processed events to {processed_rel_path} ({filled_count} amounts resolved).")
    return processed_path


def copy_or_maintain_usage_report(repo_root: Path) -> None:
    """Ensure evaluation/usage_report.md exists if available."""
    src = repo_root / "code" / "evaluation" / "usage_report.md"
    dst = repo_root / "evaluation" / "usage_report.md"
    if src.exists() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def run_pipeline() -> None:
    """Execute the full end-to-end financial affordability pipeline."""
    repo_root = _REPO_ROOT
    print("=" * 60)
    print("BUY OR WAIT? -- Financial Affordability Decision Pipeline")
    print(f"Repository root: {repo_root}")
    print("=" * 60)

    # 1. Resolve missing financial event amounts via existing image parser / cache
    print("\n[Step 1/5] Resolving financial events & image data...")
    processed_events_path = resolve_financial_events(repo_root)

    # 2. Process message amendments
    messages_path = repo_root / "dataset" / "messages.csv"
    if not messages_path.exists():
        raise FileNotFoundError(f"Missing required dataset: {messages_path}")

    print("\n[Step 2/5] Ingesting and resolving message amendments...")
    amendments = build_amendments(str(messages_path))
    amendments_by_user = defaultdict(list)
    for a in amendments:
        amendments_by_user[a.user_id].append(a)
    print(f"Processed {len(amendments)} message amendments across {len(amendments_by_user)} users.")

    # 3. Load financial profiles, events, payment options, and requests
    print("\n[Step 3/5] Loading financial state and parameters...")
    profiles_path = repo_root / "dataset" / "financial_profiles.csv"
    options_path = repo_root / "dataset" / "request_payment_options.csv"
    requests_path = repo_root / "dataset" / "requests.csv"

    for p in (profiles_path, options_path, requests_path):
        if not p.exists():
            raise FileNotFoundError(f"Missing required dataset: {p}")

    profiles = load_profiles(profiles_path)
    events_by_user = load_events(processed_events_path)
    options_by_request = load_payment_options(options_path)
    requests = load_requests(requests_path)
    rates_service = get_default_service()

    print(f"Loaded {len(profiles)} profiles, {len(events_by_user)} user event histories, "
          f"{len(options_by_request)} payment options, and {len(requests)} evaluation requests.")

    # 4. Evaluate each purchase request
    print("\n[Step 4/5] Evaluating purchase requests through financial forecaster...")
    output_rows: List[dict] = []

    for req in requests:
        user_id = req.user_id
        profile = profiles[user_id]
        user_events = events_by_user.get(user_id, [])
        user_amendments = amendments_by_user.get(user_id, [])
        user_options = options_by_request.get(req.request_id, [])

        forecaster = FinancialForecaster(
            profile=profile,
            events=user_events,
            exchange_service=rates_service,
            amendments=user_amendments,
        )
        engine = AffordabilityEngine(profile, forecaster)
        eval_res: AffordabilityEvaluation = engine.evaluate_request(req, user_options)

        # Ensure safe bounds: 0 <= amount_safe_to_pay <= requested_amount
        safe_amt = max(0.0, min(float(req.requested_amount), float(eval_res.amount_safe_to_pay)))
        safe_amt_str = format_amount(safe_amt)

        output_rows.append({
            "request_id": eval_res.request_id,
            "amount_safe_to_pay": safe_amt_str,
            "affordability_status": eval_res.affordability_status,
            "recommended_payment_method": eval_res.recommended_payment_method,
            "payment_plan": eval_res.payment_plan,
            "earliest_date_for_full_payment": eval_res.earliest_date_for_full_payment,
            "spending_changes_needed": eval_res.spending_changes_needed,
            "decision_explanation": eval_res.decision_explanation,
        })

    print(f"Successfully evaluated {len(output_rows)} requests.")

    # 5. Serialize output to dataset/output.csv and root output.csv
    print("\n[Step 5/5] Writing output files...")
    dataset_output_path = repo_root / "dataset" / "output.csv"
    root_output_path = repo_root / "output.csv"

    # Write atomically to dataset/output.csv
    tmp_dataset_output = dataset_output_path.with_suffix(".tmp")
    with open(tmp_dataset_output, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)
    tmp_dataset_output.replace(dataset_output_path)
    print(f"Generated {dataset_output_path} ({len(output_rows)} rows).")

    # Also maintain root output.csv as specified in repository README
    shutil.copy2(dataset_output_path, root_output_path)
    print(f"Generated {root_output_path} ({len(output_rows)} rows).")

    # Ensure evaluation usage report is in place
    copy_or_maintain_usage_report(repo_root)

    print("\nPipeline execution complete!")


if __name__ == "__main__":
    run_pipeline()
