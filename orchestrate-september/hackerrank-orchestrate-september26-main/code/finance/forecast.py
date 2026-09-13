"""Cash-flow forecasting and 90-day safety simulation engine."""

from __future__ import annotations

import calendar
import statistics
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from .exchange_rates import ExchangeRateService, get_default_service
from .models import FinancialEvent, FinancialProfile, PaymentScheduleItem, SpendingChange

if TYPE_CHECKING:
    pass

try:
    from messages.models import Amendment, AmendmentType
except ImportError:
    Amendment = None  # type: ignore[assignment,misc]
    AmendmentType = None  # type: ignore[assignment]


def parse_date(date_str: str) -> datetime:
    """Parse YYYY-MM-DD string into a datetime object."""
    return datetime.strptime(date_str.strip(), "%Y-%m-%d")


def format_date(dt: datetime) -> str:
    """Format datetime object into YYYY-MM-DD string."""
    return dt.strftime("%Y-%m-%d")

_TERMINAL_PAYROLL_PATTERN = re.compile(
    r"\b("
    r"final\s+(employer\s+|contract\s+)?(payroll|salary|pay|paycheck|wages|compensation)"
    r"|terminal\s+(payroll|salary|pay)"
    r"|severance(\s+(pay|payment|payroll))?"
    r"|separation\s+pay"
    r"|termination\s+pay"
    r"|exit\s+payroll"
    r"|end\s+of\s+employment"
    r"|last\s+(employer\s+)?(payroll|salary|paycheck)"
    r")\b",
    re.IGNORECASE,
)


_CONFIRMED_SALARY_PATTERN = re.compile(
    r"\b(salary|payroll)\b",
    re.IGNORECASE,
)


def _is_confirmed_salary(description: str) -> bool:
    """Return True if description indicates a confirmed salary or payroll stream."""
    return bool(_CONFIRMED_SALARY_PATTERN.search(description))


def _date_to_dt(d: date) -> datetime:
    """Convert a date to a midnight datetime for comparison with parsed datetimes."""
    return datetime(d.year, d.month, d.day)


class ProjectedCashEvent:
    """A projected future cash event in user's home currency."""

    def __init__(
        self,
        event_id: str,
        category: str,
        description: str,
        direction: str,
        amount: float,
        date_str: str,
        flexibility: str = "fixed",
        is_recurring: bool = False,
        minimum_allowed_amount: Optional[float] = None,
    ) -> None:
        self.event_id = event_id
        self.category = category
        self.description = description
        self.direction = direction
        self.amount = amount
        self.date_str = date_str
        self.flexibility = flexibility
        self.is_recurring = is_recurring
        self.minimum_allowed_amount = minimum_allowed_amount

    def __repr__(self) -> str:
        return f"<ProjectedCashEvent {self.date_str} {self.direction} {self.amount} {self.category}:{self.description} id={self.event_id}>"


