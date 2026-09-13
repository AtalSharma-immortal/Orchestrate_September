"""Unit test suite for financial forecasting, affordability evaluation, and plan safety."""

import unittest
from pathlib import Path
import sys

_code_dir = Path(__file__).resolve().parent.parent
if str(_code_dir) not in sys.path:
    sys.path.insert(0, str(_code_dir))

from finance.affordability import AffordabilityEngine
from finance.exchange_rates import ExchangeRateNotFoundError, ExchangeRateService
from finance.forecast import FinancialForecaster, ProjectedCashEvent
from finance.loader import (
    load_events,
    load_payment_options,
    load_profiles,
    load_requests,
)
from finance.models import (
    FinancialEvent,
    FinancialProfile,
    PaymentOption,
    PaymentScheduleItem,
    PurchaseRequest,
    SpendingChange,
)
from finance.spending_adjustments import SpendingAdjustmentEngine


class TestFinancialEngine(unittest.TestCase):
    """Comprehensive unit tests for the financial engine components."""

    def setUp(self):
        self.profile = FinancialProfile(
            user_id="user_test",
            home_currency="USD",
            current_available_balance=5000.0,
            minimum_balance_to_keep=1000.0,
            financial_priorities=["education", "debt_repayment"],
            expense_categories_to_protect={"rent", "groceries"},
            expense_categories_user_is_willing_to_reduce={"dining"},
            expense_categories_user_is_willing_to_stop={"streaming"},
            payment_methods_user_will_consider={"full_payment", "installments", "partial_payment"},
            max_installment_months=6,
        )
        self.forecaster = FinancialForecaster(self.profile, events=[])
        self.engine = AffordabilityEngine(self.profile, self.forecaster)

    def test_full_payment_safely_affordable_today(self):
        """Verify full_payment today produces affordable_now when balance remains above minimum."""
        req = PurchaseRequest(
            request_id="req_test_01",
            user_id="user_test",
            request_date="2025-01-01",
            request_type="purchase",
            requested_amount=1500.0,
            desired_completion_date="2025-01-15",
            allows_partial_payment=True,
            request_text="Can I buy this gadget?",
        )
        result = self.engine.evaluate_request(req, payment_options=[])
        self.assertEqual(result.affordability_status, "affordable_now")
        self.assertEqual(result.recommended_payment_method, "full_payment")
        self.assertEqual(result.payment_plan, "2025-01-01:1500")
        self.assertEqual(result.earliest_date_for_full_payment, "2025-01-01")
        self.assertEqual(result.spending_changes_needed, "none")

    def test_full_payment_unsafe_today_but_affordable_later(self):
        """Verify wait / affordable_later when income settles before completion date."""
        self.profile.current_available_balance = 1500.0
        events = [
            FinancialEvent(
                event_id="inc_01",
                user_id="user_test",
                event_type="income",
                description="Salary",
                category="salary",
                direction="credit",
                amount=2000.0,
                currency="USD",
                event_date="2025-01-10",
                settlement_date="2025-01-10",
                status="scheduled",
            )
        ]
        forecaster = FinancialForecaster(self.profile, events)
        engine = AffordabilityEngine(self.profile, forecaster)

        req = PurchaseRequest(
            request_id="req_test_02",
            user_id="user_test",
            request_date="2025-01-01",
            request_type="purchase",
            requested_amount=1200.0,
            desired_completion_date="2025-01-15",
            allows_partial_payment=False,
            request_text="Buy later",
        )
        result = engine.evaluate_request(req, payment_options=[])
        self.assertEqual(result.affordability_status, "affordable_later")
        self.assertEqual(result.recommended_payment_method, "wait")
        self.assertEqual(result.earliest_date_for_full_payment, "2025-01-10")
        self.assertEqual(result.payment_plan, "2025-01-10:1200")

    def test_installments_recommendation(self):
        """Verify installment option selection when full payment is unsafe today."""
        self.profile.current_available_balance = 1500.0
        options = [
            PaymentOption(
                payment_option_id="opt_inst_01",
                request_id="req_test_03",
                payment_method="installments",
                payment_amount=400.0,
                number_of_payments=3,
                first_payment_date="2025-01-05",
                payment_frequency_days=30,
                financing_fee=0.0,
                total_payable_amount=1200.0,
            )
        ]
        events = [
            FinancialEvent(
                event_id="inc_02",
                user_id="user_test",
                event_type="income",
                description="Monthly salary",
                category="salary",
                direction="credit",
                amount=2000.0,
                currency="USD",
                event_date="2025-01-15",
                settlement_date="2025-01-15",
                status="scheduled",
            )
        ]
        forecaster = FinancialForecaster(self.profile, events)
        engine = AffordabilityEngine(self.profile, forecaster)

        req = PurchaseRequest(
            request_id="req_test_03",
            user_id="user_test",
            request_date="2025-01-01",
            request_type="purchase",
            requested_amount=1200.0,
            desired_completion_date="2025-04-01",
            allows_partial_payment=False,
            request_text="Installment plan test",
        )
        result = engine.evaluate_request(req, options)
        self.assertEqual(result.affordability_status, "affordable_with_plan")
        self.assertEqual(result.recommended_payment_method, "installments")
        self.assertIn("2025-01-05:400", result.payment_plan)

    def test_partial_payment_plan(self):
        """Verify partial payment when allowed, safe > 0, and completion date is respected."""
        self.profile.current_available_balance = 1500.0
        events = [
            FinancialEvent(
                event_id="inc_03",
                user_id="user_test",
                event_type="income",
                description="Salary",
                category="salary",
                direction="credit",
                amount=2000.0,
                currency="USD",
                event_date="2025-01-15",
                settlement_date="2025-01-15",
                status="scheduled",
            )
        ]
        forecaster = FinancialForecaster(self.profile, events)
        engine = AffordabilityEngine(self.profile, forecaster)

        req = PurchaseRequest(
            request_id="req_test_04",
            user_id="user_test",
            request_date="2025-01-01",
            request_type="purchase",
            requested_amount=1200.0,
            desired_completion_date="2025-01-20",
            allows_partial_payment=True,
            request_text="Partial payment test",
        )
        result = engine.evaluate_request(req, payment_options=[])
        self.assertEqual(result.affordability_status, "affordable_with_plan")
        self.assertEqual(result.recommended_payment_method, "partial_payment")
        self.assertEqual(result.amount_safe_to_pay, 500.0)
        self.assertEqual(result.payment_plan, "2025-01-01:500|2025-01-15:700")

    def test_not_affordable(self):
        """Verify not_affordable when payment cannot complete safely within deadline."""
        self.profile.current_available_balance = 1100.0
        req = PurchaseRequest(
            request_id="req_test_05",
            user_id="user_test",
            request_date="2025-01-01",
            request_type="purchase",
            requested_amount=5000.0,
            desired_completion_date="2025-01-10",
            allows_partial_payment=False,
            request_text="Too expensive",
        )
        result = self.engine.evaluate_request(req, payment_options=[])
        self.assertEqual(result.affordability_status, "not_affordable")
        self.assertEqual(result.recommended_payment_method, "not_recommended")
        self.assertEqual(result.payment_plan, "none")

    def test_minimum_balance_protection(self):
        """Verify that the projected balance strictly respects minimum_balance_to_keep."""
        self.profile.current_available_balance = 2000.0
        self.profile.minimum_balance_to_keep = 1000.0
        timeline = self.forecaster.build_forecast_timeline("2025-01-01")
        safe = self.engine.calculate_amount_safe_to_pay("2025-01-01", timeline, 1500.0)
        self.assertEqual(safe, 1000.0)

    def test_protected_expenses_never_modified(self):
        """Verify that expenses in expense_categories_to_protect are never reduced or stopped."""
        ev = ProjectedCashEvent(
            event_id="rent_01",
            category="rent",
            description="Apartment rent",
            direction="debit",
            amount=1000.0,
            date_str="2025-01-05",
            flexibility="stoppable",
        )
        adj_engine = SpendingAdjustmentEngine(self.profile)
        self.assertFalse(adj_engine.is_modifiable(ev))
        self.assertEqual(len(adj_engine.get_candidate_changes_for_event(ev)), 0)

    def test_reducible_expenses(self):
        """Verify reducible expenses respect minimum_allowed_amount."""
        ev = ProjectedCashEvent(
            event_id="dining_01",
            category="dining",
            description="Weekly dining",
            direction="debit",
            amount=200.0,
            date_str="2025-01-05",
            flexibility="reducible",
            minimum_allowed_amount=120.0,
        )
        adj_engine = SpendingAdjustmentEngine(self.profile)
        self.assertTrue(adj_engine.is_modifiable(ev))
        changes = adj_engine.get_candidate_changes_for_event(ev)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].action, "reduce_to")
        self.assertEqual(changes[0].new_amount, 120.0)
        self.assertEqual(changes[0].to_output_str(), "reduce_to:dining_01:120")

    def test_stoppable_expenses(self):
        """Verify stoppable expenses can be stopped."""
        ev = ProjectedCashEvent(
            event_id="stream_01",
            category="streaming",
            description="Movie streaming",
            direction="debit",
            amount=25.0,
            date_str="2025-01-05",
            flexibility="stoppable",
        )
        adj_engine = SpendingAdjustmentEngine(self.profile)
        self.assertTrue(adj_engine.is_modifiable(ev))
        changes = adj_engine.get_candidate_changes_for_event(ev)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].action, "stop")
        self.assertEqual(changes[0].to_output_str(), "stop:stream_01")

    def test_pending_expenses_and_scheduled_income(self):
        """Verify pending debits are included while pending credits are ignored."""
        events = [
            FinancialEvent(
                event_id="deb_pend",
                user_id="user_test",
                event_type="expense",
                description="Pending charge",
                category="utilities",
                direction="debit",
                amount=150.0,
                currency="USD",
                event_date="2025-01-03",
                settlement_date="2025-01-05",
                status="pending",
            ),
            FinancialEvent(
                event_id="cred_pend",
                user_id="user_test",
                event_type="refund",
                description="Pending tax refund",
                category="other",
                direction="credit",
                amount=500.0,
                currency="USD",
                event_date="2025-01-03",
                settlement_date="2025-01-05",
                status="pending",
            ),
        ]
        forecaster = FinancialForecaster(self.profile, events)
        timeline = forecaster.build_forecast_timeline("2025-01-01")
        debits = [e for e in timeline if e.event_id == "deb_pend"]
        credits = [e for e in timeline if e.event_id == "cred_pend"]
        self.assertEqual(len(debits), 1)
        self.assertEqual(len(credits), 0)

    def test_cancelled_and_failed_events_ignored(self):
        """Verify cancelled, failed, and unrealized events do not affect cash flow."""
        events = [
            FinancialEvent(
                event_id="cancel_01",
                user_id="user_test",
                event_type="expense",
                description="Cancelled order",
                category="shopping",
                direction="debit",
                amount=300.0,
                currency="USD",
                event_date="2025-01-05",
                settlement_date="2025-01-05",
                status="cancelled",
            ),
            FinancialEvent(
                event_id="fail_01",
                user_id="user_test",
                event_type="expense",
                description="Failed transaction",
                category="utilities",
                direction="debit",
                amount=200.0,
                currency="USD",
                event_date="2025-01-06",
                settlement_date="2025-01-06",
                status="failed",
            ),
        ]
        forecaster = FinancialForecaster(self.profile, events)
        timeline = forecaster.build_forecast_timeline("2025-01-01")
        ids = {e.event_id for e in timeline}
        self.assertNotIn("cancel_01", ids)
        self.assertNotIn("fail_01", ids)

    def test_non_cash_investment_valuation(self):
        """Verify non-cash investment valuations do not impact cash flow."""
        events = [
            FinancialEvent(
                event_id="inv_val",
                user_id="user_test",
                event_type="investment_valuation",
                description="Portfolio Valuation",
                category="investment",
                direction="non_cash",
                amount=50000.0,
                currency="USD",
                event_date="2025-01-05",
                settlement_date="2025-01-05",
                status="settled",
            )
        ]
        forecaster = FinancialForecaster(self.profile, events)
        timeline = forecaster.build_forecast_timeline("2025-01-01")
        ids = {e.event_id for e in timeline}
        self.assertNotIn("inv_val", ids)

    def test_foreign_currency_event_conversion(self):
        """Verify foreign-currency events are converted using the dated rate."""
        events = [
            FinancialEvent(
                event_id="forex_01",
                user_id="user_test",
                event_type="expense",
                description="EUR charge",
                category="utilities",
                direction="debit",
                amount=100.0,
                currency="EUR",
                event_date="2024-03-15",
                settlement_date="2024-03-15",
                status="pending",
            )
        ]
        forecaster = FinancialForecaster(self.profile, events)
        same_curr_ev = FinancialEvent(
            event_id="usd_ev",
            user_id="user_test",
            event_type="expense",
            description="USD charge",
            category="utilities",
            direction="debit",
            amount=150.0,
            currency="USD",
            event_date="2024-03-15",
            settlement_date="2024-03-15",
            status="pending",
        )
        converted = forecaster.convert_event_amount(same_curr_ev)
        self.assertEqual(converted, 150.0)

    def test_user_payment_method_restrictions(self):
        """Verify payment methods excluded by the user are never recommended."""
        self.profile.payment_methods_user_will_consider = {"full_payment"}
        self.profile.current_available_balance = 1200.0
        self.profile.minimum_balance_to_keep = 1000.0

        options = [
            PaymentOption(
                payment_option_id="opt_excl_01",
                request_id="req_excl_01",
                payment_method="installments",
                payment_amount=100.0,
                number_of_payments=3,
                first_payment_date="2025-01-05",
                payment_frequency_days=30,
                financing_fee=0.0,
                total_payable_amount=300.0,
            )
        ]
        req = PurchaseRequest(
            request_id="req_excl_01",
            user_id="user_test",
            request_date="2025-01-01",
            request_type="purchase",
            requested_amount=300.0,
            desired_completion_date="2025-01-10",
            allows_partial_payment=True,
            request_text="Exclude installments test",
        )
        result = self.engine.evaluate_request(req, options)
        self.assertNotEqual(result.recommended_payment_method, "installments")
        self.assertNotEqual(result.recommended_payment_method, "partial_payment")

    def test_max_installment_months_restriction(self):
        """Verify installment options exceeding max_installment_months are rejected."""
        self.profile.max_installment_months = 3
        self.profile.payment_methods_user_will_consider = {"installments"}
        self.profile.current_available_balance = 10000.0

        long_option = PaymentOption(
            payment_option_id="opt_long",
            request_id="req_long",
            payment_method="installments",
            payment_amount=50.0,
            number_of_payments=6,
            first_payment_date="2025-01-05",
            payment_frequency_days=30,
            financing_fee=0.0,
            total_payable_amount=300.0,
        )
        req = PurchaseRequest(
            request_id="req_long",
            user_id="user_test",
            request_date="2025-01-01",
            request_type="purchase",
            requested_amount=300.0,
            desired_completion_date="2025-07-01",
            allows_partial_payment=False,
            request_text="Long installments",
        )
        result = self.engine.evaluate_request(req, [long_option])
        self.assertNotEqual(result.recommended_payment_method, "installments")

    def test_recurring_income_projected_across_forecast_months(self):
        """Verify recurring salary credits appear across future forecast months."""
        events = [
            FinancialEvent(
                event_id="sal_01",
                user_id="user_test",
                event_type="income",
                description="Payroll credit",
                category="salary",
                direction="credit",
                amount=3000.0,
                currency="USD",
                event_date="2025-01-15",
                settlement_date="2025-01-15",
                status="settled",
            ),
            FinancialEvent(
                event_id="sal_02",
                user_id="user_test",
                event_type="income",
                description="Payroll credit",
                category="salary",
                direction="credit",
                amount=3000.0,
                currency="USD",
                event_date="2025-02-15",
                settlement_date="2025-02-15",
                status="settled",
            ),
        ]
        forecaster = FinancialForecaster(self.profile, events)
        timeline = forecaster.build_forecast_timeline("2025-03-01")
        future_salaries = [e for e in timeline if e.direction == "credit" and e.category == "salary"]

        dates = [e.date_str for e in future_salaries]
        self.assertIn("2025-03-15", dates)
        self.assertIn("2025-04-15", dates)
        self.assertIn("2025-05-15", dates)
        for s in future_salaries:
            self.assertEqual(s.amount, 3000.0)
            self.assertTrue(s.is_recurring)

    def test_scheduled_income_not_double_counted(self):
        """Verify explicitly scheduled salary is preserved and not duplicated on the same date."""
        events = [
            FinancialEvent(
                event_id="sal_hist",
                user_id="user_test",
                event_type="income",
                description="Prorated first salary",
                category="salary",
                direction="credit",
                amount=1500.0,
                currency="USD",
                event_date="2025-02-15",
                settlement_date="2025-02-15",
                status="settled",
            ),
            FinancialEvent(
                event_id="sal_sched",
                user_id="user_test",
                event_type="income",
                description="Next confirmed salary",
                category="salary",
                direction="credit",
                amount=3000.0,
                currency="USD",
                event_date="2025-03-15",
                settlement_date="2025-03-15",
                status="scheduled",
            ),
        ]
        forecaster = FinancialForecaster(self.profile, events)
        timeline = forecaster.build_forecast_timeline("2025-03-01")

        march_credits = [e for e in timeline if e.date_str == "2025-03-15" and e.direction == "credit"]
        self.assertEqual(len(march_credits), 1)
        self.assertEqual(march_credits[0].event_id, "sal_sched")
        self.assertFalse(march_credits[0].is_recurring)

        future_credits = [e for e in timeline if e.date_str in ("2025-04-15", "2025-05-15") and e.direction == "credit"]
        self.assertEqual(len(future_credits), 2)
        for fc in future_credits:
            self.assertEqual(fc.amount, 3000.0)
            self.assertTrue(fc.is_recurring)

    def test_non_recurring_credits_not_projected(self):
        """Verify refunds, windfalls, and investment sales are never projected as recurring."""
        events = [
            FinancialEvent(
                event_id="ref_01",
                user_id="user_test",
                event_type="refund",
                description="Card refund",
                category="shopping",
                direction="credit",
                amount=200.0,
                currency="USD",
                event_date="2025-01-10",
                settlement_date="2025-01-10",
                status="settled",
            ),
            FinancialEvent(
                event_id="ref_02",
                user_id="user_test",
                event_type="refund",
                description="Card refund",
                category="shopping",
                direction="credit",
                amount=200.0,
                currency="USD",
                event_date="2025-02-10",
                settlement_date="2025-02-10",
                status="settled",
            ),
            FinancialEvent(
                event_id="win_01",
                user_id="user_test",
                event_type="windfall",
                description="Prize proceeds",
                category="windfall",
                direction="credit",
                amount=5000.0,
                currency="USD",
                event_date="2025-01-20",
                settlement_date="2025-01-20",
                status="settled",
            ),
        ]
        forecaster = FinancialForecaster(self.profile, events)
        timeline = forecaster.build_forecast_timeline("2025-03-01")
        future_credits = [e for e in timeline if e.direction == "credit"]
        self.assertEqual(len(future_credits), 0)

    def test_no_synthetic_spending_injected(self):
        events = [
            FinancialEvent(
                event_id=f"groc_{i}",
                user_id="user_test",
                event_type="expense",
                description="Weekly groceries",
                category="groceries",
                direction="debit",
                amount=100.0,
                currency="USD",
                event_date=f"2025-01-0{i+1}",
                settlement_date=f"2025-01-0{i+1}",
                status="settled",
            )
            for i in range(5)
        ] + [
            FinancialEvent(
                event_id=f"trans_{i}",
                user_id="user_test",
                event_type="expense",
                description="City transport",
                category="transport",
                direction="debit",
                amount=50.0,
                currency="USD",
                event_date=f"2025-01-1{i+1}",
                settlement_date=f"2025-01-1{i+1}",
                status="settled",
            )
            for i in range(5)
        ]
        forecaster = FinancialForecaster(self.profile, events)
        timeline = forecaster.build_forecast_timeline("2025-02-01")
        estimated_events = [e for e in timeline if e.description.startswith("Estimated ") or not e.event_id]
        self.assertEqual(len(estimated_events), 0)


