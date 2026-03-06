"""
FlowFileRecord class implementing the DSL for file-level flow triggers.

This module provides the FlowFileRecord class and the file() factory function
for defining workflows triggered by project-level file changes.

Example usage:
    file(master_model).on_update().invalidate_all_records("create_master_projection")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .flow_action import CallFunctionAction, InvalidateRecordsAction

if TYPE_CHECKING:
    from collections.abc import Callable

# Global registry for file flows
FILE_REGISTRY: list[FlowFileRecord] = []


class FlowFileRecord:
    """Represents a file-level trigger in a flow definition with DSL methods.

    This class provides a chainable API for defining workflows that trigger
    when a project-level file changes on disk (detected via checksum comparison).

    Example:
        file(master_model).on_update().invalidate_all_records("create_master_projection")
    """

    def __init__(self, file_name: str) -> None:
        self.file_name = file_name
        self.update_trigger: bool = False
        self.actions: list[InvalidateRecordsAction | CallFunctionAction] = []

    def on_update(self) -> FlowFileRecord:
        """Trigger this flow when the file changes on disk.

        Returns:
            Self for method chaining.
        """
        self.update_trigger = True
        return self

    def invalidate_all_records(
        self,
        *type_names: str,
        mode: str = "hard",
        callback: Callable[..., Any] | None = None,
    ) -> FlowFileRecord:
        """Add an invalidation action for records of specified types.

        Unlike record-level invalidation, this invalidates ALL matching records
        for a patient — there is no source record to skip.

        Args:
            type_names: Names of record types to invalidate.
            mode: "hard" resets status to pending.
                  "soft" only appends reason to context_info.
            callback: Optional callback function.

        Returns:
            Self for method chaining.
        """
        action = InvalidateRecordsAction(
            record_type_names=list(type_names),
            mode=mode,
            callback=callback,
        )
        self.actions.append(action)
        return self

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> FlowFileRecord:
        """Add a custom function call action.

        The function will be called with keyword arguments:
        - file_name: The name of the changed file
        - patient_id: The patient ID
        - client: The ClarinetClient instance

        Args:
            func: The function to call.
            *args: Positional arguments to pass to the function.
            **kwargs: Keyword arguments to pass to the function.

        Returns:
            Self for method chaining.
        """
        action = CallFunctionAction(
            function=func,
            args=args,
            extra_kwargs=dict(kwargs),
        )
        self.actions.append(action)
        return self

    def is_active_flow(self) -> bool:
        """Check if this FlowFileRecord defines an actual flow.

        Returns True if the flow has a trigger or actions.
        """
        return bool(self.update_trigger or self.actions)

    def validate(self) -> bool:
        """Validate the file flow definition.

        Returns:
            True if valid.

        Raises:
            ValueError: If the flow definition is invalid.
        """
        if not self.file_name:
            raise ValueError("FlowFileRecord must have a non-empty file_name")
        return True

    def __repr__(self) -> str:
        parts = [f"file('{self.file_name}')"]
        if self.update_trigger:
            parts.append(".on_update()")
        return "".join(parts)


def file(file_obj: Any) -> FlowFileRecord:
    """Create a new FlowFileRecord for a project-level file.

    Accepts any object with a ``.name`` attribute (e.g. ``File`` dataclass
    from project config or ``src.config.primitives.File``).

    Args:
        file_obj: Object with a ``.name`` attribute, or a plain string.

    Returns:
        A new FlowFileRecord instance for chaining DSL methods.

    Raises:
        ValueError: If the file name is empty.

    Example:
        file(master_model).on_update().invalidate_all_records("create_master_projection")
    """
    if isinstance(file_obj, str):
        name: str = file_obj
    else:
        raw_name = getattr(file_obj, "name", None)
        if raw_name is None:
            raise ValueError(
                f"file() expects an object with a .name attribute, got {type(file_obj).__name__}"
            )
        name = str(raw_name)

    if not name:
        raise ValueError("file() requires a non-empty file name")

    flow = FlowFileRecord(name)
    FILE_REGISTRY.append(flow)
    return flow