class FinancialForecaster:
    """Constructs cash-flow projections and executes 90-day balance simulations."""

    def __init__(
        self,
        profile: FinancialProfile,
        events: List[FinancialEvent],
        exchange_service: Optional[ExchangeRateService] = None,
        amendments: Optional[List] = None,
    ) -> None:
        self.profile = profile
        self.events = events
        self.rates = exchange_service or get_default_service()
        self._amendments: List = [
            a for a in (amendments or [])
            if getattr(a, "user_id", None) == profile.user_id
        ]

    def convert_event_amount(self, event: FinancialEvent) -> float:
        """Convert event amount to the user's home currency using the dated rate."""
        if not event.amount:
            return 0.0
        if event.currency == self.profile.home_currency:
            return float(event.amount)
        rate_date = event.effective_date or event.event_date
        return float(
            self.rates.convert_amount(
                event.amount,
                event.currency,
                self.profile.home_currency,
                rate_date,
            )
        )

    def _convert_amendment_amount(self, amendment) -> float:
        """Convert an amendment amount to home currency."""
        if amendment.amount is None:
            return 0.0
        if not amendment.currency or amendment.currency == self.profile.home_currency:
            return float(amendment.amount)
        eff = amendment.effective_date
        rate_date = eff.strftime("%Y-%m-%d") if eff else None
        if rate_date is None:
            return float(amendment.amount)
        return float(
            self.rates.convert_amount(
                amendment.amount,
                amendment.currency,
                self.profile.home_currency,
                rate_date,
            )
        )

    def _cancelled_event_ids(self) -> Set[str]:
        """Return set of event_ids that are cancelled by amendments."""
        if not self._amendments or AmendmentType is None:
            return set()
        return {
            a.related_event_id
            for a in self._amendments
            if a.amendment_type == AmendmentType.EVENT_CANCELLED
            and a.related_event_id
        }

    def _excluded_event_ids(self) -> Set[str]:
        """Return set of event_ids excluded (income_excluded) by amendments."""
        if not self._amendments or AmendmentType is None:
            return set()
        return {
            a.related_event_id
            for a in self._amendments
            if a.amendment_type == AmendmentType.INCOME_EXCLUDED
            and a.related_event_id
        }

    def _has_unlinked_income_excluded(self) -> bool:
        """Return True if user has an INCOME_EXCLUDED amendment with no related_event_id."""
        if not self._amendments or AmendmentType is None:
            return False
        return any(
            a.amendment_type == AmendmentType.INCOME_EXCLUDED and not a.related_event_id
            for a in self._amendments
        )

    def _amended_events(self) -> Dict[str, object]:
        """Return map of event_id -> Amendment for EVENT_AMENDED amendments."""
        if not self._amendments or AmendmentType is None:
            return {}
        result: Dict[str, object] = {}
        for a in self._amendments:
            if a.amendment_type == AmendmentType.EVENT_AMENDED and a.related_event_id:
                result[a.related_event_id] = a
        return result

    def build_forecast_timeline(
        self,
        request_date_str: str,
        forecast_days: int = 90,
    ) -> List[ProjectedCashEvent]:
        """Generate the complete list of future projected cash events for the forecast window."""
        req_dt = parse_date(request_date_str)
        end_dt = req_dt + timedelta(days=forecast_days)

        cancelled_ids = self._cancelled_event_ids()
        excluded_ids = self._excluded_event_ids()
        amended_map = self._amended_events()

        projected: List[ProjectedCashEvent] = []
        settled_dates_by_key: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        scheduled_credit_dates: Dict[str, Set[str]] = defaultdict(set)

        for e in self.events:
            eff_date = e.effective_date
            if not eff_date:
                continue

            dt = parse_date(eff_date)
            if e.status == "settled":
                settled_dates_by_key[(e.category, e.description)].add(eff_date)

            if dt >= req_dt and dt <= end_dt:
                if e.event_id in cancelled_ids or e.event_id in excluded_ids:
                    continue

                if e.status in ("cancelled", "failed", "unrealized") or e.direction == "non_cash":
                    continue

                if e.status == "pending" and e.direction == "credit":
                    continue

                # A pending or scheduled debit whose event_date (initiation date) is
                # before request_date was already in-flight before today and is already
                # reflected in current_available_balance.  Projecting it again would
                # double-count the debit.
                if (
                    e.status in ("pending", "scheduled")
                    and e.direction == "debit"
                    and e.event_date
                    and e.event_date < format_date(req_dt)
                ):
                    continue

                if e.status in ("pending", "scheduled"):
                    if e.event_id in amended_map:
                        am = amended_map[e.event_id]
                        amt_home = self._convert_amendment_amount(am)
                        amended_date = (
                            am.effective_date.strftime("%Y-%m-%d")
                            if am.effective_date
                            else eff_date
                        )
                        projected.append(
                            ProjectedCashEvent(
                                event_id=e.event_id,
                                category=e.category,
                                description=e.description,
                                direction=e.direction,
                                amount=amt_home,
                                date_str=amended_date,
                                flexibility=e.flexibility,
                                is_recurring=False,
                                minimum_allowed_amount=e.minimum_allowed_amount,
                            )
                        )
                    else:
                        amt_home = self.convert_event_amount(e)
                        projected.append(
                            ProjectedCashEvent(
                                event_id=e.event_id,
                                category=e.category,
                                description=e.description,
                                direction=e.direction,
                                amount=amt_home,
                                date_str=eff_date,
                                flexibility=e.flexibility,
                                is_recurring=False,
                                minimum_allowed_amount=e.minimum_allowed_amount,
                            )
                        )
                    if e.direction == "credit":
                        scheduled_credit_dates[e.category].add(eff_date)

        user_income_excluded = self._has_unlinked_income_excluded()

        # Cadence-aware recurrence detector for short-period debit streams across rotating descriptions
        debits_by_cat: Dict[str, List[FinancialEvent]] = defaultdict(list)
        for e in self.events:
            if e.direction == "debit" and e.status == "settled" and e.effective_date:
                debits_by_cat[e.category].append(e)

        cadence_cats: Dict[str, Tuple[int, List[FinancialEvent]]] = {}
        for cat, elist in debits_by_cat.items():
            descs = set(e.description for e in elist)
            if len(descs) > 1 and len(elist) >= 4:
                s_evts = sorted(elist, key=lambda x: parse_date(x.effective_date))
                dts = [parse_date(x.effective_date) for x in s_evts]
                uniq_dts = sorted(list(set(dts)))
                gaps = [(uniq_dts[i+1] - uniq_dts[i]).days for i in range(len(uniq_dts)-1)]
                if gaps:
                    c = Counter(gaps)
                    top_gap, top_count = c.most_common(1)[0]
                    if top_count / len(gaps) >= 0.6:
                        cad = None
                        if 6 <= top_gap <= 8:
                            cad = 7
                        elif 9 <= top_gap <= 11:
                            cad = 10
                        elif 13 <= top_gap <= 15:
                            cad = 14
                        if cad is not None:
                            cadence_cats[cat] = (cad, s_evts)

        for cat, (cad_days, s_evts) in cadence_cats.items():
            last_e = s_evts[-1]
            last_dt = parse_date(last_e.effective_date)
            amts = [self.convert_event_amount(e) for e in s_evts]
            med = statistics.median(amts)
            valid_evts = [e for e in s_evts if self.convert_event_amount(e) <= 2.0 * med]
            chosen_e = valid_evts[-1] if valid_evts else last_e
            base_amount = self.convert_event_amount(chosen_e)

            cur_dt = last_dt + timedelta(days=cad_days)
            while cur_dt < req_dt:
                cur_dt += timedelta(days=cad_days)
            while cur_dt <= end_dt:
                proj_str = format_date(cur_dt)
                if proj_str not in settled_dates_by_key[(cat, chosen_e.description)]:
                    projected.append(
                        ProjectedCashEvent(
                            event_id=chosen_e.event_id,
                            category=cat,
                            description=chosen_e.description,
                            direction="debit",
                            amount=base_amount,
                            date_str=proj_str,
                            flexibility=chosen_e.flexibility,
                            is_recurring=True,
                            minimum_allowed_amount=chosen_e.minimum_allowed_amount,
                        )
                    )
                cur_dt += timedelta(days=cad_days)

        groups: Dict[Tuple[str, str, str, str], List[FinancialEvent]] = defaultdict(list)
        for e in self.events:
            if e.direction == "debit" and e.status == "settled":
                if e.category not in cadence_cats:
                    groups[(e.category, e.description, e.direction, e.flexibility)].append(e)
            elif e.direction == "credit" and e.category in ("salary", "income") and e.status in ("settled", "scheduled"):
                if user_income_excluded and not _is_confirmed_salary(e.description):
                    continue
                groups[(e.category, e.description, e.direction, e.flexibility)].append(e)

        has_recurring_salary = any(key[2] == "credit" and len(el) >= 2 for key, el in groups.items())
        if not has_recurring_salary:
            confirmed_salary = [
                e for e in self.events
                if e.category in ("salary", "income")
                and e.direction == "credit"
                and e.status in ("settled", "scheduled")
                and (not user_income_excluded or _is_confirmed_salary(e.description))
            ]
            if len(confirmed_salary) >= 2:
                latest_sal = max(confirmed_salary, key=lambda x: parse_date(x.effective_date))
                groups[("salary", latest_sal.description, "credit", "fixed")] = confirmed_salary

        projected_credit_dates: Dict[str, Set[str]] = defaultdict(set)
        for cat, dates in scheduled_credit_dates.items():
            projected_credit_dates[cat].update(dates)

        salary_end_dt = self._salary_end_date()
        terminal_payroll_dt = self._terminal_payroll_date()
        salary_date_change_dt = self._salary_date_change()
        salary_change_overrides = self._salary_change_overrides()
        salary_temp_overrides = self._salary_temporary_overrides()

        for (cat, desc, direction, flex), elist in groups.items():
            if len(elist) < 2:
                continue

            day_counts: Dict[int, int] = defaultdict(int)
            amounts: List[float] = []
            for e in elist:
                dt_str = e.effective_date
                if dt_str:
                    d_obj = parse_date(dt_str)
                    day_counts[d_obj.day] += 1
                    amounts.append(self.convert_event_amount(e))

            if not day_counts:
                continue

            best_day, count = max(day_counts.items(), key=lambda x: x[1])
            is_monthly = (count / len(elist)) >= 0.5 or (len(elist) >= 3 and count >= 2)

            # Sparse 2-event groups qualification:
            # If gap > 60 days, only qualify if user does not already have an established recurring stream in this category
            if is_monthly and len(elist) == 2 and direction == "debit":
                sorted_dts = sorted([parse_date(e.effective_date) for e in elist if e.effective_date])
                gap = (sorted_dts[1] - sorted_dts[0]).days if len(sorted_dts) == 2 else 0
                if gap > 60:
                    def _is_other_group_recurring(k: Tuple[str, str, str, str], el: List[FinancialEvent]) -> bool:
                        if k[0] != cat or k[2] != "debit" or k == (cat, desc, direction, flex):
                            return False
                        if len(el) >= 3:
                            dc: Dict[int, int] = defaultdict(int)
                            for item in el:
                                if item.effective_date:
                                    dc[parse_date(item.effective_date).day] += 1
                            if dc:
                                _, c = max(dc.items(), key=lambda x: x[1])
                                return (c / len(el)) >= 0.5 or c >= 2
                            return False
                        elif len(el) == 2:
                            dts_other = sorted([parse_date(item.effective_date) for item in el if item.effective_date])
                            return len(dts_other) == 2 and (dts_other[1] - dts_other[0]).days <= 45
                        return False

                    if any(_is_other_group_recurring(k, el) for k, el in groups.items()):
                        is_monthly = False

            if is_monthly and (direction == "debit" or (direction == "credit" and cat in ("salary", "income"))):
                latest_event = max(elist, key=lambda x: parse_date(x.effective_date))
                base_amount = amounts[-1]

                is_salary_stream = direction == "credit" and cat in ("salary", "income")

                skip_old_day = best_day if (is_salary_stream and salary_date_change_dt is not None) else None

                curr_year = req_dt.year
                curr_month = req_dt.month

                for m_offset in range(4):
                    target_month = (curr_month - 1 + m_offset) % 12 + 1
                    target_year = curr_year + (curr_month - 1 + m_offset) // 12
                    max_days = calendar.monthrange(target_year, target_month)[1]
                    actual_day = min(best_day, max_days)
                    proj_dt = datetime(target_year, target_month, actual_day)

                    if proj_dt >= req_dt and proj_dt <= end_dt:
                        proj_str = format_date(proj_dt)

                        if is_salary_stream:
                            if salary_end_dt is not None and proj_dt > salary_end_dt:
                                continue
                            if terminal_payroll_dt is not None and proj_dt > terminal_payroll_dt:
                                continue

                        if is_salary_stream and salary_date_change_dt is not None:
                            if proj_dt.day == skip_old_day and proj_dt >= salary_date_change_dt:
                                continue

                        if direction == "debit":
                            if proj_str not in settled_dates_by_key[(cat, desc)]:
                                projected.append(
                                    ProjectedCashEvent(
                                        event_id=latest_event.event_id,
                                        category=cat,
                                        description=desc,
                                        direction=direction,
                                        amount=base_amount,
                                        date_str=proj_str,
                                        flexibility=flex,
                                        is_recurring=True,
                                        minimum_allowed_amount=latest_event.minimum_allowed_amount,
                                    )
                                )
                        elif direction == "credit":
                            if proj_str not in projected_credit_dates[cat] and proj_str not in settled_dates_by_key[(cat, desc)]:
                                eff_amount = self._effective_salary_amount(base_amount, proj_dt, salary_change_overrides, salary_temp_overrides)
                                projected.append(
                                    ProjectedCashEvent(
                                        event_id=latest_event.event_id,
                                        category=cat,
                                        description=desc,
                                        direction=direction,
                                        amount=eff_amount,
                                        date_str=proj_str,
                                        flexibility=flex,
                                        is_recurring=True,
                                        minimum_allowed_amount=None,
                                    )
                                )
                                projected_credit_dates[cat].add(proj_str)

        self._inject_amendment_credits(projected, projected_credit_dates, req_dt, end_dt)

        if salary_date_change_dt is not None and salary_date_change_dt >= req_dt and salary_date_change_dt <= end_dt:
            if (salary_end_dt is None or salary_date_change_dt <= salary_end_dt) and (terminal_payroll_dt is None or salary_date_change_dt <= terminal_payroll_dt):
                new_date_str = format_date(salary_date_change_dt)
                if new_date_str not in projected_credit_dates.get("salary", set()):
                    base_amount = self._base_salary_amount()
                    if base_amount > 0.0:
                        eff_amount = self._effective_salary_amount(base_amount, salary_date_change_dt, salary_change_overrides, salary_temp_overrides)
                        projected.append(
                            ProjectedCashEvent(
                                event_id="amendment_salary_date_change",
                                category="salary",
                                description="Salary (date change)",
                                direction="credit",
                                amount=eff_amount,
                                date_str=new_date_str,
                                flexibility="fixed",
                                is_recurring=False,
                                minimum_allowed_amount=None,
                            )
                        )

        projected.sort(key=lambda x: parse_date(x.date_str))
        return projected

    def _terminal_payroll_date(self) -> Optional[datetime]:
        """Detect if underlying financial records indicate the recurring payroll stream has ended.

        Returns the effective date of the terminal payroll event if a genuine final/terminal
        payroll event is present and no subsequent active salary stream has commenced after it.
        """
        salary_events = [
            e
            for e in self.events
            if e.category in ("salary", "income")
            and e.direction == "credit"
            and e.status in ("settled", "scheduled")
            and e.effective_date
        ]
        if not salary_events:
            return None

        terminal_events = [
            e
            for e in salary_events
            if _TERMINAL_PAYROLL_PATTERN.search(e.description)
        ]
        if not terminal_events:
            return None

        latest_terminal = max(terminal_events, key=lambda e: parse_date(e.effective_date))
        latest_terminal_dt = parse_date(latest_terminal.effective_date)

        # A subsequent non-terminal salary event indicates a new employment / salary stream
        # started after the prior job ended.
        subsequent_events = [
            e
            for e in salary_events
            if parse_date(e.effective_date) > latest_terminal_dt
            and not _TERMINAL_PAYROLL_PATTERN.search(e.description)
        ]
        if subsequent_events:
            return None

        return latest_terminal_dt

    def _salary_end_date(self) -> Optional[datetime]:
        """Return the effective_date of the SALARY_END amendment if present."""
        if not self._amendments or AmendmentType is None:
            return None
        for a in self._amendments:
            if a.amendment_type == AmendmentType.SALARY_END and a.effective_date:
                return _date_to_dt(a.effective_date)
        return None

    def _salary_date_change(self) -> Optional[datetime]:
        """Return the new pay date from SALARY_DATE_CHANGE amendment if present."""
        if not self._amendments or AmendmentType is None:
            return None
        for a in self._amendments:
            if a.amendment_type == AmendmentType.SALARY_DATE_CHANGE and a.effective_date:
                return _date_to_dt(a.effective_date)
        return None

    def _salary_change_overrides(self) -> List[Tuple[datetime, float]]:
        """Return list of (effective_datetime, new_amount_home) for SALARY_CHANGE."""
        if not self._amendments or AmendmentType is None:
            return []
        result = []
        for a in self._amendments:
            if a.amendment_type == AmendmentType.SALARY_CHANGE and a.amount is not None:
                eff = a.effective_date or (a.sent_at.date() if getattr(a, "sent_at", None) else None)
                if eff:
                    result.append((_date_to_dt(eff), self._convert_amendment_amount(a)))
        result.sort(key=lambda x: x[0])
        return result

    def _salary_temporary_overrides(self) -> List[Tuple[datetime, Optional[datetime], float]]:
        """Return list of (start_dt, end_dt, temp_amount_home) for SALARY_TEMPORARY."""
        if not self._amendments or AmendmentType is None:
            return []
        result = []
        for a in self._amendments:
            if a.amendment_type == AmendmentType.SALARY_TEMPORARY and a.effective_date and a.amount is not None:
                start_dt = _date_to_dt(a.effective_date)
                end_dt = _date_to_dt(a.end_date) if a.end_date else None
                result.append((start_dt, end_dt, self._convert_amendment_amount(a)))
        return result

    def _base_salary_amount(self) -> float:
        """Return the most recent settled salary amount in home currency, or 0.0."""
        salary_events = [
            e for e in self.events
            if e.category in ("salary", "income") and e.direction == "credit" and e.status in ("settled", "scheduled")
        ]
        if not salary_events:
            return 0.0
        latest = max(salary_events, key=lambda x: parse_date(x.effective_date))
        return self.convert_event_amount(latest)

    def _effective_salary_amount(
        self,
        base_amount: float,
        proj_dt: datetime,
        change_overrides: List[Tuple[datetime, float]],
        temp_overrides: List[Tuple[datetime, Optional[datetime], float]],
    ) -> float:
        """Resolve the correct salary amount for a projected date considering amendments."""
        for start_dt, end_dt, temp_amt in temp_overrides:
            if proj_dt >= start_dt and (end_dt is None or proj_dt <= end_dt):
                return temp_amt

        effective_amount = base_amount
        for eff_dt, new_amt in change_overrides:
            if proj_dt >= eff_dt:
                effective_amount = new_amt

        return effective_amount

    def _inject_amendment_credits(
        self,
        projected: List[ProjectedCashEvent],
        projected_credit_dates: Dict[str, Set[str]],
        req_dt: datetime,
        end_dt: datetime,
    ) -> None:
        """Inject one-off credits for INCOME_CONFIRMED and REFUND_SETTLED amendments."""
        if not self._amendments or AmendmentType is None:
            return

        one_off_types = {AmendmentType.INCOME_CONFIRMED, AmendmentType.REFUND_SETTLED}
        for a in self._amendments:
            if a.amendment_type not in one_off_types:
                continue
            if a.amount is None or not a.effective_date:
                continue
            eff_dt = _date_to_dt(a.effective_date)
            if eff_dt < req_dt or eff_dt > end_dt:
                continue
            cat = a.category or ("income" if a.amendment_type == AmendmentType.INCOME_CONFIRMED else "refund")
            date_str = a.effective_date.strftime("%Y-%m-%d")
            amt_home = self._convert_amendment_amount(a)
            projected.append(
                ProjectedCashEvent(
                    event_id=f"amendment_{a.message_id}",
                    category=cat,
                    description=f"Amendment: {a.amendment_type.value}",
                    direction="credit",
                    amount=amt_home,
                    date_str=date_str,
                    flexibility="fixed",
                    is_recurring=False,
                    minimum_allowed_amount=None,
                )
            )

    def simulate(
        self,
        request_date_str: str,
        events: List[ProjectedCashEvent],
        payments: Optional[List[PaymentScheduleItem]] = None,
        adjustments: Optional[List[SpendingChange]] = None,
        forecast_days: int = 90,
    ) -> Tuple[float, Dict[str, float], str]:
        """Simulate daily balances over the 90-day window.

        Returns (min_balance, daily_balances, min_balance_date).
        """
        req_dt = parse_date(request_date_str)
        end_dt = req_dt + timedelta(days=forecast_days)

        stop_ids: Set[str] = set()
        reduce_map: Dict[str, float] = {}
        if adjustments:
            for adj in adjustments:
                if adj.action == "stop":
                    stop_ids.add(adj.event_id)
                elif adj.action == "reduce_to" and adj.new_amount is not None:
                    reduce_map[adj.event_id] = adj.new_amount

        daily_delta: Dict[str, float] = defaultdict(float)

        if payments:
            for p in payments:
                daily_delta[p.payment_date] -= p.amount

        for e in events:
            amt = e.amount
            if e.event_id in stop_ids:
                amt = 0.0
            elif e.event_id in reduce_map:
                amt = min(amt, reduce_map[e.event_id])

            if e.direction == "credit":
                daily_delta[e.date_str] += amt
            elif e.direction == "debit":
                daily_delta[e.date_str] -= amt

        balance = float(self.profile.current_available_balance)
        min_balance = balance
        min_date = request_date_str
        daily_balances: Dict[str, float] = {}

        cur = req_dt
        while cur <= end_dt:
            date_str = format_date(cur)
            if date_str in daily_delta:
                balance += daily_delta[date_str]
            daily_balances[date_str] = balance

            if balance < min_balance:
                min_balance = balance
                min_date = date_str

            cur += timedelta(days=1)

        return min_balance, daily_balances, min_date