class TestDatasetIntegration(unittest.TestCase):
    """Integration tests running the financial engine on actual dataset records."""

    @classmethod
    def setUpClass(cls):
        cls.profiles = load_profiles()
        cls.events = load_events()
        cls.options = load_payment_options()
        cls.requests = load_requests()

    def test_dataset_loading_completeness(self):
        """Verify all dataset tables are loaded and non-empty."""
        self.assertEqual(len(self.profiles), 275)
        self.assertEqual(len(self.events), 275)
        self.assertGreater(len(self.options), 0)
        self.assertGreater(len(self.requests), 0)

    def test_sample_requests_evaluation_runs(self):
        """Verify evaluate_request executes without unhandled exceptions on dataset requests."""
        first_req = self.requests[0]
        user_prof = self.profiles[first_req.user_id]
        user_ev = self.events.get(first_req.user_id, [])
        req_opts = self.options.get(first_req.request_id, [])

        forecaster = FinancialForecaster(user_prof, user_ev)
        engine = AffordabilityEngine(user_prof, forecaster)
        result = engine.evaluate_request(first_req, req_opts)

        self.assertIsNotNone(result)
        self.assertEqual(result.request_id, first_req.request_id)
        self.assertIn(result.affordability_status, {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"})
        self.assertIn(result.recommended_payment_method, {"full_payment", "partial_payment", "installments", "wait", "not_recommended"})
        self.assertTrue(0 <= result.amount_safe_to_pay <= first_req.requested_amount)


class TestAmendmentIntegration(unittest.TestCase):
    """Finance engine tests for Amendment-driven event modifications."""

    _ref_date = "2026-06-15"

    def _profile(self, balance=20000.0, currency="USD") -> "FinancialProfile":
        return FinancialProfile(
            user_id="test_user",
            home_currency=currency,
            current_available_balance=balance,
            minimum_balance_to_keep=500.0,
            financial_priorities=[],
            expense_categories_to_protect=set(),
            expense_categories_user_is_willing_to_reduce=set(),
            expense_categories_user_is_willing_to_stop=set(),
            payment_methods_user_will_consider={"full_payment"},
            max_installment_months=None,
        )

    def _salary_events(self, amount=3000.0, currency="USD"):
        """Two settled salary events establishing a recurring monthly pattern."""
        return [
            FinancialEvent(
                event_id="sal_1",
                user_id="test_user",
                event_type="income",
                category="salary",
                description="Monthly salary",
                direction="credit",
                status="settled",
                amount=amount,
                currency=currency,
                event_date="2026-04-25",
                settlement_date="2026-04-25",
                flexibility="fixed",
                minimum_allowed_amount=None,
            ),
            FinancialEvent(
                event_id="sal_2",
                user_id="test_user",
                event_type="income",
                category="salary",
                description="Monthly salary",
                direction="credit",
                status="settled",
                amount=amount,
                currency=currency,
                event_date="2026-05-25",
                settlement_date="2026-05-25",
                flexibility="fixed",
                minimum_allowed_amount=None,
            ),
        ]

    def _amendment(self, **kwargs):
        """Build a minimal Amendment using messages.models if available, else skip."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from messages.models import Amendment, AmendmentType
        except ImportError:
            self.skipTest("messages module not importable")

        from datetime import datetime as _dt
        defaults = dict(
            message_id="msg_test",
            user_id="test_user",
            amendment_type=AmendmentType.UNRESOLVED,
            sent_at=_dt(2026, 6, 1),
            request_id=None,
            related_event_id=None,
            effective_date=None,
            end_date=None,
            amount=None,
            currency="USD",
            category=None,
            confidence=1.0,
            evidence_span="",
            notes="",
        )
        defaults.update(kwargs)
        return Amendment(**defaults)

    def _AmendmentType(self):
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from messages.models import AmendmentType
            return AmendmentType
        except ImportError:
            self.skipTest("messages module not importable")

    def test_salary_change_replaces_recurring_amount(self):
        """SALARY_CHANGE: recurring projections on/after effective_date use new amount."""
        AT = self._AmendmentType()
        from datetime import date as _date
        am = self._amendment(
            amendment_type=AT.SALARY_CHANGE,
            effective_date=_date(2026, 7, 1),
            amount=4000.0,
            currency="USD",
            category="salary",
        )
        profile = self._profile()
        events = self._salary_events(amount=3000.0)
        forecaster = FinancialForecaster(profile, events, amendments=[am])
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        future_salary = [e for e in timeline if e.category == "salary" and e.is_recurring]
        before = [e for e in future_salary if e.date_str < "2026-07-01"]
        after = [e for e in future_salary if e.date_str >= "2026-07-01"]

        for e in before:
            self.assertAlmostEqual(e.amount, 3000.0, places=1,
                                   msg=f"Before effective_date should be 3000, got {e.amount} at {e.date_str}")
        for e in after:
            self.assertAlmostEqual(e.amount, 4000.0, places=1,
                                   msg=f"After effective_date should be 4000, got {e.amount} at {e.date_str}")

    def test_salary_temporary_bounded_window(self):
        """SALARY_TEMPORARY: within window uses temp amount; outside uses base amount."""
        AT = self._AmendmentType()
        from datetime import date as _date
        am = self._amendment(
            amendment_type=AT.SALARY_TEMPORARY,
            effective_date=_date(2026, 7, 1),
            end_date=_date(2026, 7, 31),
            amount=1500.0,
            currency="USD",
            category="salary",
        )
        profile = self._profile()
        events = self._salary_events(amount=3000.0)
        forecaster = FinancialForecaster(profile, events, amendments=[am])
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        salary_events = [e for e in timeline if e.category == "salary" and e.is_recurring]
        inside = [e for e in salary_events if "2026-07-01" <= e.date_str <= "2026-07-31"]
        outside = [e for e in salary_events if e.date_str < "2026-07-01" or e.date_str > "2026-07-31"]

        for e in inside:
            self.assertAlmostEqual(e.amount, 1500.0, places=1,
                                   msg=f"Inside temp window should be 1500, got {e.amount}")
        for e in outside:
            self.assertAlmostEqual(e.amount, 3000.0, places=1,
                                   msg=f"Outside temp window should be 3000, got {e.amount}")

    def test_salary_end_stops_recurring_salary(self):
        """SALARY_END: no recurring salary events projected after effective_date."""
        AT = self._AmendmentType()
        from datetime import date as _date
        am = self._amendment(
            amendment_type=AT.SALARY_END,
            effective_date=_date(2026, 7, 1),
            category="salary",
        )
        profile = self._profile()
        events = self._salary_events(amount=3000.0)
        forecaster = FinancialForecaster(profile, events, amendments=[am])
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        salary_after = [
            e for e in timeline
            if e.category == "salary" and e.is_recurring and e.date_str > "2026-07-01"
        ]
        self.assertEqual(len(salary_after), 0,
                         msg=f"Expected no salary after SALARY_END, got: {salary_after}")

    def test_salary_date_change_suppresses_old_date(self):
        """SALARY_DATE_CHANGE: old recurring pay date is suppressed; new date appears instead."""
        AT = self._AmendmentType()
        from datetime import date as _date
        am = self._amendment(
            amendment_type=AT.SALARY_DATE_CHANGE,
            effective_date=_date(2026, 7, 5),
            category="salary",
        )
        profile = self._profile()
        events = self._salary_events(amount=3000.0)
        forecaster = FinancialForecaster(profile, events, amendments=[am])
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        salary_july = [e for e in timeline if e.category == "salary" and "2026-07" in e.date_str]
        old_dates = [e for e in salary_july if e.date_str == "2026-07-25"]
        new_dates = [e for e in salary_july if e.date_str == "2026-07-05"]

        self.assertEqual(len(old_dates), 0,
                         msg="Old pay date (25th) should be suppressed")
        self.assertGreater(len(new_dates), 0,
                           msg="New pay date (5th) should appear in timeline")

    def test_event_cancelled_removes_from_timeline(self):
        """EVENT_CANCELLED: the referenced event_id is absent from the forecast."""
        AT = self._AmendmentType()
        am = self._amendment(
            amendment_type=AT.EVENT_CANCELLED,
            related_event_id="target_ev",
        )
        profile = self._profile()
        target_event = FinancialEvent(
            event_id="target_ev",
            user_id="test_user",
            event_type="expense",
            category="rent",
            description="Rent",
            direction="debit",
            status="scheduled",
            amount=1200.0,
            currency="USD",
            event_date="2026-06-25",
            settlement_date="2026-06-25",
            flexibility="fixed",
            minimum_allowed_amount=None,
        )
        forecaster = FinancialForecaster(profile, [target_event], amendments=[am])
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        cancelled_present = any(e.event_id == "target_ev" for e in timeline)
        self.assertFalse(cancelled_present, "Cancelled event should not appear in timeline")

    def test_event_amended_replaces_amount_and_date(self):
        """EVENT_AMENDED: referenced event appears with amended amount and date."""
        AT = self._AmendmentType()
        from datetime import date as _date
        am = self._amendment(
            amendment_type=AT.EVENT_AMENDED,
            related_event_id="target_ev",
            amount=1400.0,
            currency="USD",
            effective_date=_date(2026, 6, 28),
        )
        profile = self._profile()
        target_event = FinancialEvent(
            event_id="target_ev",
            user_id="test_user",
            event_type="expense",
            category="rent",
            description="Rent",
            direction="debit",
            status="scheduled",
            amount=1200.0,
            currency="USD",
            event_date="2026-06-25",
            settlement_date="2026-06-25",
            flexibility="fixed",
            minimum_allowed_amount=None,
        )
        forecaster = FinancialForecaster(profile, [target_event], amendments=[am])
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        amended = [e for e in timeline if e.event_id == "target_ev"]
        self.assertEqual(len(amended), 1)
        self.assertAlmostEqual(amended[0].amount, 1400.0, places=1)
        self.assertEqual(amended[0].date_str, "2026-06-28")

    def test_income_confirmed_adds_credit(self):
        """INCOME_CONFIRMED: adds a one-off credit on effective_date."""
        AT = self._AmendmentType()
        from datetime import date as _date
        am = self._amendment(
            amendment_type=AT.INCOME_CONFIRMED,
            effective_date=_date(2026, 6, 20),
            amount=2000.0,
            currency="USD",
            category="bonus",
        )
        profile = self._profile()
        forecaster = FinancialForecaster(profile, [], amendments=[am])
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        bonus = [e for e in timeline if e.category == "bonus" and e.direction == "credit"]
        self.assertEqual(len(bonus), 1)
        self.assertEqual(bonus[0].date_str, "2026-06-20")
        self.assertAlmostEqual(bonus[0].amount, 2000.0, places=1)

    def test_income_excluded_removes_linked_event(self):
        """INCOME_EXCLUDED: the linked event_id does not appear as cash-in."""
        AT = self._AmendmentType()
        am = self._amendment(
            amendment_type=AT.INCOME_EXCLUDED,
            related_event_id="pending_bonus",
        )
        profile = self._profile()
        bonus_event = FinancialEvent(
            event_id="pending_bonus",
            user_id="test_user",
            event_type="income",
            category="income",
            description="Pending bonus",
            direction="credit",
            status="scheduled",
            amount=5000.0,
            currency="USD",
            event_date="2026-06-20",
            settlement_date="2026-06-20",
            flexibility="fixed",
            minimum_allowed_amount=None,
        )
        forecaster = FinancialForecaster(profile, [bonus_event], amendments=[am])
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        excluded = any(e.event_id == "pending_bonus" for e in timeline)
        self.assertFalse(excluded, "INCOME_EXCLUDED event should not appear in timeline")

    def test_unlinked_income_excluded_preserves_salary_and_excludes_variable_income(self):
        """Unlinked INCOME_EXCLUDED preserves confirmed salary and excludes variable income."""
        AT = self._AmendmentType()
        am = self._amendment(
            amendment_type=AT.INCOME_EXCLUDED,
            related_event_id=None,
        )
        profile = self._profile()
        sal_events = [
            FinancialEvent(
                event_id=f"sal_{i}",
                user_id="test_user",
                event_type="income",
                category="salary",
                description="Base salary",
                direction="credit",
                status="settled",
                amount=3000.0,
                currency="USD",
                event_date=f"2026-0{i}-15",
                settlement_date=f"2026-0{i}-15",
                flexibility="fixed",
                minimum_allowed_amount=None,
            )
            for i in range(1, 5)
        ]
        gig_events = [
            FinancialEvent(
                event_id=f"gig_{i}",
                user_id="test_user",
                event_type="income",
                category="salary",
                description="Delivery platform payout",
                direction="credit",
                status="settled",
                amount=500.0 + i * 10,
                currency="USD",
                event_date=f"2026-0{i}-10",
                settlement_date=f"2026-0{i}-10",
                flexibility="fixed",
                minimum_allowed_amount=None,
            )
            for i in range(1, 5)
        ]
        forecaster = FinancialForecaster(profile, sal_events + gig_events, amendments=[am])
        timeline = forecaster.build_forecast_timeline("2026-05-01")

        sal_proj = [e for e in timeline if e.description == "Base salary" and e.direction == "credit"]
        gig_proj = [e for e in timeline if e.description == "Delivery platform payout" and e.direction == "credit"]

        self.assertGreater(len(sal_proj), 0, "Confirmed salary should be projected")
        self.assertEqual(len(gig_proj), 0, "Unconfirmed gig income should NOT be projected")

    def test_amendments_only_applied_to_matching_user(self):
        """Amendments for a different user_id must not affect this user's forecast."""
        AT = self._AmendmentType()
        am = self._amendment(
            amendment_type=AT.EVENT_CANCELLED,
            related_event_id="target_ev",
        )
        am.user_id = "other_user"

        profile = self._profile()
        target_event = FinancialEvent(
            event_id="target_ev",
            user_id="test_user",
            event_type="expense",
            category="rent",
            description="Rent",
            direction="debit",
            status="scheduled",
            amount=900.0,
            currency="USD",
            event_date="2026-06-25",
            settlement_date="2026-06-25",
            flexibility="fixed",
            minimum_allowed_amount=None,
        )
        forecaster = FinancialForecaster(profile, [target_event], amendments=[am])
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        present = any(e.event_id == "target_ev" for e in timeline)
        self.assertTrue(present, "Event for test_user should not be cancelled by other_user amendment")

    def test_integration_build_amendments_consumed_by_forecaster(self):
        """Integration: build_amendments() output is accepted by FinancialForecaster without re-parsing."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from messages.message_processor import build_amendments
        except ImportError:
            self.skipTest("messages module not importable")

        sample_csv = Path(__file__).resolve().parent.parent / "messages" / "messages_sample.csv"
        if not sample_csv.exists():
            self.skipTest("messages_sample.csv not found")

        amendments = build_amendments(str(sample_csv))
        self.assertIsInstance(amendments, list, "build_amendments must return a list")

        profile = self._profile()
        forecaster = FinancialForecaster(profile, [], amendments=amendments)
        timeline = forecaster.build_forecast_timeline(self._ref_date)

        self.assertIsInstance(timeline, list)


class TestRecurringIncomeTermination(unittest.TestCase):
    """Focused regression tests for finance-side recurring-income termination."""

    @classmethod
    def setUpClass(cls):
        try:
            from messages.models import Amendment, AmendmentType
            cls.Amendment = Amendment
            cls.AmendmentType = AmendmentType
        except ImportError:
            cls.Amendment = None
            cls.AmendmentType = None

    def _profile(self, balance=20000.0, currency="USD") -> FinancialProfile:
        return FinancialProfile(
            user_id="test_user",
            home_currency=currency,
            current_available_balance=balance,
            minimum_balance_to_keep=500.0,
            financial_priorities=[],
            expense_categories_to_protect=set(),
            expense_categories_user_is_willing_to_reduce=set(),
            expense_categories_user_is_willing_to_stop=set(),
            payment_methods_user_will_consider={"full_payment"},
            max_installment_months=None,
        )

    def _make_salary_event(self, event_id: str, date_str: str, desc: str = "Monthly salary",
                           amount: float = 3000.0, status: str = "settled") -> FinancialEvent:
        return FinancialEvent(
            event_id=event_id,
            user_id="test_user",
            event_type="income",
            category="salary",
            description=desc,
            direction="credit",
            status=status,
            amount=amount,
            currency="USD",
            event_date=date_str,
            settlement_date=date_str,
            flexibility="fixed",
            minimum_allowed_amount=None,
        )

    def test_recurring_salary_continues_normally(self):
        """Ordinary recurring salary stream continues throughout forecast horizon."""
        events = [
            self._make_salary_event("sal_1", "2026-04-25", desc="Payroll credit"),
            self._make_salary_event("sal_2", "2026-05-25", desc="Payroll credit"),
        ]
        forecaster = FinancialForecaster(self._profile(), events)
        timeline = forecaster.build_forecast_timeline("2026-06-01", forecast_days=90)

        salary_events = [e for e in timeline if e.category == "salary" and e.is_recurring]
        self.assertGreaterEqual(len(salary_events), 2, "Ordinary recurring salary should continue")
        dates = [e.date_str for e in salary_events]
        self.assertTrue(any("2026-06" in d for d in dates), f"Expected June salary, got {dates}")
        self.assertTrue(any("2026-07" in d for d in dates), f"Expected July salary, got {dates}")

    def test_final_payroll_stops_future_recurrence(self):
        """A settled final employer payroll event stops future salary recurrence."""
        events = [
            self._make_salary_event("sal_1", "2026-03-25", desc="Payroll credit"),
            self._make_salary_event("sal_2", "2026-04-25", desc="Payroll credit"),
            self._make_salary_event("sal_final", "2026-05-25", desc="Final employer payroll", status="settled"),
        ]
        forecaster = FinancialForecaster(self._profile(), events)
        timeline = forecaster.build_forecast_timeline("2026-06-01", forecast_days=90)

        salary_events = [e for e in timeline if e.category == "salary"]
        self.assertEqual(len(salary_events), 0,
                         f"No salary should recur after final payroll. Found: {salary_events}")

    def test_final_payroll_itself_still_included(self):
        """A terminal payroll event itself is included, but stops subsequent recurrence."""
        events = [
            self._make_salary_event("sal_1", "2026-04-25", desc="Payroll credit"),
            self._make_salary_event("sal_2", "2026-05-25", desc="Payroll credit"),
            self._make_salary_event("sal_final", "2026-06-25", desc="Final employer payroll", status="scheduled"),
        ]
        forecaster = FinancialForecaster(self._profile(), events)
        timeline = forecaster.build_forecast_timeline("2026-06-01", forecast_days=90)

        salary_events = [e for e in timeline if e.category == "salary"]
        # The scheduled final payroll on 2026-06-25 should be included
        final_events = [e for e in salary_events if e.date_str == "2026-06-25"]
        self.assertEqual(len(final_events), 1, "Final payroll on 2026-06-25 must be included")

        # Future recurrence after 2026-06-25 must be suppressed
        subsequent_events = [e for e in salary_events if e.date_str > "2026-06-25"]
        self.assertEqual(len(subsequent_events), 0,
                         f"No salary events should be projected after final payroll date. Found: {subsequent_events}")

    def test_salary_end_amendment_remains_correct(self):
        """SALARY_END amendment continues to terminate recurring salary correctly."""
        if self.Amendment is None or self.AmendmentType is None:
            self.skipTest("messages module not available")

        from datetime import date
        events = [
            self._make_salary_event("sal_1", "2026-04-25", desc="Monthly salary"),
            self._make_salary_event("sal_2", "2026-05-25", desc="Monthly salary"),
        ]
        am = self.Amendment(
            message_id="msg_salary_end",
            user_id="test_user",
            amendment_type=self.AmendmentType.SALARY_END,
            sent_at="2026-05-28",
            effective_date=date(2026, 7, 1),
            confidence=1.0,
            evidence_span="Contract ending July 1",
            notes="Salary terminates July 1",
            category="salary",
        )
        forecaster = FinancialForecaster(self._profile(), events, amendments=[am])
        timeline = forecaster.build_forecast_timeline("2026-06-01", forecast_days=90)

        salary_events = [e for e in timeline if e.category == "salary"]
        # June 25th salary should be present (before salary end)
        june_salary = [e for e in salary_events if e.date_str == "2026-06-25"]
        self.assertEqual(len(june_salary), 1, "June salary before SALARY_END must be present")

        # No salary after July 1st
        after_end = [e for e in salary_events if e.date_str > "2026-07-01"]
        self.assertEqual(len(after_end), 0,
                         f"No salary should appear after SALARY_END date. Found: {after_end}")

    def test_subsequent_employment_not_suppressed_by_prior_terminal_payroll(self):
        """A new employment salary stream commencing after an old final payroll recurs normally."""
        events = [
            self._make_salary_event("sal_old_1", "2024-11-15", desc="Previous employer payroll"),
            self._make_salary_event("sal_old_final", "2024-12-15", desc="Final employer payroll"),
            self._make_salary_event("sal_new_1", "2025-02-15", desc="New employer payroll"),
            self._make_salary_event("sal_new_2", "2025-03-15", desc="New employer payroll"),
        ]
        forecaster = FinancialForecaster(self._profile(), events)
        timeline = forecaster.build_forecast_timeline("2025-04-01", forecast_days=90)

        salary_events = [e for e in timeline if e.category == "salary" and e.is_recurring]
        self.assertGreaterEqual(len(salary_events), 2,
                                "New employment salary stream should recur despite historical final payroll")

    def test_temporary_reduction_and_ordinary_descriptions_not_treated_as_terminal(self):
        """Temporary reduction descriptions and regular payroll descriptions do not stop recurrence."""
        events = [
            self._make_salary_event("sal_1", "2026-04-25", desc="Payroll credit"),
            self._make_salary_event("sal_2", "2026-05-25", desc="Temporary salary adjustment"),
        ]
        forecaster = FinancialForecaster(self._profile(), events)
        timeline = forecaster.build_forecast_timeline("2026-06-01", forecast_days=90)

        salary_events = [e for e in timeline if e.category == "salary" and e.is_recurring]
        self.assertGreaterEqual(len(salary_events), 2,
                                "Temporary reduction must not permanently terminate recurring salary")



class TestSpendingAdjustmentsAndPlanSelection(unittest.TestCase):
    """Regression tests for spending adjustments and plan selection ranking rules."""

    def _profile(self, **kwargs):
        defaults = dict(
            user_id="user_test_adj",
            home_currency="USD",
            current_available_balance=2500.0,
            minimum_balance_to_keep=1000.0,
            financial_priorities=["housing", "food"],
            expense_categories_to_protect={"rent", "groceries"},
            expense_categories_user_is_willing_to_reduce={"dining"},
            expense_categories_user_is_willing_to_stop={"streaming"},
            payment_methods_user_will_consider={"full_payment", "installments", "partial_payment"},
            max_installment_months=6,
        )
        defaults.update(kwargs)
        return FinancialProfile(**defaults)

    def test_unsafe_without_spending_change_but_safe_with_permitted_reduction(self):
        """Unsafe without spending change but safe with permitted reduction."""
        profile = self._profile(current_available_balance=2500.0, minimum_balance_to_keep=1000.0)
        events = [
            FinancialEvent(
                event_id="dining_ev_1",
                user_id="user_test_adj",
                event_type="expense",
                description="Restaurant dinner",
                category="dining",
                direction="debit",
                amount=300.0,
                currency="USD",
                event_date="2025-01-05",
                settlement_date="2025-01-05",
                status="settled",
                flexibility="reducible",
                minimum_allowed_amount=100.0,
            ),
            FinancialEvent(
                event_id="dining_ev_2",
                user_id="user_test_adj",
                event_type="expense",
                description="Restaurant dinner",
                category="dining",
                direction="debit",
                amount=300.0,
                currency="USD",
                event_date="2025-02-05",
                settlement_date="2025-02-05",
                status="settled",
                flexibility="reducible",
                minimum_allowed_amount=100.0,
            ),
        ]
        forecaster = FinancialForecaster(profile, events)
        engine = AffordabilityEngine(profile, forecaster)
        req = PurchaseRequest(
            request_id="req_adj_01",
            user_id="user_test_adj",
            request_date="2025-03-01",
            request_type="purchase",
            requested_amount=1000.0,
            desired_completion_date="2025-03-15",
            allows_partial_payment=False,
            request_text="Purchase requiring dining reduction",
        )
        res = engine.evaluate_request(req, payment_options=[])
        self.assertEqual(res.affordability_status, "affordable_with_plan")
        self.assertEqual(res.recommended_payment_method, "full_payment")
        self.assertIn("reduce_to:dining_ev_2:100", res.spending_changes_needed)

    def test_unsafe_without_spending_change_but_safe_with_permitted_stoppage(self):
        """Unsafe without spending change but safe with permitted stoppage."""
        profile = self._profile(current_available_balance=2500.0, minimum_balance_to_keep=1000.0)
        events = [
            FinancialEvent(
                event_id="stream_ev_1",
                user_id="user_test_adj",
                event_type="subscription",
                description="Streaming sub",
                category="streaming",
                direction="debit",
                amount=250.0,
                currency="USD",
                event_date="2025-01-08",
                settlement_date="2025-01-08",
                status="settled",
                flexibility="stoppable",
            ),
            FinancialEvent(
                event_id="stream_ev_2",
                user_id="user_test_adj",
                event_type="subscription",
                description="Streaming sub",
                category="streaming",
                direction="debit",
                amount=250.0,
                currency="USD",
                event_date="2025-02-08",
                settlement_date="2025-02-08",
                status="settled",
                flexibility="stoppable",
            ),
        ]
        forecaster = FinancialForecaster(profile, events)
        engine = AffordabilityEngine(profile, forecaster)
        req = PurchaseRequest(
            request_id="req_adj_02",
            user_id="user_test_adj",
            request_date="2025-03-01",
            request_type="purchase",
            requested_amount=1000.0,
            desired_completion_date="2025-03-15",
            allows_partial_payment=False,
            request_text="Purchase requiring streaming stoppage",
        )
        res = engine.evaluate_request(req, payment_options=[])
        self.assertEqual(res.affordability_status, "affordable_with_plan")
        self.assertEqual(res.recommended_payment_method, "full_payment")
        self.assertIn("stop:stream_ev_2", res.spending_changes_needed)

    def test_protected_expense_cannot_be_reduced_or_stopped(self):
        """Protected expense cannot be reduced or stopped even if marked reducible."""
        profile = self._profile(
            expense_categories_to_protect={"rent", "groceries"},
            expense_categories_user_is_willing_to_reduce={"rent", "groceries", "dining"},
            expense_categories_user_is_willing_to_stop={"rent", "groceries", "streaming"},
        )
        adj_engine = SpendingAdjustmentEngine(profile)
        rent_ev = ProjectedCashEvent(
            event_id="rent_01",
            category="rent",
            description="Apartment rent",
            direction="debit",
            amount=800.0,
            date_str="2025-03-05",
            flexibility="reducible_or_stoppable",
            minimum_allowed_amount=400.0,
        )
        self.assertFalse(adj_engine.is_modifiable(rent_ev))
        self.assertEqual(adj_engine.get_candidate_changes_for_event(rent_ev), [])

    def test_spending_adjustment_considered_even_when_baseline_is_not_affordable(self):
        """Spending adjustment is explored even when baseline without changes is unsafe."""
        profile = self._profile(current_available_balance=2000.0, minimum_balance_to_keep=1000.0)
        events = [
            FinancialEvent(
                event_id="stream_ev_base1",
                user_id="user_test_adj",
                event_type="subscription",
                description="Streaming",
                category="streaming",
                direction="debit",
                amount=500.0,
                currency="USD",
                event_date="2025-01-05",
                settlement_date="2025-01-05",
                status="settled",
                flexibility="stoppable",
            ),
            FinancialEvent(
                event_id="stream_ev_base2",
                user_id="user_test_adj",
                event_type="subscription",
                description="Streaming",
                category="streaming",
                direction="debit",
                amount=500.0,
                currency="USD",
                event_date="2025-02-05",
                settlement_date="2025-02-05",
                status="settled",
                flexibility="stoppable",
            ),
        ]
        forecaster = FinancialForecaster(profile, events)
        engine = AffordabilityEngine(profile, forecaster)
        req = PurchaseRequest(
            request_id="req_adj_base",
            user_id="user_test_adj",
            request_date="2025-03-01",
            request_type="purchase",
            requested_amount=1000.0,
            desired_completion_date="2025-03-15",
            allows_partial_payment=False,
            request_text="Baseline unsafe",
        )
        # Without changes: safe_today is 0.0 (safe_today < requested_amount).
        res = engine.evaluate_request(req, payment_options=[])
        self.assertEqual(res.affordability_status, "affordable_with_plan")
        self.assertEqual(res.recommended_payment_method, "full_payment")
        self.assertIn("stop:stream_ev_base2", res.spending_changes_needed)

    def test_partial_payment_vs_installments_follows_ranking(self):
        """Partial payment ranks ahead of installments when cheaper and both on time."""
        profile = self._profile(
            current_available_balance=2500.0,
            minimum_balance_to_keep=1000.0,
            payment_methods_user_will_consider={"installments", "partial_payment"},
            max_installment_months=3,
        )
        events = [
            FinancialEvent(
                event_id="salary_p1",
                user_id="user_test_adj",
                event_type="income",
                description="Salary",
                category="salary",
                direction="credit",
                amount=2000.0,
                currency="USD",
                event_date="2025-01-15",
                settlement_date="2025-01-15",
                status="settled",
            ),
            FinancialEvent(
                event_id="salary_p2",
                user_id="user_test_adj",
                event_type="income",
                description="Salary",
                category="salary",
                direction="credit",
                amount=2000.0,
                currency="USD",
                event_date="2025-02-15",
                settlement_date="2025-02-15",
                status="settled",
            ),
        ]
        forecaster = FinancialForecaster(profile, events)
        engine = AffordabilityEngine(profile, forecaster)
        req = PurchaseRequest(
            request_id="req_ranking",
            user_id="user_test_adj",
            request_date="2025-03-01",
            request_type="purchase",
            requested_amount=2000.0,
            desired_completion_date="2025-03-25",
            allows_partial_payment=True,
            request_text="Evaluate partial vs installments",
        )
        opts = [
            PaymentOption(
                payment_option_id="opt_inst_1",
                request_id="req_ranking",
                payment_method="installments",
                total_payable_amount=2200.0,
                number_of_payments=2,
                first_payment_date="2025-03-05",
                payment_frequency_days=30,
                financing_fee=200.0,
                payment_amount=1100.0,
            )
        ]
        res = engine.evaluate_request(req, payment_options=opts)
        self.assertEqual(res.recommended_payment_method, "partial_payment")
        self.assertEqual(res.affordability_status, "affordable_with_plan")

    def test_official_plan_ranking_order_prefers_no_spending_changes(self):
        """Rule 2: plans requiring no spending changes rank ahead of plans requiring spending changes."""
        from finance.affordability import CandidatePlan, parse_date
        desired_dt = parse_date("2025-03-20")

        def rank_key(p: CandidatePlan):
            on_time = 0 if parse_date(p.completion_date) <= desired_dt else 1
            num_changes = len(p.spending_changes)
            return (
                on_time,
                num_changes,
                p.total_cost,
                p.start_date,
                len(p.payments),
                p.payment_option_id,
            )

        plan_no_changes = CandidatePlan(
            method="wait",
            status="affordable_later",
            payments=[PaymentScheduleItem("2025-03-10", 1000.0)],
            spending_changes=[],
            total_cost=1000.0,
            start_date="2025-03-10",
            completion_date="2025-03-10",
            payment_option_id="999",
        )
        plan_with_changes = CandidatePlan(
            method="full_payment",
            status="affordable_with_plan",
            payments=[PaymentScheduleItem("2025-03-01", 1000.0)],
            spending_changes=[SpendingChange("stop", "ev_1")],
            total_cost=1000.0,
            start_date="2025-03-01",
            completion_date="2025-03-01",
            payment_option_id="000",
        )
        plans = [plan_with_changes, plan_no_changes]
        plans.sort(key=rank_key)
        self.assertEqual(plans[0], plan_no_changes)

class TestDataNormalizationAndOrdering(unittest.TestCase):
    """Regression tests for data normalization and option ordering in the finance layer."""

    def test_financial_profile_currency_property(self):
        """Verify profile.currency property returns home_currency."""
        profile = FinancialProfile(
            user_id="user_test",
            home_currency="EUR",
            current_available_balance=1000.0,
            minimum_balance_to_keep=200.0,
        )
        self.assertEqual(profile.currency, "EUR")
        self.assertEqual(profile.currency, profile.home_currency)

    def test_payment_options_natural_sorting(self):
        """Verify payment options across numeric boundaries (99, 100, 101) sort naturally."""
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as tf:
            tf.write("payment_option_id,request_id,payment_method,payment_amount,number_of_payments,first_payment_date,payment_frequency_days,financing_fee,total_payable_amount\n")
            tf.write("payment_option_100,req_test,installments,100,3,2025-01-01,30,0,300\n")
            tf.write("payment_option_99,req_test,installments,100,3,2025-01-01,30,0,300\n")
            tf.write("payment_option_101,req_test,installments,100,3,2025-01-01,30,0,300\n")
            temp_path = tf.name

        try:
            opts = load_payment_options(temp_path)
            sorted_ids = [o.payment_option_id for o in opts["req_test"]]
            self.assertEqual(sorted_ids, ["payment_option_99", "payment_option_100", "payment_option_101"])
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_profile_categories_lowercased(self):
        """Verify categories and payment methods in profile are normalized to lowercase."""
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as tf:
            tf.write("user_id,home_currency,current_available_balance,minimum_balance_to_keep,financial_priorities,expense_categories_to_protect,expense_categories_user_is_willing_to_reduce,expense_categories_user_is_willing_to_stop,payment_methods_user_will_consider,max_installment_months\n")
            tf.write("user_case_test,USD,5000,1000,Education|Debt_Repayment,Rent|Groceries,Dining,Streaming,Full_Payment|Installments,12\n")
            temp_path = tf.name

        try:
            profiles = load_profiles(temp_path)
            prof = profiles["user_case_test"]
            self.assertIn("rent", prof.expense_categories_to_protect)
            self.assertIn("groceries", prof.expense_categories_to_protect)
            self.assertIn("dining", prof.expense_categories_user_is_willing_to_reduce)
            self.assertIn("streaming", prof.expense_categories_user_is_willing_to_stop)
            self.assertIn("full_payment", prof.payment_methods_user_will_consider)
            self.assertIn("education", prof.financial_priorities)
        finally:
            Path(temp_path).unlink(missing_ok=True)


    def test_plan_ranking_natural_option_tie_breaker(self):
        """Verify tie-breaker Rule 6 prefers payment_option_99 over payment_option_100."""
        req = PurchaseRequest(
            request_id="req_tie",
            user_id="user_test",
            request_date="2025-01-01",
            request_type="purchase",
            requested_amount=300.0,
            desired_completion_date="2025-04-01",
            allows_partial_payment=False,
            request_text="Can I buy this?",
        )
        opt99 = PaymentOption("payment_option_99", "req_tie", "installments", 100.0, 3, "2025-01-01", 30, 0.0, 300.0)
        opt100 = PaymentOption("payment_option_100", "req_tie", "installments", 100.0, 3, "2025-01-01", 30, 0.0, 300.0)
        prof = FinancialProfile("user_tie", "USD", 5000.0, 1000.0, payment_methods_user_will_consider={"installments"})
        forecaster = FinancialForecaster(prof, [])
        engine = AffordabilityEngine(prof, forecaster)
        # Even if opt100 is passed first, opt99 should win the tie-breaker
        res = engine.evaluate_request(req, [opt100, opt99])
        self.assertEqual(res.recommended_payment_method, "installments")
        self.assertEqual(res.payment_plan, "2025-01-01:100|2025-01-31:100|2025-03-02:100")


if __name__ == "__main__":
    unittest.main()
