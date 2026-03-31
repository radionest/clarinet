"""Metaclass infrastructure for declarative exception classes.

Provides `_ExceptionMeta` — a metaclass that auto-generates `__init__` from
class annotations, similar to dataclasses. Combined with `@dataclass_transform`,
type checkers (mypy, pyright) see fields as `__init__` parameters with full
autocomplete and type safety.

Message resolution priority:
    1. ``format_message()`` method — for conditional/custom logic
    2. ``message_template`` ClassVar — auto-formatted from field values
    3. ``message`` field — direct message string (default fallback)
"""

import typing
from typing import Any, ClassVar, dataclass_transform

_MISSING = object()


def _is_classvar(annotation: object) -> bool:
    if isinstance(annotation, str):
        return "ClassVar" in annotation
    if annotation is ClassVar:
        return True
    return getattr(annotation, "__origin__", None) is ClassVar


def _collect_fields(cls: type) -> list[tuple[str, Any, object]]:
    """Collect fields from class annotations, walking MRO base-first."""
    fields: dict[str, tuple[str, Any, object]] = {}

    for base in reversed(cls.__mro__):
        if base is object or base is Exception:
            continue

        # Python 3.14+: annotations are lazily evaluated via a descriptor
        # on type, not stored directly in __dict__. Access via getattr
        # triggers the descriptor and returns own-only annotations.
        annotations: dict[str, Any] = getattr(base, "__annotations__", {})
        for name, type_hint in annotations.items():
            if _is_classvar(type_hint):
                continue

            if name in base.__dict__ and not callable(base.__dict__[name]):
                default = base.__dict__[name]
            elif name in fields:
                _, _, existing_default = fields[name]
                default = existing_default
            else:
                default = _MISSING

            fields[name] = (name, type_hint, default)

    return list(fields.values())


def _resolve_message(exc: Exception) -> str:
    """Resolve exception message by priority: format_message > template > message."""
    cls = type(exc)

    # Priority 1: format_message() method defined on the class hierarchy
    for klass in cls.__mro__:
        if klass is object or klass is Exception:
            break
        if "format_message" in klass.__dict__:
            return str(exc.format_message())  # type: ignore[attr-defined]

    # Priority 2: message_template ClassVar
    template = getattr(cls, "message_template", "")
    if template:
        field_values: dict[str, object] = {}
        for klass in reversed(cls.__mro__):
            if klass is object or klass is Exception:
                continue
            for name, ann in getattr(klass, "__annotations__", {}).items():
                if not _is_classvar(ann):
                    field_values[name] = getattr(exc, name, None)
        return typing.cast(str, template).format_map(field_values)

    # Priority 3: message field (fallback)
    return getattr(exc, "message", "")


def _make_init(
    fields: list[tuple[str, Any, object]],
) -> Any:
    """Generate __init__ via exec, like dataclasses."""
    params: list[str] = []
    defaults: dict[str, object] = {}

    for name, _type, default in fields:
        if default is not _MISSING:
            defaults[name] = default
            params.append(f"{name}=_defaults_['{name}']")
        else:
            params.append(name)

    assignments = "\n".join(f"  self.{name} = {name}" for name, _, _ in fields)
    if not assignments:
        assignments = "  pass"

    code = (
        f"def __init__(self, *, {', '.join(params)}):\n"
        f"{assignments}\n"
        f"  _exc_init_(self, _resolve_(self))\n"
    )

    ns: dict[str, object] = {
        "_defaults_": defaults,
        "_resolve_": _resolve_message,
        "_exc_init_": Exception.__init__,
    }
    exec(code, ns)
    return ns["__init__"]


@dataclass_transform(kw_only_default=True)
class _ExceptionMeta(type):
    """Metaclass that generates __init__ from annotations.

    Classes with an explicit ``__init__`` in their own ``__dict__`` are
    left untouched — the metaclass only acts on classes that rely on
    declarative field annotations.
    """

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, object],
        **kwargs: object,
    ) -> "_ExceptionMeta":
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)

        # Don't override explicitly defined __init__
        if "__init__" in namespace:
            return cls

        fields = _collect_fields(cls)  # type: ignore[arg-type]
        if fields:
            cls.__init__ = _make_init(fields)  # type: ignore[misc]

        return cls
