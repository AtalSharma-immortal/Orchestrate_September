"""
message_processor.py
---------------------
Message & text ingestion for the "Buy or Wait?" financial agent.

Responsibilities (per problem_statement.md and the master build spec):
  1. Load dataset/messages.csv into normalized Message objects.
  2. Classify each message and extract a structured Amendment — a fact the
     deterministic financial engine can apply (salary change, cancellation,
     confirmed/pending income, internal transfer, non-cash valuation, refund
     status, scam detection). Extraction is regex/rule-based and therefore
     deterministic and free (no LLM tokens spent on the common cases).
  3. Resolve conflicts across messages using the dataset's own precedence
     rules: explicit cancellation/amendment > newer record from the same
     source > settled evidence > safer interpretation when still ambiguous.
  4. Treat message_text as untrusted DATA ONLY. No instruction embedded in a
     message is ever executed or allowed to change program behavior — we only
     ever pull out (date, amount, currency, category) tuples that match known
     amendment patterns. Anything else is ignored.

Public entry points:
    load_messages(path) -> List[Message]
    process_messages(messages) -> List[Amendment]              (extract, no resolution)
    build_amendments(path) -> List[Amendment]                   (load + extract + resolve)
    amendments_to_csv(amendments, out_path)

Integration contract for teammates building financial_state.py /
event_reconciler.py:
    Consume the *resolved* list of Amendment objects (see conflict_resolution
    below). Each Amendment carries an `amendment_type`; switch on that enum
    and apply the corresponding effect to the recurring-income /ledger model.
    Do not re-parse message_text downstream — if a new amendment type is
    needed, extend this module instead, so classification stays in one place.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Optional

try:
    from .models import Amendment, AmendmentType, Message, SourceType
except (ImportError, ValueError):
    from models import Amendment, AmendmentType, Message, SourceType


# ---------------------------------------------------------------------------
# 1. Loading
# ---------------------------------------------------------------------------

def _parse_ts(value: str) -> datetime:
    value = value.strip()
    # Accept full ISO timestamps or bare dates; be liberal, since we don't
    # control upstream formatting.
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    # Last resort: fromisoformat handles most remaining ISO variants,
    # including offsets like +00:00.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_messages(path: str) -> List[Message]:
    messages: List[Message] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            source_raw = (row.get("source_type") or "").strip().lower()
            try:
                source = SourceType(source_raw)
            except ValueError:
                source = SourceType.UNKNOWN
            messages.append(
                Message(
                    message_id=row["message_id"].strip(),
                    user_id=row["user_id"].strip(),
                    request_id=(row.get("request_id") or "").strip() or None,
                    related_event_id=(row.get("related_event_id") or "").strip() or None,
                    sent_at=_parse_ts(row["sent_at"]),
                    source_type=source,
                    message_text=row.get("message_text") or "",
                )
            )
    return messages


# ---------------------------------------------------------------------------
# 2. Deterministic entity extraction helpers
# ---------------------------------------------------------------------------

_CURRENCY_AMOUNT_RE = re.compile(
    r"\b(INR|ZAR|IDR|USD|EUR)\s?([0-9][0-9,]*(?:\.[0-9]+)?)\b", re.IGNORECASE
)
# Also catch amount-then-currency, e.g. "1037.52 EUR"
_AMOUNT_CURRENCY_RE = re.compile(
    r"\b([0-9][0-9,]*(?:\.[0-9]+)?)\s?(INR|ZAR|IDR|USD|EUR)\b", re.IGNORECASE
)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_EVENT_ID_RE = re.compile(r"\b(event_\d+)\b", re.IGNORECASE)


def extract_amount_currency(text: str) -> Optional[tuple]:
    """Returns (amount: float, currency: str) for the first monetary mention, or None."""
    m = _CURRENCY_AMOUNT_RE.search(text)
    if m:
        currency, amount = m.group(1), m.group(2)
    else:
        m = _AMOUNT_CURRENCY_RE.search(text)
        if not m:
            return None
        amount, currency = m.group(1), m.group(2)
    try:
        return float(amount.replace(",", "")), currency.upper()
    except ValueError:
        return None


def extract_all_dates(text: str) -> List[date]:
    out = []
    for d in _DATE_RE.findall(text):
        try:
            out.append(datetime.strptime(d, "%Y-%m-%d").date())
        except ValueError:
            continue
    return out


def extract_event_ids(text: str) -> List[str]:
    return [m.lower() for m in _EVENT_ID_RE.findall(text)]


# ---------------------------------------------------------------------------
# 3. Keyword banks (English + Indonesian) — pattern level only, no per-term
#    annotation is exposed outside this module.
# ---------------------------------------------------------------------------

_KW = {
    "scam": [
        "prize", "lottery", "winnings", "claim your", "processing fee",
        "release fee", "you have won", "congratulations you",
        "hadiah", "menang", "undian", "biaya proses", "biaya pencairan",
    ],
    "internal_transfer": [
        "internal transfer", "between your accounts", "your own account",
        "own accounts", "self-transfer", "transfer internal",
        "antar rekening anda", "rekening pribadi anda",
    ],
    "event_cancelled": [
        "cancelled", "canceled", "voided", "reversed", "authorization was cancelled",
        "dibatalkan", "dibatalkan otomatis",
    ],
    "event_amended": [
        "increased by", "revised to", "amended to", "new amount is",
        "updated to", "rent has increased", "fee has increased",
        "direvisi menjadi", "naik menjadi",
    ],
    "non_cash_valuation": [
        "portfolio value", "valuation", "unrealized", "market value increased",
        "market value decreased", "no units were sold", "no units sold",
        "nilai portofolio", "belum direalisasikan",
    ],
    "investment_sale_settled": [
        "sale proceeds", "proceeds have settled", "proceeds settled",
        "units sold", "investment sale", "sale has settled",
        "hasil penjualan investasi telah cair", "dana hasil penjualan",
    ],
    "refund_pending": [
        "refund is processing", "refund is being processed", "refund pending",
        "still processing your refund", "is still processing", "refund is still processing",
        "pengembalian dana sedang diproses", "belum selesai",
    ],
    "refund_settled": [
        "refund completed", "refund has been credited", "refund settled",
        "refund issued", "telah dikreditkan", "pengembalian dana selesai",
    ],
    "salary_end": [
        "employment has ended", "contract has ended", "last working day",
        "seasonal contract concluded", "no longer employed", "role has ended",
        "kontrak berakhir", "hubungan kerja berakhir", "pemutusan hubungan kerja",
        "telah berakhir",
    ],
    "salary_temporary": [
        "temporarily reduced", "temporary reduction", "for the next", "until further notice",
        "sementara dikurangi", "untuk sementara waktu",
    ],
    "salary_date_change": [
        "salary date has changed", "replaces the previous date", "new payroll date",
        "updated payment date", "payroll date has been updated",
        "menggantikan tanggal sebelumnya", "tanggal gaji telah berubah",
    ],
    "salary_change": [
        "salary increases", "salary has increased", "new salary", "raise effective",
        "promotion effective", "compensation increase",
        "salary is reduced to", "next salary is reduced to", "reduced to", "salary reduced to",
        "gaji naik", "kenaikan gaji", "gaji baru", "naik menjadi", "gaji anda naik",
        "gaji dikurangi menjadi", "gaji turun menjadi",
    ],
    "income_excluded": [
        "not yet approved", "remains pending", "pending approval",
        "can change until the payout is closed", "still pending", "not yet confirmed",
        "belum disetujui", "masih tertunda", "belum dikonfirmasi",
    ],
    "income_confirmed": [
        "invoice approved", "payout confirmed", "settlement expected on",
        "has been approved and will settle", "confirmed salary", "bonus approved",
        "disetujui dan akan", "telah disetujui",
    ],
}


def _has_any(text: str, keys: Iterable[str]) -> Optional[str]:
    low = text.lower()
    for k in keys:
        if k in low:
            return k
    return None


# ---------------------------------------------------------------------------
# 4. Classification + extraction (deterministic)
# ---------------------------------------------------------------------------

def classify_message(msg: Message) -> Amendment:
    """
    Rule-based classification. Order matters: highest-risk / most-specific
    patterns are checked first so a message can't be reframed into a more
    favorable category by incidental wording.
    """
    text = msg.message_text
    amt_cur = extract_amount_currency(text)
    dates = extract_all_dates(text)
    event_ids = extract_event_ids(text)
    primary_event = msg.related_event_id or (event_ids[0] if event_ids else None)
    effective = dates[0] if dates else None

    def make(atype: AmendmentType, evidence: str, **kw) -> Amendment:
        return Amendment(
            message_id=msg.message_id,
            user_id=msg.user_id,
            amendment_type=atype,
            sent_at=msg.sent_at,
            request_id=msg.request_id,
            related_event_id=primary_event,
            evidence_span=evidence,
            **kw,
        )

    # --- 1. Scam / fraud pattern: never counts as income, regardless of amount ---
    hit = _has_any(text, _KW["scam"])
    if hit:
        return make(AmendmentType.SCAM_EXCLUDED, hit,
                     notes="Prize/fee pattern detected; not counted as income under any circumstance.")

    # --- 2. Internal transfer: net to zero ---
    hit = _has_any(text, _KW["internal_transfer"])
    if hit and msg.source_type == SourceType.BANK:
        return make(AmendmentType.INTERNAL_TRANSFER, hit,
                     notes="Matching debit/credit between the user's own accounts; ignored for net cash flow.")

    # --- 3. Explicit corrections to a specific financial event ---
    if primary_event:
        hit = _has_any(text, _KW["event_cancelled"])
        if hit:
            return make(AmendmentType.EVENT_CANCELLED, hit)

        hit = _has_any(text, _KW["event_amended"])
        if hit:
            amount, currency = (amt_cur or (None, None))
            return make(
                AmendmentType.EVENT_AMENDED, hit,
                amount=amount, currency=currency,
                effective_date=effective,
            )

    # --- 4. Investments: valuation change (non-cash) vs. settled sale (cash) ---
    hit = _has_any(text, _KW["non_cash_valuation"])
    if hit:
        return make(AmendmentType.NON_CASH_EXCLUDED, hit,
                     notes="Unrealized valuation change; excluded from liquid cash balance.")

    hit = _has_any(text, _KW["investment_sale_settled"])
    if hit:
        amount, currency = (amt_cur or (None, None))
        return make(AmendmentType.INCOME_CONFIRMED, hit,
                     amount=amount, currency=currency, category="investment_sale",
                     effective_date=effective)

    # --- 5. Refunds ---
    hit = _has_any(text, _KW["refund_pending"])
    if hit:
        return make(AmendmentType.REFUND_PENDING, hit,
                     notes="Refund not yet settled; not counted as available cash.")

    hit = _has_any(text, _KW["refund_settled"])
    if hit:
        amount, currency = (amt_cur or (None, None))
        return make(AmendmentType.REFUND_SETTLED, hit,
                     amount=amount, currency=currency, category="refund",
                     effective_date=effective)

    # --- 6. Salary / employment-income family ---
    hit = _has_any(text, _KW["salary_end"])
    if hit:
        return make(AmendmentType.SALARY_END, hit,
                     effective_date=effective, category="salary")

    hit = _has_any(text, _KW["salary_date_change"])
    if hit:
        return make(AmendmentType.SALARY_DATE_CHANGE, hit,
                     effective_date=effective, category="salary")

    hit = _has_any(text, _KW["salary_temporary"])
    if hit:
        amount, currency = (amt_cur or (None, None))
        end_date = dates[1] if len(dates) > 1 else None
        return make(AmendmentType.SALARY_TEMPORARY, hit,
                     amount=amount, currency=currency, category="salary",
                     effective_date=effective, end_date=end_date)

    hit = _has_any(text, _KW["salary_change"])
    if hit:
        amount, currency = (amt_cur or (None, None))
        eff_date = effective or msg.sent_at.date()
        return make(AmendmentType.SALARY_CHANGE, hit,
                     amount=amount, currency=currency, category="salary",
                     effective_date=eff_date)

    # --- 7. Generic confirmed / pending income (bonuses, commissions, invoices) ---
    hit = _has_any(text, _KW["income_excluded"])
    if hit:
        return make(AmendmentType.INCOME_EXCLUDED, hit,
                     notes="Pending/unapproved amount; not counted until confirmed.")

    hit = _has_any(text, _KW["income_confirmed"])
    if hit:
        amount, currency = (amt_cur or (None, None))
        return make(AmendmentType.INCOME_CONFIRMED, hit,
                     amount=amount, currency=currency,
                     effective_date=effective)

    # --- 8. Nothing matched deterministically ---
    return make(AmendmentType.UNRESOLVED, "",
                 amount=(amt_cur[0] if amt_cur else None),
                 currency=(amt_cur[1] if amt_cur else None),
                 effective_date=effective,
                 confidence=0.0,
                 notes="No deterministic rule matched; route to manual/LLM review before use.")


def process_messages(messages: Iterable[Message]) -> List[Amendment]:
    return [classify_message(m) for m in messages]


# ---------------------------------------------------------------------------
# 5. Conflict resolution
#
# Precedence (per problem_statement.md "Choosing Between Safe Plans" /
# "When records conflict"):
#   1. An explicit cancellation, settlement, or amendment
#   2. A newer record from the same source
#   3. A settled event over an estimate or forecast
#   4. The financially safer interpretation when still ambiguous
# ---------------------------------------------------------------------------

_EXPLICIT_TYPES = {
    AmendmentType.EVENT_CANCELLED,
    AmendmentType.EVENT_AMENDED,
    AmendmentType.SALARY_DATE_CHANGE,
    AmendmentType.SALARY_END,
    AmendmentType.REFUND_SETTLED,
}

# Amendments that key off a single recurring channel per user (only the
# latest, chronologically, should remain active for a given effective window).
_SALARY_STREAM_TYPES = {
    AmendmentType.SALARY_CHANGE,
    AmendmentType.SALARY_TEMPORARY,
    AmendmentType.SALARY_END,
    AmendmentType.SALARY_DATE_CHANGE,
}


def _group_key(a: Amendment) -> tuple:
    if a.related_event_id:
        return ("event", a.user_id, a.related_event_id)
    if a.amendment_type in _SALARY_STREAM_TYPES:
        return ("salary", a.user_id)
    # Everything else (one-off income, refunds not tied to an event id, etc.)
    # is independent per message — no cross-message collapsing needed.
    return ("standalone", a.message_id)


def resolve_conflicts(amendments: List[Amendment]) -> List[Amendment]:
    """
    Collapse amendments that describe the same underlying entity (one
    financial event, or one user's salary stream) down to the set that should
    actually be applied, using the dataset's stated precedence rules.

    - For a given financial event: an explicit cancellation/amendment always
      wins over an older/softer mention; among same-strength amendments, the
      newest sent_at wins.
    - For a user's salary stream: SALARY_END and SALARY_DATE_CHANGE are
      "replacement" facts — only the latest one is kept. SALARY_CHANGE and
      SALARY_TEMPORARY can coexist (a temporary dip can sit inside a longer
      permanent-salary timeline), so both are kept unless they contradict
      (see tests) in which case the newer message wins.
    """
    groups: dict = defaultdict(list)
    for a in amendments:
        groups[_group_key(a)].append(a)

    resolved: List[Amendment] = []
    for key, group in groups.items():
        if len(group) == 1 or key[0] == "standalone":
            resolved.extend(group)
            continue

        if key[0] == "event":
            # Prefer explicit cancellation/amendment; among ties, newest wins.
            def rank(a: Amendment):
                is_explicit = a.amendment_type in _EXPLICIT_TYPES
                return (is_explicit, a.sent_at)
            group.sort(key=rank, reverse=True)
            resolved.append(group[0])
            continue

        if key[0] == "salary":
            # Replacement-style facts: keep only the newest per amendment_type
            # bucket among {SALARY_END, SALARY_DATE_CHANGE}; SALARY_CHANGE and
            # SALARY_TEMPORARY are additive (kept, newest-first) unless they
            # share the exact same effective_date, in which case newest wins.
            by_type: dict = defaultdict(list)
            for a in group:
                by_type[a.amendment_type].append(a)

            for atype in (AmendmentType.SALARY_END, AmendmentType.SALARY_DATE_CHANGE):
                if atype in by_type:
                    newest = max(by_type[atype], key=lambda a: a.sent_at)
                    resolved.append(newest)

            for atype in (AmendmentType.SALARY_CHANGE, AmendmentType.SALARY_TEMPORARY):
                items = by_type.get(atype, [])
                if not items:
                    continue
                by_effective: dict = defaultdict(list)
                for a in items:
                    by_effective[a.effective_date].append(a)
                for eff, same_date_items in by_effective.items():
                    resolved.append(max(same_date_items, key=lambda a: a.sent_at))
            continue

        resolved.extend(group)

    resolved.sort(key=lambda a: (a.user_id, a.sent_at))
    return resolved


# ---------------------------------------------------------------------------
# 6. Convenience pipeline + CSV export (for testing / handoff to teammates)
# ---------------------------------------------------------------------------

def build_amendments(messages_csv_path: str) -> List[Amendment]:
    messages = load_messages(messages_csv_path)
    raw = process_messages(messages)
    return resolve_conflicts(raw)


def amendments_to_csv(amendments: List[Amendment], out_path: str) -> None:
    fieldnames = [
        "message_id", "user_id", "amendment_type", "sent_at", "request_id",
        "related_event_id", "effective_date", "end_date", "amount", "currency",
        "category", "confidence", "evidence_span", "notes",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for a in amendments:
            writer.writerow(a.to_dict())


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "dataset/messages.csv"
    dst = sys.argv[2] if len(sys.argv) > 2 else "amendments.csv"
    result = build_amendments(src)
    amendments_to_csv(result, dst)
    print(f"Processed {len(result)} resolved amendments -> {dst}")
