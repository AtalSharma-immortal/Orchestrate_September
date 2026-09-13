"""Affordability evaluation, payment plan formulation, and decision ranking engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from .forecast import FinancialForecaster, ProjectedCashEvent, format_date, parse_date
from .models import (
    AffordabilityEvaluation,
    FinancialProfile,
    PaymentOption,
    PaymentScheduleItem,
    PurchaseRequest,
    SpendingChange,
)
from .spending_adjustments import SpendingAdjustmentEngine


def format_amount(amt: float) -> str:
    """Format numeric amount to clean string (integer if exact, else 2 decimals)."""
    if abs(amt - round(amt)) < 1e-6:
        return str(int(round(amt)))
    return f"{amt:.2f}".rstrip("0").rstrip(".")


def format_currency_amount(curr: str, amt: float) -> str:
    """Format amount with currency code and comma separators."""
    if abs(amt - round(amt)) < 1e-6:
        val_str = f"{int(round(amt)):,}"
    else:
        val_str = f"{amt:,.2f}"
    return f"{curr} {val_str}"


class CandidatePlan:
    """A candidate payment strategy evaluated for safety."""

    def __init__(
        self,
        method: str,
        status: str,
        payments: List[PaymentScheduleItem],
        spending_changes: List[SpendingChange],
        total_cost: float,
        start_date: str,
        completion_date: str,
        payment_option_id: str = "zzz",
    ) -> None:
        self.method = method
        self.status = status
        self.payments = payments
        self.spending_changes = spending_changes
        self.total_cost = total_cost
        self.start_date = start_date
        self.completion_date = completion_date
        self.payment_option_id = payment_option_id

    @property
    def plan_str(self) -> str:
        if not self.payments:
            return "none"
        return "|".join(f"{p.payment_date}:{format_amount(p.amount)}" for p in self.payments)

    @property
    def changes_str(self) -> str:
        if not self.spending_changes:
            return "none"
        return "|".join(c.to_output_str() for c in self.spending_changes)


class AffordabilityEngine:
    """Evaluates purchase requests deterministically against user cash-flow constraints."""

    def __init__(
        self,
        profile: FinancialProfile,
        forecaster: FinancialForecaster,
        adjustment_engine: Optional[SpendingAdjustmentEngine] = None,
    ) -> None:
        self.profile = profile
        self.forecaster = forecaster
        self.adj_engine = adjustment_engine or SpendingAdjustmentEngine(profile)

    def calculate_amount_safe_to_pay(
        self,
        request_date: str,
        timeline: List[ProjectedCashEvent],
        requested_amount: float,
    ) -> float:
        """Compute maximum safe payment today without spending changes."""
        min_bal, _, _ = self.forecaster.simulate(
            request_date_str=request_date,
            events=timeline,
            payments=None,
            adjustments=None,
        )
        headroom = min_bal - self.profile.minimum_balance_to_keep
        if headroom <= 0:
            return 0.0
        return round(min(requested_amount, headroom), 2)

    def find_earliest_full_payment_date(
        self,
        request_date: str,
        timeline: List[ProjectedCashEvent],
        requested_amount: float,
        desired_date: str,
    ) -> str:
        """Find first date when full payment is safe without optional spending changes."""
        req_dt = parse_date(request_date)
        max_dt = req_dt + timedelta(days=90)

        cur = req_dt
        while cur <= max_dt:
            cur_str = format_date(cur)
            single_payment = [PaymentScheduleItem(payment_date=cur_str, amount=requested_amount)]
            min_bal, _, _ = self.forecaster.simulate(
                request_date_str=request_date,
                events=timeline,
                payments=single_payment,
                adjustments=None,
            )
            if min_bal >= self.profile.minimum_balance_to_keep - 1e-4:
                return cur_str
            cur += timedelta(days=1)

        return ""

    def evaluate_request(
        self,
        request: PurchaseRequest,
        payment_options: List[PaymentOption],
    ) -> AffordabilityEvaluation:
        """Evaluate a purchase request and determine optimal recommendation."""
        timeline = self.forecaster.build_forecast_timeline(request.request_date)
        safe_today = self.calculate_amount_safe_to_pay(
            request.request_date,
            timeline,
            request.requested_amount,
        )
        earliest_full_date = self.find_earliest_full_payment_date(
            request.request_date,
            timeline,
            request.requested_amount,
            request.desired_completion_date,
        )

        desired_dt = parse_date(request.desired_completion_date)
        req_dt = parse_date(request.request_date)

        candidate_change_sets = self.adj_engine.generate_candidate_combinations(timeline, max_changes=3)

        safe_candidates: List[CandidatePlan] = []

        if "full_payment" in self.profile.payment_methods_user_will_consider:
            for changes in candidate_change_sets:
                pay_item = [PaymentScheduleItem(payment_date=request.request_date, amount=request.requested_amount)]
                min_b, _, _ = self.forecaster.simulate(
                    request_date_str=request.request_date,
                    events=timeline,
                    payments=pay_item,
                    adjustments=changes,
                )
                if min_b >= self.profile.minimum_balance_to_keep - 1e-4:
                    status = "affordable_now" if not changes else "affordable_with_plan"
                    safe_candidates.append(
                        CandidatePlan(
                            method="full_payment",
                            status=status,
                            payments=pay_item,
                            spending_changes=changes,
                            total_cost=request.requested_amount,
                            start_date=request.request_date,
                            completion_date=request.request_date,
                            payment_option_id="000",
                        )
                    )
                    if not changes:
                        break

        if "installments" in self.profile.payment_methods_user_will_consider:
            inst_options = [o for o in payment_options if o.payment_method == "installments"]
            for opt in inst_options:
                if self.profile.max_installment_months is not None:
                    freq = opt.payment_frequency_days or 30
                    duration_days = (opt.number_of_payments - 1) * freq
                    duration_months = max(1, round(duration_days / 30.0))
                    if duration_months > self.profile.max_installment_months:
                        continue

                sched: List[PaymentScheduleItem] = []
                first_dt = parse_date(opt.first_payment_date)
                freq_days = opt.payment_frequency_days or 30
                for i in range(opt.number_of_payments):
                    p_date = format_date(first_dt + timedelta(days=i * freq_days))
                    sched.append(PaymentScheduleItem(payment_date=p_date, amount=opt.payment_amount))

                min_b, _, _ = self.forecaster.simulate(
                    request_date_str=request.request_date,
                    events=timeline,
                    payments=sched,
                    adjustments=None,
                )
                if min_b >= self.profile.minimum_balance_to_keep - 1e-4:
                    safe_candidates.append(
                        CandidatePlan(
                            method="installments",
                            status="affordable_with_plan",
                            payments=sched,
                            spending_changes=[],
                            total_cost=opt.total_payable_amount,
                            start_date=opt.first_payment_date,
                            completion_date=sched[-1].payment_date,
                            payment_option_id=opt.payment_option_id,
                        )
                    )

        if (
            request.allows_partial_payment
            and "partial_payment" in self.profile.payment_methods_user_will_consider
            and 0 < safe_today < request.requested_amount
            and earliest_full_date
            and parse_date(earliest_full_date) <= desired_dt
        ):
            remaining = round(request.requested_amount - safe_today, 2)
            partial_sched = [
                PaymentScheduleItem(payment_date=request.request_date, amount=safe_today),
                PaymentScheduleItem(payment_date=earliest_full_date, amount=remaining),
            ]
            min_b, _, _ = self.forecaster.simulate(
                request_date_str=request.request_date,
                events=timeline,
                payments=partial_sched,
                adjustments=None,
            )
            if min_b >= self.profile.minimum_balance_to_keep - 1e-4:
                safe_candidates.append(
                    CandidatePlan(
                        method="partial_payment",
                        status="affordable_with_plan",
                        payments=partial_sched,
                        spending_changes=[],
                        total_cost=request.requested_amount,
                        start_date=request.request_date,
                        completion_date=earliest_full_date,
                        payment_option_id="001",
                    )
                )

        if (
            "full_payment" in self.profile.payment_methods_user_will_consider
            and earliest_full_date
            and earliest_full_date != request.request_date
            and parse_date(earliest_full_date) <= desired_dt
        ):
            wait_sched = [PaymentScheduleItem(payment_date=earliest_full_date, amount=request.requested_amount)]
            min_b, _, _ = self.forecaster.simulate(
                request_date_str=request.request_date,
                events=timeline,
                payments=wait_sched,
                adjustments=None,
            )
            if min_b >= self.profile.minimum_balance_to_keep - 1e-4:
                safe_candidates.append(
                    CandidatePlan(
                        method="wait",
                        status="affordable_later",
                        payments=wait_sched,
                        spending_changes=[],
                        total_cost=request.requested_amount,
                        start_date=earliest_full_date,
                        completion_date=earliest_full_date,
                        payment_option_id="999",
                    )
                )

        def _opt_sort_key(opt_id: str) -> Tuple[int, int, str]:
            digits = "".join(c for c in opt_id if c.isdigit())
            if digits:
                return (0, int(digits), opt_id)
            return (1, 0, opt_id)

        def rank_key(p: CandidatePlan):
            on_time = 0 if parse_date(p.completion_date) <= desired_dt else 1
            num_changes = len(p.spending_changes)
            return (
                on_time,
                num_changes,
                p.total_cost,
                p.start_date,
                len(p.payments),
                _opt_sort_key(p.payment_option_id),
            )

        best_plan: Optional[CandidatePlan] = None
        if safe_candidates:
            safe_candidates.sort(key=rank_key)
            best_plan = safe_candidates[0]

        if best_plan is not None:
            explanation = self.build_explanation(request, best_plan, safe_today)
            return AffordabilityEvaluation(
                request_id=request.request_id,
                amount_safe_to_pay=safe_today,
                affordability_status=best_plan.status,
                recommended_payment_method=best_plan.method,
                payment_plan=best_plan.plan_str,
                earliest_date_for_full_payment=earliest_full_date,
                spending_changes_needed=best_plan.changes_str,
                decision_explanation=explanation,
            )
        else:
            explanation = self.build_not_affordable_explanation(request, safe_today)
            return AffordabilityEvaluation(
                request_id=request.request_id,
                amount_safe_to_pay=safe_today,
                affordability_status="not_affordable",
                recommended_payment_method="not_recommended",
                payment_plan="none",
                earliest_date_for_full_payment=earliest_full_date if earliest_full_date else "",
                spending_changes_needed="none",
                decision_explanation=explanation,
            )

    def build_explanation(
        self,
        request: PurchaseRequest,
        plan: CandidatePlan,
        safe_today: float,
    ) -> str:
        """Generate concise, factual decision explanation."""
        curr = self.profile.home_currency
        req_fmt = format_currency_amount(curr, request.requested_amount)
        min_fmt = format_currency_amount(curr, self.profile.minimum_balance_to_keep)

        if plan.status == "affordable_now":
            return f"Pay {req_fmt} today. This leaves at least {min_fmt} available over the next 90 days."

        if plan.method == "installments":
            n = len(plan.payments)
            inst_amt = format_currency_amount(curr, plan.payments[0].amount)
            start_dt = parse_date(plan.start_date).strftime("%d %B %Y").lstrip("0")
            return f"Use {n} installments of {inst_amt}, starting {start_dt}. This leaves at least {min_fmt} available."

        if plan.method == "partial_payment":
            p1 = format_currency_amount(curr, plan.payments[0].amount)
            p2 = format_currency_amount(curr, plan.payments[1].amount)
            d2 = parse_date(plan.payments[1].payment_date).strftime("%d %B %Y").lstrip("0")
            return f"Pay {p1} today and the remaining {p2} on {d2}. This completes the full request and keeps the {min_fmt} minimum protected."

        if plan.method == "wait":
            pay_dt = parse_date(plan.start_date).strftime("%d %B %Y").lstrip("0")
            return f"Pay {req_fmt} in full on {pay_dt}. Paying earlier would take the balance below the {min_fmt} minimum."

        if plan.spending_changes:
            changes_desc = []
            for c in plan.spending_changes:
                if c.action == "stop":
                    changes_desc.append(f"stop {c.event_id}")
                else:
                    changes_desc.append(f"reduce {c.event_id}")
            actions = " and ".join(changes_desc)
            return f"Apply spending adjustments ({actions}), then pay {req_fmt} today. This leaves at least {min_fmt} available."

        return f"Payment of {req_fmt} is safe. This keeps the {min_fmt} minimum protected."

    def build_not_affordable_explanation(
        self,
        request: PurchaseRequest,
        safe_today: float,
    ) -> str:
        """Generate concise explanation for not_affordable requests."""
        curr = self.profile.home_currency
        req_fmt = format_currency_amount(curr, request.requested_amount)
        min_fmt = format_currency_amount(curr, self.profile.minimum_balance_to_keep)
        d_str = parse_date(request.desired_completion_date).strftime("%d %B %Y").lstrip("0")

        return f"Do not make this payment by {d_str}. None of the available options keeps the {min_fmt} minimum protected."
