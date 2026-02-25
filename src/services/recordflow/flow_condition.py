"""
FlowCondition class for handling conditional logic in record flows.

This module provides the FlowCondition class that represents a condition block
with associated actions in a flow definition.
"""

from typing import TYPE_CHECKING

from .flow_action import FlowAction
from .flow_result import ComparisonResult

if TYPE_CHECKING:
    from src.models import RecordRead


class FlowCondition:
    """
    Represents a condition block with associated actions.

    A condition can have multiple comparisons combined with logical operators
    and a list of actions to execute when the condition is true.
    """

    def __init__(self, condition: ComparisonResult | None, is_else: bool = False):
        self.condition = condition
        self.is_else = is_else
        self.actions: list[FlowAction] = []

    def add_condition(self, condition: ComparisonResult) -> None:
        """Add or combine a condition."""
        if self.condition is None:
            self.condition = condition
        else:
            # This would be handled by the FlowRecord's or_/and_ methods
            raise ValueError("Use or_() or and_() methods to combine conditions")

    def add_action(self, action: FlowAction) -> None:
        """Add an action to execute when this condition is true."""
        self.actions.append(action)

    def evaluate(self, record_context: dict[str, RecordRead]) -> bool:
        """Evaluate whether this condition is true given the record context."""
        if self.is_else:
            # Else conditions are handled specially by the engine
            return True

        if self.condition is None:
            return True  # No condition means always execute

        return self.condition.evaluate(record_context)

    def __repr__(self) -> str:
        if self.is_else:
            return f"FlowCondition(else, {len(self.actions)} actions)"
        return f"FlowCondition({self.condition}, {len(self.actions)} actions)"
