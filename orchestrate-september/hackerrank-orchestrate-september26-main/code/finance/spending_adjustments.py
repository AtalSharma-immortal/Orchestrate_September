"""Candidate spending adjustment generation and validation utilities."""

from __future__ import annotations

from itertools import combinations
from typing import List, Optional, Set

from .forecast import ProjectedCashEvent
from .models import FinancialProfile, SpendingChange


class SpendingAdjustmentEngine:
    """Generates valid spending adjustments based on user preferences and event constraints."""

    def __init__(self, profile: FinancialProfile) -> None:
        self.profile = profile

    def is_modifiable(self, event: ProjectedCashEvent) -> bool:
        """Check whether an event can be reduced or stopped."""
        if not event.event_id or event.direction != "debit":
            return False

        cat = event.category.lower()
        if cat in self.profile.expense_categories_to_protect:
            return False

        flex = event.flexibility.lower()
        can_reduce = (
            cat in self.profile.expense_categories_user_is_willing_to_reduce
            and flex in ("reducible", "reducible_or_stoppable")
        )
        can_stop = (
            cat in self.profile.expense_categories_user_is_willing_to_stop
            and flex in ("stoppable", "reducible_or_stoppable")
        )
        return can_reduce or can_stop

    def get_candidate_changes_for_event(
        self,
        event: ProjectedCashEvent,
        needed_savings: float = 0.0,
    ) -> List[SpendingChange]:
        """Return potential SpendingChange actions for a specific event."""
        if not self.is_modifiable(event):
            return []

        cat = event.category.lower()
        flex = event.flexibility.lower()
        changes: List[SpendingChange] = []

        if (
            cat in self.profile.expense_categories_user_is_willing_to_stop
            and flex in ("stoppable", "reducible_or_stoppable")
        ):
            changes.append(SpendingChange(action="stop", event_id=event.event_id))

        if (
            cat in self.profile.expense_categories_user_is_willing_to_reduce
            and flex in ("reducible", "reducible_or_stoppable")
        ):
            min_allowed = event.minimum_allowed_amount if event.minimum_allowed_amount is not None else 0.0
            if min_allowed < event.amount:
                changes.append(
                    SpendingChange(
                        action="reduce_to",
                        event_id=event.event_id,
                        new_amount=min_allowed,
                    )
                )

        return changes

    def generate_candidate_combinations(
        self,
        events: List[ProjectedCashEvent],
        max_changes: int = 3,
    ) -> List[List[SpendingChange]]:
        """Generate ranked candidate combinations of spending changes (up to 3 changes)."""
        seen_ids: Set[str] = set()
        unique_events: List[ProjectedCashEvent] = []
        for e in events:
            if e.event_id and e.event_id not in seen_ids and self.is_modifiable(e):
                seen_ids.add(e.event_id)
                unique_events.append(e)

        options_by_event: List[List[SpendingChange]] = []
        for ev in unique_events:
            event_opts = self.get_candidate_changes_for_event(ev)
            if event_opts:
                options_by_event.append(event_opts)

        all_combinations: List[List[SpendingChange]] = [[]]

        single_changes: List[SpendingChange] = [opt for opts in options_by_event for opt in opts]
        for c in single_changes:
            all_combinations.append([c])

        if max_changes >= 2:
            for i in range(len(options_by_event)):
                for j in range(i + 1, len(options_by_event)):
                    for opt1 in options_by_event[i]:
                        for opt2 in options_by_event[j]:
                            all_combinations.append([opt1, opt2])

        if max_changes >= 3:
            for i in range(len(options_by_event)):
                for j in range(i + 1, len(options_by_event)):
                    for k in range(j + 1, len(options_by_event)):
                        for opt1 in options_by_event[i]:
                            for opt2 in options_by_event[j]:
                                for opt3 in options_by_event[k]:
                                    all_combinations.append([opt1, opt2, opt3])

        return all_combinations
