"""
FlowResult class for handling dynamic record data comparisons.

This module provides classes for building comparison expressions that are
evaluated lazily against actual record data at runtime.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.models import RecordRead


class ComparisonResult(ABC):
    """Abstract base for comparison results."""

    @abstractmethod
    def evaluate(self, record_context: dict[str, RecordRead]) -> bool:
        """Evaluate the comparison against actual record data."""
        pass


class FieldComparison(ComparisonResult):
    """Represents a comparison between record data fields."""

    def __init__(self, left: FlowResult, right: FlowResult, operator: str):
        self.left = left
        self.right = right
        self.operator = operator

    def evaluate(self, record_context: dict[str, RecordRead]) -> bool:
        """Evaluate the field comparison."""
        left_value = self.left._get_value(record_context)
        right_value = self.right._get_value(record_context)

        match self.operator:
            case "==":
                return bool(left_value == right_value)
            case "!=":
                return bool(left_value != right_value)
            case "<":
                return bool(left_value < right_value)
            case "<=":
                return bool(left_value <= right_value)
            case ">":
                return bool(left_value > right_value)
            case ">=":
                return bool(left_value >= right_value)
            case _:
                raise ValueError(f"Unknown operator: {self.operator}")

    def __repr__(self) -> str:
        return f"FieldComparison({self.left} {self.operator} {self.right})"


class LogicalComparison(ComparisonResult):
    """Represents logical operations between comparisons."""

    def __init__(self, left: ComparisonResult, right: ComparisonResult, operator: str):
        self.left = left
        self.right = right
        self.operator = operator

    def evaluate(self, record_context: dict[str, RecordRead]) -> bool:
        """Evaluate the logical comparison."""
        match self.operator:
            case "and":
                return self.left.evaluate(record_context) and self.right.evaluate(record_context)
            case "or":
                return self.left.evaluate(record_context) or self.right.evaluate(record_context)
            case _:
                raise ValueError(f"Unknown logical operator: {self.operator}")

    def __repr__(self) -> str:
        return f"LogicalComparison({self.left} {self.operator} {self.right})"


class FlowResult:
    """
    Represents a reference to a record data field that can be compared dynamically.

    This class overrides comparison operators to build comparison expressions
    that are evaluated later against actual record data.

    Example:
        record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R
    """

    def __init__(self, record_name: str, field_path: str | list[str] | None = None):
        self.record_name = record_name
        self.field_path: list[str] = []
        if field_path is not None:
            if isinstance(field_path, str):
                self.field_path = field_path.split(".")
            else:
                self.field_path = field_path

    def __getattr__(self, name: str) -> FlowResult:
        """Allow chaining attribute access for nested fields."""
        if name.startswith("_"):
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        return FlowResult(self.record_name, [*self.field_path, name])

    def _get_value(self, record_context: dict[str, RecordRead]) -> Any:
        """Get the actual value from record data."""
        if self.record_name not in record_context:
            raise ValueError(f"Record '{self.record_name}' not found in context")

        record = record_context[self.record_name]
        value = record.data

        # Navigate through the field path
        for field in self.field_path:
            if isinstance(value, dict):
                value = value.get(field)
            else:
                raise ValueError(f"Cannot access field '{field}' in non-dict value")

        return value

    # Comparison operators - return FieldComparison for lazy evaluation
    def __eq__(self, other: Any) -> FieldComparison:  # type: ignore[override]
        """Equal comparison."""
        if isinstance(other, FlowResult):
            return FieldComparison(self, other, "==")
        return FieldComparison(self, ConstantFlowResult(other), "==")

    def __ne__(self, other: Any) -> FieldComparison:  # type: ignore[override]
        """Not equal comparison."""
        if isinstance(other, FlowResult):
            return FieldComparison(self, other, "!=")
        return FieldComparison(self, ConstantFlowResult(other), "!=")

    def __lt__(self, other: Any) -> FieldComparison:
        """Less than comparison."""
        if isinstance(other, FlowResult):
            return FieldComparison(self, other, "<")
        return FieldComparison(self, ConstantFlowResult(other), "<")

    def __le__(self, other: Any) -> FieldComparison:
        """Less than or equal comparison."""
        if isinstance(other, FlowResult):
            return FieldComparison(self, other, "<=")
        return FieldComparison(self, ConstantFlowResult(other), "<=")

    def __gt__(self, other: Any) -> FieldComparison:
        """Greater than comparison."""
        if isinstance(other, FlowResult):
            return FieldComparison(self, other, ">")
        return FieldComparison(self, ConstantFlowResult(other), ">")

    def __ge__(self, other: Any) -> FieldComparison:
        """Greater than or equal comparison."""
        if isinstance(other, FlowResult):
            return FieldComparison(self, other, ">=")
        return FieldComparison(self, ConstantFlowResult(other), ">=")

    def __repr__(self) -> str:
        field_str = ".".join(self.field_path) if self.field_path else "data"
        return f"FlowResult({self.record_name}.{field_str})"


class ConstantFlowResult(FlowResult):
    """Represents a constant value in comparisons."""

    def __init__(self, value: Any):
        self.value = value
        super().__init__("", [])

    def _get_value(
        self,
        record_context: dict[str, RecordRead],  # noqa: ARG002 - required by interface
    ) -> Any:
        """Return the constant value."""
        return self.value

    def __repr__(self) -> str:
        return f"ConstantFlowResult({self.value!r})"
