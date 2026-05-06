"""
FlowResult class for handling dynamic record data comparisons.

This module provides classes for building comparison expressions that are
evaluated lazily against actual record data at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from clarinet.models import RecordRead


Strategy = Literal["single", "any", "all"]


class AmbiguousContextError(ValueError):
    """Raised when a single-strategy FlowResult finds multiple records in context.

    The flow author must disambiguate via ``.any()`` / ``.all()`` modifiers on
    ``record(...)`` (e.g. ``record('first-check').any().d.is_good == True``).
    """


def _apply_op(left: Any, right: Any, operator: str) -> bool:
    match operator:
        case "==":
            return bool(left == right)
        case "!=":
            return bool(left != right)
        case "<":
            return bool(left < right)
        case "<=":
            return bool(left <= right)
        case ">":
            return bool(left > right)
        case ">=":
            return bool(left >= right)
        case _:
            raise ValueError(f"Unknown operator: {operator}")


class ComparisonResult(ABC):
    """Abstract base for comparison results."""

    @abstractmethod
    def evaluate(self, record_context: dict[str, list[RecordRead]]) -> bool:
        """Evaluate the comparison against actual record data."""
        pass


class FieldComparison(ComparisonResult):
    """Represents a comparison between record data fields.

    Honours per-side strategy: a ``single`` side requires exactly one record
    in context (else :class:`AmbiguousContextError`); ``any`` is satisfied if
    any record matches; ``all`` requires every record to match. Both sides
    multi-valued at once is rejected — reduce one side to a constant or
    single record.
    """

    def __init__(self, left: FlowResult, right: FlowResult, operator: str):
        self.left = left
        self.right = right
        self.operator = operator

    def evaluate(self, record_context: dict[str, list[RecordRead]]) -> bool:
        left_strategy, left_values = self.left._resolve(record_context)
        right_strategy, right_values = self.right._resolve(record_context)

        if left_strategy == "single" and right_strategy == "single":
            if not left_values:
                raise ValueError(f"Record '{self.left.record_name}' not found in context")
            if not right_values:
                raise ValueError(f"Record '{self.right.record_name}' not found in context")
            return _apply_op(left_values[0], right_values[0], self.operator)

        if left_strategy != "single" and right_strategy != "single":
            raise NotImplementedError(
                f"Comparison between two multi-valued sides "
                f"({left_strategy} {self.operator} {right_strategy}) is not supported. "
                f"Reduce one side to a single record or constant."
            )

        # Exactly one side is multi-valued; the other is single.
        if left_strategy == "single":
            if not left_values:
                raise ValueError(f"Record '{self.left.record_name}' not found in context")
            single_value = left_values[0]
            multi_strategy = right_strategy
            multi_values = right_values

            def check(mv: Any) -> bool:
                return _apply_op(single_value, mv, self.operator)
        else:
            if not right_values:
                raise ValueError(f"Record '{self.right.record_name}' not found in context")
            single_value = right_values[0]
            multi_strategy = left_strategy
            multi_values = left_values

            def check(mv: Any) -> bool:
                return _apply_op(mv, single_value, self.operator)

        if not multi_values:
            return False

        if multi_strategy == "any":
            return any(check(v) for v in multi_values)
        return all(check(v) for v in multi_values)

    def __repr__(self) -> str:
        return f"FieldComparison({self.left} {self.operator} {self.right})"


class LogicalComparison(ComparisonResult):
    """Represents logical operations between comparisons."""

    def __init__(self, left: ComparisonResult, right: ComparisonResult, operator: str):
        self.left = left
        self.right = right
        self.operator = operator

    def evaluate(self, record_context: dict[str, list[RecordRead]]) -> bool:
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

    The ``strategy`` attribute controls how the (possibly multi-valued)
    context list for ``record_name`` is reduced during comparison:

    - ``"single"`` (default) — exactly one record expected; else
      :class:`AmbiguousContextError`.
    - ``"any"`` — comparison is True if any record in the list matches.
    - ``"all"`` — every record must match (empty list ⇒ False).

    Example:
        record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R
        record('first-check').any().d.is_good == True
    """

    def __init__(
        self,
        record_name: str,
        field_path: str | list[str] | None = None,
        *,
        strategy: Strategy = "single",
    ):
        self.record_name = record_name
        self.strategy: Strategy = strategy
        self.field_path: list[str] = []
        if field_path is not None:
            if isinstance(field_path, str):
                self.field_path = field_path.split(".")
            else:
                self.field_path = field_path

    @property
    def data(self) -> FlowResult:
        """Identity for chain symmetry with ``record(name).data.field``."""
        return self

    @property
    def d(self) -> FlowResult:
        """Shorthand for :attr:`data`."""
        return self

    def __getattr__(self, name: str) -> FlowResult:
        """Allow chaining attribute access for nested fields."""
        if name.startswith("_"):
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        return FlowResult(
            self.record_name,
            [*self.field_path, name],
            strategy=self.strategy,
        )

    def _extract_value(self, record: RecordRead) -> Any:
        """Walk ``field_path`` against a single record's ``data`` dict."""
        value = record.data
        for field in self.field_path:
            if isinstance(value, dict):
                value = value.get(field)
            else:
                raise ValueError(f"Cannot access field '{field}' in non-dict value")
        return value

    def _resolve(self, record_context: dict[str, list[RecordRead]]) -> tuple[Strategy, list[Any]]:
        """Return ``(strategy, values)`` for use by :class:`FieldComparison`.

        Empty list means the record type is absent from context. The caller
        decides whether that is a hard error (single) or a soft False (any/all).

        The runtime engine always passes ``list[RecordRead]`` per key. Unit
        tests and inspection helpers that synthesize a context dict by hand
        may pass a bare ``RecordRead`` — normalize defensively.
        """
        if self.record_name not in record_context:
            return self.strategy, []
        raw = record_context[self.record_name]
        records: list[RecordRead] = raw if isinstance(raw, list) else [raw]
        if self.strategy == "single" and len(records) > 1:
            raise AmbiguousContextError(
                f"Found {len(records)} records of type '{self.record_name}' in context, "
                f"strategy='single' is ambiguous — use record('{self.record_name}').any() "
                f"or .all() to disambiguate."
            )
        return self.strategy, [self._extract_value(r) for r in records]

    def _get_value(self, record_context: dict[str, list[RecordRead]]) -> Any:
        """Return a single value (legacy contract for tests/inspection)."""
        strategy, values = self._resolve(record_context)
        if not values:
            raise ValueError(f"Record '{self.record_name}' not found in context")
        if strategy != "single":
            raise ValueError(
                f"FlowResult.strategy='{strategy}' has no single value; "
                f"use a comparison operator instead of _get_value()."
            )
        return values[0]

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
        suffix = "" if self.strategy == "single" else f", {self.strategy}"
        return f"FlowResult({self.record_name}.{field_str}{suffix})"


class ConstantFlowResult(FlowResult):
    """Represents a constant value in comparisons."""

    def __init__(self, value: Any):
        super().__init__("", [], strategy="single")
        self.value = value

    def _resolve(
        self,
        record_context: dict[str, list[RecordRead]],  # noqa: ARG002
    ) -> tuple[Strategy, list[Any]]:
        return "single", [self.value]

    def _get_value(
        self,
        record_context: dict[str, list[RecordRead]],  # noqa: ARG002
    ) -> Any:
        """Return the constant value."""
        return self.value

    def __repr__(self) -> str:
        return f"ConstantFlowResult({self.value!r})"


_SELF = "__self__"


class Field(FlowResult):
    """Proxy for referencing the triggering record's own data fields.

    Unlike FlowResult which requires an explicit record type name,
    Field resolves against whichever record triggered the flow.
    Used with ``if_record()`` for concise self-referential conditions.

    Example:
        F = Field()
        record("first_check")
            .on_status("finished")
            .if_record(F.is_good == True, F.study_type == "CT")
            .add_record("segment_CT")
    """

    def __init__(self) -> None:
        super().__init__(_SELF)
